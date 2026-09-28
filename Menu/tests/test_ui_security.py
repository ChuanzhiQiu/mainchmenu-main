"""
QA Adversarial / Red-Team de la UI multi-tenant, gestión de cajeros y POS.

Rama objetivo: `task/ui-tenant-cajeros-pos` (commit 7b76333).

Este módulo intenta deliberadamente vulnerar:
1. El control de acceso de las vistas administrativas de cajeros
   (`cajeros_admin_view`, `api_cajero_crear`, `api_cajero_toggle_activo`).
2. El aislamiento por fila (`restaurante_id`) en la gestión de cajeros.
3. La validación cruzada de mesas en `crear_orden`.
4. El spoofing de tenant durante la navegación administrativa.

Todos los tests expresan el COMPORTAMIENTO SEGURO esperado (302/403/404/400,
sin fugas de datos y sin mutaciones cross-tenant). Si alguno falla, documenta
una vulnerabilidad real.
"""

import json
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from Menu.models import (
    Area,
    Cajero,
    Mesa,
    Orden,
    PerfilAdministrador,
    Plato,
    Restaurante,
)


class UIPrivilegeEscalationTests(TestCase):
    """
    Intenta acceder a la gestión de cajeros como anónimo o como usuario de caja
    sin permisos de administrador. La mutación y la renderización deben quedar
    bloqueadas (302 -> login, o 403 para endpoints JSON/API).
    """

    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(
            nombre="Tenant UI Sec A", slug="tenant-ui-sec-a"
        )
        self.otro = Restaurante.objects.create(
            nombre="Tenant UI Sec B", slug="tenant-ui-sec-b"
        )
        self.admin = User.objects.create_superuser(
            username="ui_admin_sec", password="password123", email="uiadmin@test.cl"
        )
        self.no_admin = User.objects.create_user(
            username="ui_cajero_sec", password="password123"
        )
        self.cajero_victima = Cajero.objects.create(
            restaurante=self.tenant,
            nombre="Cajero Víctima",
            codigo_empleado="CAJ-VICT",
            pin_hash="hash",
            activo=True,
        )

    # ------------------------------------------------------------------ Anónimo
    def test_anonimo_get_cajeros_redirige_a_login(self):
        resp = self.client.get("/cajeros/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_anonimo_get_cajeros_canonical_redirige_a_login(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_anonimo_post_crear_cajero_devuelve_403_sin_mutacion(self):
        resp = self.client.post(
            "/api/cajeros/crear/",
            data=json.dumps({
                "nombre": "Cajero Anónimo",
                "codigo_empleado": "CAJ-ANON",
                "pin": "1234",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            Cajero.all_objects.filter(codigo_empleado="CAJ-ANON").exists()
        )

    def test_anonimo_post_toggle_cajero_devuelve_403_sin_mutacion(self):
        resp = self.client.post(
            "/api/cajeros/toggle-activo/",
            data=json.dumps({"cajero_id": self.cajero_victima.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.cajero_victima.refresh_from_db()
        self.assertTrue(self.cajero_victima.activo)

    # ----------------------------------------------------------- Usuario no admin
    def test_no_admin_get_cajeros_redirige_a_login(self):
        self.client.login(username="ui_cajero_sec", password="password123")
        resp = self.client.get("/cajeros/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_no_admin_get_cajeros_canonical_redirige_a_login(self):
        self.client.login(username="ui_cajero_sec", password="password123")
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_no_admin_post_crear_cajero_devuelve_403_sin_mutacion(self):
        self.client.login(username="ui_cajero_sec", password="password123")
        resp = self.client.post(
            "/api/cajeros/crear/",
            data=json.dumps({
                "nombre": "Cajero No Admin",
                "codigo_empleado": "CAJ-NOADMIN",
                "pin": "1234",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            Cajero.all_objects.filter(codigo_empleado="CAJ-NOADMIN").exists()
        )

    def test_no_admin_post_toggle_cajero_devuelve_403_sin_mutacion(self):
        self.client.login(username="ui_cajero_sec", password="password123")
        resp = self.client.post(
            "/api/cajeros/toggle-activo/",
            data=json.dumps({"cajero_id": self.cajero_victima.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.cajero_victima.refresh_from_db()
        self.assertTrue(self.cajero_victima.activo)


class CajeroAdminCrossTenantTests(TestCase):
    """
    Estando autenticado como Admin, intenta operar sobre cajeros de otro
    restaurante o forzar el `restaurante_id` en el payload.
    """

    def setUp(self):
        self.client = self.client_class()
        self.tenant_a = Restaurante.objects.create(
            nombre="Tenant Cajero Sec A", slug="tenant-cajero-sec-a"
        )
        self.tenant_b = Restaurante.objects.create(
            nombre="Tenant Cajero Sec B", slug="tenant-cajero-sec-b"
        )
        self.admin = User.objects.create_superuser(
            username="cajero_admin_sec", password="password123",
            email="cajeroadmin@test.cl",
        )
        self.client.login(username="cajero_admin_sec", password="password123")

        self.cajero_b = Cajero.objects.create(
            restaurante=self.tenant_b,
            nombre="Cajero B Víctima",
            codigo_empleado="CAJ-B-VICT",
            pin_hash="hash",
            activo=True,
        )

    def _crear_url(self, slug):
        return f"/r/{slug}/api/cajeros/crear/"

    def _toggle_url(self, slug):
        return f"/r/{slug}/api/cajeros/toggle-activo/"

    def test_toggle_cajero_de_otro_tenant_devuelve_404_sin_mutacion(self):
        resp = self.client.post(
            self._toggle_url(self.tenant_a.slug),
            data=json.dumps({"cajero_id": self.cajero_b.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.cajero_b.refresh_from_db()
        self.assertTrue(self.cajero_b.activo)

    def test_crear_cajero_inyectando_restaurante_de_otro_tenant_ignorado(self):
        resp = self.client.post(
            self._crear_url(self.tenant_a.slug),
            data=json.dumps({
                "nombre": "Cajero Spoof",
                "codigo_empleado": "CAJ-SPOOF",
                "pin": "1234",
                "restaurante": self.tenant_b.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        cajero = Cajero.all_objects.get(codigo_empleado="CAJ-SPOOF")
        self.assertEqual(cajero.restaurante_id, self.tenant_a.id)

    # ------------------------------------------------------ Controles positivos
    def test_toggle_cajero_propio_alterna_estado(self):
        cajero_a = Cajero.objects.create(
            restaurante=self.tenant_a,
            nombre="Cajero A Propio",
            codigo_empleado="CAJ-A-PROP",
            pin_hash="hash",
            activo=True,
        )
        resp = self.client.post(
            self._toggle_url(self.tenant_a.slug),
            data=json.dumps({"cajero_id": cajero_a.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        cajero_a.refresh_from_db()
        self.assertFalse(cajero_a.activo)

    def test_crear_cajero_propio_control(self):
        resp = self.client.post(
            self._crear_url(self.tenant_a.slug),
            data=json.dumps({
                "nombre": "Cajero Control",
                "codigo_empleado": "CAJ-CONTROL",
                "pin": "1234",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        cajero = Cajero.all_objects.get(codigo_empleado="CAJ-CONTROL")
        self.assertEqual(cajero.restaurante_id, self.tenant_a.id)


class MesaCrossTenantOrderCreationTests(TestCase):
    """
    Intenta crear una orden en el Restaurante A usando una mesa del Restaurante B.
    Debe rechazar la orden y no asociar nunca la mesa ajena.
    """

    def setUp(self):
        self.client = self.client_class()
        self.tenant_a = Restaurante.objects.create(
            nombre="Tenant Mesa Sec A", slug="tenant-mesa-sec-a"
        )
        self.tenant_b = Restaurante.objects.create(
            nombre="Tenant Mesa Sec B", slug="tenant-mesa-sec-b"
        )
        self.admin = User.objects.create_superuser(
            username="mesa_admin_sec", password="password123",
            email="mesaadmin@test.cl",
        )
        self.client.login(username="mesa_admin_sec", password="password123")

        self.plato_a = Plato.objects.create(
            restaurante=self.tenant_a, nombre="Plato A Sec", valor=Decimal("5000.00")
        )
        self.area_b = Area.objects.create(restaurante=self.tenant_b, nombre="Salón B")
        self.mesa_b = Mesa.objects.create(
            restaurante=self.tenant_b, area=self.area_b, numero="B-SEC"
        )

    def test_crear_orden_con_mesa_de_otro_tenant_rechazada(self):
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=json.dumps({
                "cliente": "Cliente Cross Mesa",
                "items": [{"tipo": "plato", "id": self.plato_a.id, "cantidad": 1}],
                "mesa_id": self.mesa_b.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mesa", resp.json()["message"].lower())
        self.assertFalse(
            Orden.all_objects.filter(cliente="Cliente Cross Mesa").exists()
        )
        self.assertFalse(
            Orden.all_objects.filter(mesa_id=self.mesa_b.id).exists()
        )

    def test_crear_orden_con_mesa_propia_control(self):
        area_a = Area.objects.create(restaurante=self.tenant_a, nombre="Salón A")
        mesa_a = Mesa.objects.create(
            restaurante=self.tenant_a, area=area_a, numero="A-SEC"
        )

        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=json.dumps({
                "cliente": "Cliente Mesa Propia",
                "items": [{"tipo": "plato", "id": self.plato_a.id, "cantidad": 1}],
                "mesa_id": mesa_a.id,
                "tipo_servicio": "salon",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        orden = Orden.all_objects.get(id=resp.json()["orden_id"])
        self.assertEqual(orden.mesa_id, mesa_a.id)
        self.assertEqual(orden.tipo_servicio, "salon")


class TenantNavigationSpoofingTests(TestCase):
    """
    Un Admin vinculado de forma unívoca al Restaurante A intenta navegar
    manualmente al slug del Restaurante B. El middleware de tenancy estricta
    debe responder 403 Forbidden (o 404) sin filtrar ni mezclar datos.
    """

    def setUp(self):
        self.client = self.client_class()
        self.tenant_a = Restaurante.objects.create(
            nombre="Tenant Nav Sec A", slug="tenant-nav-sec-a"
        )
        self.tenant_b = Restaurante.objects.create(
            nombre="Tenant Nav Sec B", slug="tenant-nav-sec-b"
        )
        self.admin = User.objects.create_superuser(
            username="nav_admin_sec", password="password123",
            email="navadmin@test.cl",
        )
        # Vinculación unívoca Admin -> Restaurante A
        PerfilAdministrador.objects.create(user=self.admin, restaurante=self.tenant_a)
        self.client.login(username="nav_admin_sec", password="password123")

        self.cajero_a = Cajero.objects.create(
            restaurante=self.tenant_a,
            nombre="Cajero A Sec",
            codigo_empleado="CAJ-A-SEC",
            pin_hash="hash",
        )
        self.cajero_b = Cajero.objects.create(
            restaurante=self.tenant_b,
            nombre="Cajero B Sec",
            codigo_empleado="CAJ-B-SEC",
            pin_hash="hash",
        )

    def test_admin_a_no_puede_navegar_a_slug_b(self):
        resp = self.client.get(f"/r/{self.tenant_b.slug}/cajeros/")
        self.assertIn(resp.status_code, (403, 404))

    def test_admin_a_sigue_accediendo_a_su_propio_tenant(self):
        resp = self.client.get(f"/r/{self.tenant_a.slug}/cajeros/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.cajero_a.nombre)
        self.assertNotContains(resp, self.cajero_b.nombre)

    def test_navegacion_cruzada_no_envenena_sesion_al_volver_a_a(self):
        # Primero el Admin intenta el slug de B (bloqueado)...
        resp_b = self.client.get(f"/r/{self.tenant_b.slug}/cajeros/")
        self.assertIn(resp_b.status_code, (403, 404))

        # ...luego regresa a su tenant A y sigue operando con normalidad.
        resp_a = self.client.get(f"/r/{self.tenant_a.slug}/cajeros/")
        self.assertEqual(resp_a.status_code, 200)
        self.assertContains(resp_a, self.cajero_a.nombre)
        self.assertNotContains(resp_a, self.cajero_b.nombre)
