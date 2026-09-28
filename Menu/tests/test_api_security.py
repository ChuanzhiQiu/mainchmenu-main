"""
QA Adversarial / Red-Team de seguridad multi-tenant (rama task/api-drf-endpoints).

Este módulo intenta deliberadamente vulnerar el aislamiento por fila (`restaurante_id`)
simulando ataques entre dos tenants independientes (Restaurante A y Restaurante B).

Categorías cubiertas:
1. Cross-Tenant FK Injection (Mesa -> Area, PUT/PATCH de reasignación).
2. IDOR (acceso directo por ID a objetos de otro tenant).
3. Ataques financieros vía `registrar-pago` (orden cross-tenant, turno cerrado,
   cajero de otro restaurante, montos negativos/cero/corruptos).
4. Spoofing de identidad de tenant (campo `restaurante` forzado, header, query params).

Todos los tests expresan el COMPORTAMIENTO SEGURO esperado (404/403/400, sin fuga
de datos y sin mutaciones). Si alguno falla, documenta una vulnerabilidad real.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from Menu.models import Area, Cajero, Categoria, Mesa, Orden, Pago, Restaurante, TurnoCaja


class AdversarialMultiTenantSecurityTests(APITestCase):
    """Simula ataques deliberados entre Restaurante A y Restaurante B."""

    def setUp(self):
        self.tenant_a = Restaurante.objects.create(nombre="Tenant Sec A", slug="tenant-sec-a")
        self.tenant_b = Restaurante.objects.create(nombre="Tenant Sec B", slug="tenant-sec-b")

        # --- Áreas ---
        self.area_a = Area.objects.create(restaurante=self.tenant_a, nombre="Salón A")
        self.area_b = Area.objects.create(restaurante=self.tenant_b, nombre="Salón B")

        # --- Mesas ---
        self.mesa_a = Mesa.objects.create(
            restaurante=self.tenant_a, area=self.area_a, numero="A1"
        )
        self.mesa_b = Mesa.objects.create(
            restaurante=self.tenant_b, area=self.area_b, numero="B1"
        )

        # --- Categorías ---
        self.categoria_a = Categoria.objects.create(restaurante=self.tenant_a, nombre="Cat A")
        self.categoria_b = Categoria.objects.create(restaurante=self.tenant_b, nombre="Cat B")

        # --- Cajeros ---
        self.cajero_a = Cajero.objects.create(
            restaurante=self.tenant_a,
            nombre="Cajero A",
            codigo_empleado="CAJ-SEC-A",
            pin_hash="hash-a",
        )
        self.cajero_b = Cajero.objects.create(
            restaurante=self.tenant_b,
            nombre="Cajero B",
            codigo_empleado="CAJ-SEC-B",
            pin_hash="hash-b",
        )

        # --- Turnos de caja ---
        self.turno_a_abierto = TurnoCaja.objects.create(
            restaurante=self.tenant_a,
            cajero=self.cajero_a,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )
        self.turno_a_cerrado = TurnoCaja.objects.create(
            restaurante=self.tenant_a,
            cajero=self.cajero_a,
            estado=TurnoCaja.ESTADO_CERRADO,
        )
        self.turno_b_abierto = TurnoCaja.objects.create(
            restaurante=self.tenant_b,
            cajero=self.cajero_b,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )

        # --- Órdenes ---
        self.orden_a = Orden.objects.create(
            restaurante=self.tenant_a,
            cliente="Cliente A",
            monto_total=Decimal("20000.00"),
            mesa=self.mesa_a,
        )
        self.orden_b = Orden.objects.create(
            restaurante=self.tenant_b,
            cliente="Cliente B",
            monto_total=Decimal("30000.00"),
            mesa=self.mesa_b,
        )

    # ------------------------------------------------------------------ Helpers
    def _url(self, path, tenant=None):
        tenant = tenant or self.tenant_a
        return f"/r/{tenant.slug}/api/{path}"

    def _set_cajero_session(self, cajero, turno):
        session = self.client.session
        session["cajero_id"] = cajero.id
        session["turno_id"] = turno.id
        session.save()

    def _pagos_de(self, orden):
        return Pago.all_objects.filter(orden=orden)

    # ========================================================================
    # 1. ATAQUES DE INYECCIÓN DE CLAVES FORÁNEAS (Cross-Tenant FK Injection)
    # ========================================================================
    def test_create_mesa_con_area_de_otro_tenant_rechazada(self):
        resp = self.client.post(
            self._url("mesas/"),
            {"area": self.area_b.id, "numero": "X-INTENTO"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(Mesa.all_objects.filter(numero="X-INTENTO").exists())

    def test_patch_mesa_reasignar_area_de_otro_tenant_rechazada(self):
        resp = self.client.patch(
            self._url(f"mesas/{self.mesa_a.id}/"),
            {"area": self.area_b.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.mesa_a.refresh_from_db()
        self.assertEqual(self.mesa_a.area_id, self.area_a.id)

    def test_put_mesa_reasignar_area_de_otro_tenant_rechazada(self):
        resp = self.client.put(
            self._url(f"mesas/{self.mesa_a.id}/"),
            {
                "area": self.area_b.id,
                "numero": "A1",
                "capacidad": 4,
                "estado": Mesa.ESTADO_LIBRE,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.mesa_a.refresh_from_db()
        self.assertEqual(self.mesa_a.area_id, self.area_a.id)

    # ========================================================================
    # 2. ATAQUES DE ACCESO DIRECTO POR OBJETO (IDOR)
    # ========================================================================
    def test_get_mesa_de_otro_tenant_devuelve_404(self):
        resp = self.client.get(self._url(f"mesas/{self.mesa_b.id}/"))
        self.assertEqual(resp.status_code, 404)

    def test_patch_mesa_de_otro_tenant_devuelve_404_sin_mutacion(self):
        resp = self.client.patch(
            self._url(f"mesas/{self.mesa_b.id}/"),
            {"numero": "HACKEADA"},
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        self.mesa_b.refresh_from_db()
        self.assertEqual(self.mesa_b.numero, "B1")

    def test_delete_mesa_de_otro_tenant_devuelve_404_sin_eliminar(self):
        resp = self.client.delete(self._url(f"mesas/{self.mesa_b.id}/"))

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Mesa.all_objects.filter(id=self.mesa_b.id).exists())

    def test_get_area_de_otro_tenant_devuelve_404(self):
        resp = self.client.get(self._url(f"areas/{self.area_b.id}/"))
        self.assertEqual(resp.status_code, 404)

    def test_patch_area_de_otro_tenant_devuelve_404_sin_mutacion(self):
        resp = self.client.patch(
            self._url(f"areas/{self.area_b.id}/"),
            {"nombre": "HACKEADA"},
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        self.area_b.refresh_from_db()
        self.assertEqual(self.area_b.nombre, "Salón B")

    def test_delete_area_de_otro_tenant_devuelve_404_sin_eliminar(self):
        resp = self.client.delete(self._url(f"areas/{self.area_b.id}/"))

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Area.all_objects.filter(id=self.area_b.id).exists())

    def test_get_categoria_de_otro_tenant_devuelve_404(self):
        resp = self.client.get(self._url(f"categorias/{self.categoria_b.id}/"))
        self.assertEqual(resp.status_code, 404)

    def test_delete_categoria_de_otro_tenant_devuelve_404_sin_eliminar(self):
        resp = self.client.delete(self._url(f"categorias/{self.categoria_b.id}/"))

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Categoria.all_objects.filter(id=self.categoria_b.id).exists())

    def test_get_orden_de_otro_tenant_devuelve_404(self):
        resp = self.client.get(self._url(f"ordenes/{self.orden_b.id}/"))
        self.assertEqual(resp.status_code, 404)

    def test_lista_mesas_no_expone_datos_de_otro_tenant(self):
        resp = self.client.get(self._url("mesas/"))

        self.assertEqual(resp.status_code, 200)
        ids = [m["id"] for m in resp.json()]
        self.assertIn(self.mesa_a.id, ids)
        self.assertNotIn(self.mesa_b.id, ids)

    def test_lista_ordenes_no_expone_datos_de_otro_tenant(self):
        resp = self.client.get(self._url("ordenes/"))

        self.assertEqual(resp.status_code, 200)
        ids = [o["id"] for o in resp.json()]
        self.assertIn(self.orden_a.id, ids)
        self.assertNotIn(self.orden_b.id, ids)

    # ========================================================================
    # 3. ATAQUES FINANCIEROS Y DE COBRO (registrar-pago)
    # ========================================================================
    def test_registrar_pago_valido_con_turno_abierto_control(self):
        """Control positivo: verifica que el arnés de la suite funciona."""
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "20000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        self.orden_a.refresh_from_db()
        self.assertEqual(self.orden_a.estado, Orden.ESTADO_COMPLETADA)
        pago = self._pagos_de(self.orden_a).get()
        self.assertEqual(pago.cajero_id, self.cajero_a.id)
        self.assertEqual(pago.turno_id, self.turno_a_abierto.id)

    def test_pagar_orden_de_otro_tenant_devuelve_404(self):
        """Cajero/turno de A intenta cobrar una orden perteneciente a B."""
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_b.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "30000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        self.orden_b.refresh_from_db()
        self.assertEqual(self.orden_b.estado, Orden.ESTADO_EN_CURSO)
        self.assertFalse(self._pagos_de(self.orden_b).exists())

    def test_pago_con_turno_cerrado_rechazado(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_cerrado)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "20000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.orden_a.refresh_from_db()
        self.assertEqual(self.orden_a.estado, Orden.ESTADO_EN_CURSO)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_con_cajero_de_otro_tenant_rechazado(self):
        """Sesión spoofeada con cajero/turno del Restaurante B, cobrando orden de A."""
        self._set_cajero_session(self.cajero_b, self.turno_b_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "20000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.orden_a.refresh_from_db()
        self.assertEqual(self.orden_a.estado, Orden.ESTADO_EN_CURSO)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_sin_turno_abierto_rechazado(self):
        """Sin cajero/turno en sesión no debe poderse cobrar (regla AGENTS.md)."""
        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "20000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.orden_a.refresh_from_db()
        self.assertEqual(self.orden_a.estado, Orden.ESTADO_EN_CURSO)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_monto_negativo_rechazado(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "-100.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_monto_cero_rechazado(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "0.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_monto_no_numerico_rechazado(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "abc"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_monto_null_rechazado(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": None},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_monto_estructura_corrupta_rechazado(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": {"payload": "malicioso"}},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    def test_pago_propina_negativa_rechazada(self):
        self._set_cajero_session(self.cajero_a, self.turno_a_abierto)

        resp = self.client.post(
            self._url(f"ordenes/{self.orden_a.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "20000.00", "propina": "-999.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(self._pagos_de(self.orden_a).exists())

    # ========================================================================
    # 4. SPOOFING DE IDENTIDAD DE TENANT
    # ========================================================================
    def test_spoofing_restaurante_en_create_area_ignorado(self):
        resp = self.client.post(
            self._url("areas/"),
            {"nombre": "Terraza", "restaurante": self.tenant_b.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        area = Area.all_objects.get(nombre="Terraza")
        self.assertEqual(area.restaurante_id, self.tenant_a.id)

    def test_spoofing_restaurante_en_create_mesa_ignorado(self):
        resp = self.client.post(
            self._url("mesas/"),
            {
                "area": self.area_a.id,
                "numero": "ZZ-SPOOF",
                "restaurante": self.tenant_b.id,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        mesa = Mesa.all_objects.get(numero="ZZ-SPOOF")
        self.assertEqual(mesa.restaurante_id, self.tenant_a.id)

    def test_spoofing_restaurante_en_create_categoria_ignorado(self):
        resp = self.client.post(
            self._url("categorias/"),
            {"nombre": "Postres Spoof", "restaurante": self.tenant_b.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        categoria = Categoria.all_objects.get(nombre="Postres Spoof")
        self.assertEqual(categoria.restaurante_id, self.tenant_a.id)

    def test_spoofing_restaurante_en_patch_area_ignorado(self):
        resp = self.client.patch(
            self._url(f"areas/{self.area_a.id}/"),
            {"nombre": "Renombrada", "restaurante": self.tenant_b.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.area_a.refresh_from_db()
        self.assertEqual(self.area_a.nombre, "Renombrada")
        self.assertEqual(self.area_a.restaurante_id, self.tenant_a.id)

    def test_spoofing_header_tenant_ignorado_ante_url_canonica(self):
        """El header X-Tenant-Slug no debe ganarle al slug canónico de la URL."""
        resp = self.client.get(
            self._url("areas/"),
            HTTP_X_TENANT_SLUG=self.tenant_b.slug,
        )

        self.assertEqual(resp.status_code, 200)
        nombres = [a["nombre"] for a in resp.json()]
        self.assertIn(self.area_a.nombre, nombres)
        self.assertNotIn(self.area_b.nombre, nombres)

    def test_spoofing_query_param_restaurante_ignorado(self):
        resp = self.client.get(self._url("areas/") + f"?restaurante={self.tenant_b.id}")

        self.assertEqual(resp.status_code, 200)
        nombres = [a["nombre"] for a in resp.json()]
        self.assertIn(self.area_a.nombre, nombres)
        self.assertNotIn(self.area_b.nombre, nombres)
