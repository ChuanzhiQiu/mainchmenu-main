"""
Pruebas de los ViewSets/endpoints DRF multi-tenant (Sección 7 AGENTS.md).

Cubre:
- Aislamiento de listado por tenant (get_queryset filtrado).
- Inyección segura de `restaurante` en perform_create (ignorando payload).
- Validación cross-tenant de FKs (Mesa.area).
- Endpoint @action registrar-pago conectado a pago_service (éxito, 400 y 404).
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from Menu.models import Area, Cajero, Categoria, Mesa, Orden, Restaurante, TurnoCaja


class DrfApiEndpointsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Restaurante.objects.create(nombre="Tenant API A", slug="tenant-api-a")
        self.otro = Restaurante.objects.create(nombre="Tenant API B", slug="tenant-api-b")

    def _url(self, path, slug=None):
        slug = slug or self.tenant.slug
        return f"/r/{slug}/api/{path}"

    def _crear_cajero_turno(self, restaurante, codigo="CAJ-API"):
        cajero = Cajero.objects.create(
            restaurante=restaurante,
            nombre=f"Cajero {codigo}",
            codigo_empleado=codigo,
            pin_hash=f"hash-{codigo}",
        )
        turno = TurnoCaja.objects.create(
            restaurante=restaurante,
            cajero=cajero,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )
        return cajero, turno

    def _crear_orden_con_mesa(self, restaurante, monto="20000.00"):
        area = Area.objects.create(restaurante=restaurante, nombre="Comedor")
        mesa = Mesa.objects.create(
            restaurante=restaurante,
            area=area,
            numero="1",
            estado=Mesa.ESTADO_OCUPADA,
        )
        orden = Orden.objects.create(
            restaurante=restaurante,
            cliente="Cliente API",
            monto_total=Decimal(monto),
            mesa=mesa,
        )
        return orden

    # ------------------------------------------------------------------ Áreas
    def test_lista_areas_aisladas_por_tenant(self):
        Area.objects.create(restaurante=self.tenant, nombre="Comedor A")
        Area.objects.create(restaurante=self.otro, nombre="Comedor B")

        resp = self.client.get(self._url("areas/"))

        self.assertEqual(resp.status_code, 200)
        nombres = [a["nombre"] for a in resp.json()]
        self.assertIn("Comedor A", nombres)
        self.assertNotIn("Comedor B", nombres)

    def test_create_area_inyecta_tenant_e_ignora_payload(self):
        resp = self.client.post(
            self._url("areas/"),
            {"nombre": "Terraza", "restaurante": self.otro.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        area = Area.all_objects.get(nombre="Terraza")
        self.assertEqual(area.restaurante_id, self.tenant.id)

    # ------------------------------------------------------------------ Mesas
    def test_create_mesa_con_area_de_otro_tenant_rechazada(self):
        area_otro = Area.objects.create(restaurante=self.otro, nombre="Barra B")

        resp = self.client.post(
            self._url("mesas/"),
            {"area": area_otro.id, "numero": "5"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_create_mesa_valida_con_area_del_mismo_tenant(self):
        area = Area.objects.create(restaurante=self.tenant, nombre="Salón")

        resp = self.client.post(
            self._url("mesas/"),
            {"area": area.id, "numero": "7", "capacidad": 6},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        mesa = Mesa.all_objects.get(numero="7")
        self.assertEqual(mesa.restaurante_id, self.tenant.id)
        self.assertEqual(mesa.area_id, area.id)

    # ------------------------------------------------------------ Categorías
    def test_create_categoria_inyecta_tenant(self):
        resp = self.client.post(
            self._url("categorias/"),
            {"nombre": "Entradas", "orden": 1},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        categoria = Categoria.all_objects.get(nombre="Entradas")
        self.assertEqual(categoria.restaurante_id, self.tenant.id)

    # ------------------------------------------------------- Pago (registrar)
    def test_registrar_pago_exitoso(self):
        cajero, turno = self._crear_cajero_turno(self.tenant)
        orden = self._crear_orden_con_mesa(self.tenant)

        session = self.client.session
        session["cajero_id"] = cajero.id
        session["turno_id"] = turno.id
        session.save()

        resp = self.client.post(
            self._url(f"ordenes/{orden.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "20000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["orden_completada"])
        self.assertEqual(data["saldo_pendiente"], "0.00")

        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_COMPLETADA)

    def test_registrar_pago_orden_ya_pagada_devuelve_400(self):
        cajero, turno = self._crear_cajero_turno(self.tenant)
        orden = self._crear_orden_con_mesa(self.tenant)

        session = self.client.session
        session["cajero_id"] = cajero.id
        session["turno_id"] = turno.id
        session.save()

        url = self._url(f"ordenes/{orden.id}/registrar-pago/")
        payload = {"metodo_pago": "Efectivo", "monto": "20000.00"}

        first = self.client.post(url, payload, format="json")
        self.assertEqual(first.status_code, 201, first.content)

        second = self.client.post(url, {"metodo_pago": "Efectivo", "monto": "1.00"}, format="json")
        self.assertEqual(second.status_code, 400)
        self.assertFalse(second.json()["success"])

    def test_registrar_pago_orden_de_otro_tenant_devuelve_404(self):
        orden = self._crear_orden_con_mesa(self.tenant)

        resp = self.client.post(
            self._url(f"ordenes/{orden.id}/registrar-pago/", slug=self.otro.slug),
            {"metodo_pago": "Efectivo", "monto": "20000.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 404)

    def test_registrar_pago_monto_invalido_devuelve_400(self):
        cajero, turno = self._crear_cajero_turno(self.tenant)
        orden = self._crear_orden_con_mesa(self.tenant)

        session = self.client.session
        session["cajero_id"] = cajero.id
        session["turno_id"] = turno.id
        session.save()

        resp = self.client.post(
            self._url(f"ordenes/{orden.id}/registrar-pago/"),
            {"metodo_pago": "Efectivo", "monto": "abc"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
