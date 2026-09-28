"""
Pruebas de integración para la UI multi-tenant, gestión de cajeros y conexión POS.

Cubre:
- La barra de navegación resuelve el tenant activo (badge informativo, sin switch).
- La vista administrativa de cajeros renderiza solo cajeros del tenant activo.
- Crear/activar/bloquear cajeros está estrictamente aislado por `restaurante_id`.
- La vista POS renderiza los selectores de Áreas/Mesas y la modal de cobro.
- `crear_orden` rechaza mesas cross-tenant.
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from Menu.models import (
    Area,
    Cajero,
    Mesa,
    Plato,
    Restaurante,
)


class TenantNavbarContextTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(nombre="Tenant Nav A", slug="tenant-nav-a")
        self.otro = Restaurante.objects.create(nombre="Tenant Nav B", slug="tenant-nav-b")
        self.admin = User.objects.create_superuser(
            username="nav_admin", password="password123", email="nav@test.cl"
        )

    def test_navbar_muestra_tenant_activo(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.tenant.nombre)
        self.assertContains(resp, f"/{self.tenant.slug}")

    def test_no_existe_switch_de_restaurantes(self):
        """La UI no debe exponer un selector conmutable de restaurantes."""
        self.client.login(username="nav_admin", password="password123")
        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "tenant-switcher")
        self.assertNotContains(resp, "restaurantes_disponibles")
        # El badge informativo del restaurante activo sí está presente.
        self.assertContains(resp, self.tenant.nombre)


class CajeroAdminViewTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(nombre="Tenant Caja A", slug="tenant-caja-a")
        self.otro = Restaurante.objects.create(nombre="Tenant Caja B", slug="tenant-caja-b")
        self.admin = User.objects.create_superuser(
            username="caja_admin", password="password123", email="caja@test.cl"
        )
        self.cajero_a = Cajero.objects.create(
            restaurante=self.tenant,
            nombre="Cajero A",
            codigo_empleado="CAJ-A",
            pin_hash="hash-a",
        )
        self.cajero_b = Cajero.objects.create(
            restaurante=self.otro,
            nombre="Cajero B",
            codigo_empleado="CAJ-B",
            pin_hash="hash-b",
        )

    def test_admin_ve_listado_de_cajeros_del_tenant(self):
        self.client.login(username="caja_admin", password="password123")
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cajero A")
        self.assertNotContains(resp, "Cajero B")

    def test_cajero_no_staff_redirige_a_login(self):
        cajero_user = User.objects.create_user(username="cajero_no_admin", password="password123")
        self.client.login(username="cajero_no_admin", password="password123")
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)


class CajeroAdminApiTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(nombre="Tenant API Caja A", slug="tenant-api-caja-a")
        self.otro = Restaurante.objects.create(nombre="Tenant API Caja B", slug="tenant-api-caja-b")
        self.admin = User.objects.create_superuser(
            username="caja_api_admin", password="password123", email="cajaapi@test.cl"
        )
        self.client.login(username="caja_api_admin", password="password123")

    def _crear_url(self, slug):
        return f"/r/{slug}/api/cajeros/crear/"

    def _toggle_url(self, slug):
        return f"/r/{slug}/api/cajeros/toggle-activo/"

    def test_crear_cajero_con_pin_hash_y_tenant_inyectado(self):
        resp = self.client.post(
            self._crear_url(self.tenant.slug),
            data=json.dumps({"nombre": "Nuevo Cajero", "codigo_empleado": "CAJ-9", "pin": "1234", "activo": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])

        cajero = Cajero.all_objects.get(codigo_empleado="CAJ-9")
        self.assertEqual(cajero.restaurante_id, self.tenant.id)
        self.assertNotEqual(cajero.pin_hash, "1234")  # nunca en texto plano
        self.assertTrue(cajero.check_pin("1234"))

    def test_crear_cajero_pin_invalido_rechazado(self):
        resp = self.client.post(
            self._crear_url(self.tenant.slug),
            data=json.dumps({"nombre": "PIN Malo", "codigo_empleado": "CAJ-BAD", "pin": "12"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_crear_cajero_nunca_usa_restaurante_del_payload(self):
        # El payload intenta forzar el tenant B, pero debe crearse en el tenant de la URL.
        resp = self.client.post(
            self._crear_url(self.tenant.slug),
            data=json.dumps({
                "nombre": "Cajero Cross",
                "codigo_empleado": "CAJ-X",
                "pin": "4321",
                "restaurante": self.otro.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        cajero = Cajero.all_objects.get(codigo_empleado="CAJ-X")
        self.assertEqual(cajero.restaurante_id, self.tenant.id)

    def test_toggle_cajero_cross_tenant_rechazado(self):
        cajero = Cajero.objects.create(
            restaurante=self.otro,
            nombre="Cajero Otro",
            codigo_empleado="CAJ-OTRO",
            pin_hash="hash",
        )
        resp = self.client.post(
            self._toggle_url(self.tenant.slug),
            data=json.dumps({"cajero_id": cajero.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_toggle_cajero_propio_alterna_estado(self):
        cajero = Cajero.objects.create(
            restaurante=self.tenant,
            nombre="Cajero Propio",
            codigo_empleado="CAJ-PROP",
            pin_hash="hash",
        )
        resp = self.client.post(
            self._toggle_url(self.tenant.slug),
            data=json.dumps({"cajero_id": cajero.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        cajero.refresh_from_db()
        self.assertFalse(cajero.activo)


class PosUiIntegrationTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(nombre="Tenant POS UI", slug="tenant-pos-ui")
        self.otro = Restaurante.objects.create(nombre="Tenant POS Otro", slug="tenant-pos-otro")
        self.admin = User.objects.create_superuser(
            username="pos_admin", password="password123", email="pos@test.cl"
        )
        self.client.login(username="pos_admin", password="password123")

    def test_pos_renderiza_selectores_de_area_mesa_y_modal_cobro(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="select-area"')
        self.assertContains(resp, 'id="select-mesa"')
        self.assertContains(resp, 'id="modal-cobro"')
        self.assertContains(resp, "Cobrar Orden")

    def test_pos_renderiza_aviso_de_turno_y_soporte_teclado_fisico_pin(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        # Aviso de turno requerido para generar comandas.
        self.assertContains(resp, 'id="turno-requerido-banner"')
        self.assertContains(resp, "Debe abrir un turno de caja para generar comandas")
        # Soporte de teclado físico en la pantalla de bloqueo de PIN.
        self.assertContains(resp, "addEventListener('keydown'")
        self.assertContains(resp, 'pantalla-bloqueo-overlay')

    def test_crear_orden_rechaza_mesa_de_otro_tenant(self):
        plato = Plato.objects.create(restaurante=self.tenant, nombre="Plato POS", valor=Decimal("5000.00"))
        mesa_otro = Mesa.objects.create(
            restaurante=self.otro,
            area=Area.objects.create(restaurante=self.otro, nombre="Comedor B"),
            numero="10",
        )

        resp = self.client.post(
            f"/r/{self.tenant.slug}/pedidos/crear/",
            data=json.dumps({
                "cliente": "Cliente Mesa Cross",
                "items": [{"tipo": "plato", "id": plato.id, "cantidad": 1}],
                "mesa_id": mesa_otro.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mesa", resp.json()["message"].lower())

    def test_crear_orden_con_mesa_propia_asocia_mesa(self):
        area = Area.objects.create(restaurante=self.tenant, nombre="Salón")
        mesa = Mesa.objects.create(restaurante=self.tenant, area=area, numero="3")
        plato = Plato.objects.create(restaurante=self.tenant, nombre="Plato Salón", valor=Decimal("6000.00"))

        resp = self.client.post(
            f"/r/{self.tenant.slug}/pedidos/crear/",
            data=json.dumps({
                "cliente": "Mesa 3",
                "items": [{"tipo": "plato", "id": plato.id, "cantidad": 1}],
                "mesa_id": mesa.id,
                "tipo_servicio": "salon",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])
        from Menu.models import Orden
        orden = Orden.all_objects.get(id=data["orden_id"])
        self.assertEqual(orden.mesa_id, mesa.id)
        self.assertEqual(orden.tipo_servicio, "salon")
