"""
Pruebas del módulo de Salones y Mesas (Fase 3 — Módulo de Edición).

Cubre:
1. Pestañas de Edición y panel "Salones y Mesas".
2. CRUD de salones (Area) y mesas con aislamiento multi-tenant estricto.
3. Validación de código de mesa único dentro del restaurante.
4. Protección de endpoints para administrador (Nivel 2).
"""
import json

from django.test import TestCase
from django.contrib.auth.models import User

from Menu.models import Area, Mesa, Restaurante


class SalonesMesasTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(
            nombre="Tenant Salón A", slug="tenant-salon-a"
        )
        self.otro = Restaurante.objects.create(
            nombre="Tenant Salón B", slug="tenant-salon-b"
        )
        self.admin = User.objects.create_superuser(
            username="salon_admin", password="password123", email="salon@test.cl"
        )
        self.client.login(username="salon_admin", password="password123")

    def _url_salon_guardar(self, slug):
        return f"/r/{slug}/api/salon/guardar/"

    def _url_salon_toggle(self, slug):
        return f"/r/{slug}/api/salon/toggle-activo/"

    def _url_mesa_guardar(self, slug):
        return f"/r/{slug}/api/mesa/guardar/"

    def _url_mesa_toggle(self, slug):
        return f"/r/{slug}/api/mesa/toggle-activo/"

    def _crear_salon(self, slug, nombre="Terraza", orden=1):
        resp = self.client.post(
            self._url_salon_guardar(slug),
            data=json.dumps({"nombre": nombre, "orden": orden}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    # ------------------------------------------------------------------ Pestañas
    def test_tab_mesas_renderiza_panel(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/crud/?tab=mesas")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Salones y Mesas")
        self.assertContains(resp, "Nuevo Salón")
        self.assertContains(resp, "Nueva Mesa")

    def test_tab_carta_por_defecto(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/crud/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Platos, Menús y Recetas")

    # ------------------------------------------------------------------ Salones
    def test_salon_crear_y_aislar_por_tenant(self):
        data = self._crear_salon(self.tenant.slug, nombre="Terraza")
        self.assertTrue(data["success"])
        self.assertEqual(data["salon"]["nombre"], "Terraza")

        salon = Area.all_objects.get(id=data["salon"]["id"])
        self.assertEqual(salon.restaurante_id, self.tenant.id)

        # Visible solo en el tenant A.
        resp_a = self.client.get(f"/r/{self.tenant.slug}/crud/?tab=mesas")
        self.assertContains(resp_a, "Terraza")
        resp_b = self.client.get(f"/r/{self.otro.slug}/crud/?tab=mesas")
        self.assertNotContains(resp_b, "Terraza")

    def test_salon_editar(self):
        data = self._crear_salon(self.tenant.slug, nombre="Barra")
        salon_id = data["salon"]["id"]

        resp = self.client.post(
            self._url_salon_guardar(self.tenant.slug),
            data=json.dumps({"id": salon_id, "nombre": "Barra VIP", "orden": 2}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        salon = Area.all_objects.get(id=salon_id)
        self.assertEqual(salon.nombre, "Barra VIP")
        self.assertEqual(salon.orden, 2)

    def test_salon_toggle_activo(self):
        data = self._crear_salon(self.tenant.slug)
        salon_id = data["salon"]["id"]

        resp = self.client.post(
            self._url_salon_toggle(self.tenant.slug),
            data=json.dumps({"id": salon_id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()["salon"]["activo"])
        self.assertFalse(Area.all_objects.get(id=salon_id).activo)

    def test_salon_cross_tenant_no_editable(self):
        data_b = self._crear_salon(self.otro.slug, nombre="Salón B")
        salon_b_id = data_b["salon"]["id"]

        resp = self.client.post(
            self._url_salon_guardar(self.tenant.slug),
            data=json.dumps({"id": salon_b_id, "nombre": "Intento Cross", "orden": 0}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertNotEqual(Area.all_objects.get(id=salon_b_id).nombre, "Intento Cross")

    # ------------------------------------------------------------------ Mesas
    def test_mesa_crear_y_listar(self):
        data_salon = self._crear_salon(self.tenant.slug, nombre="Terraza")
        salon_id = data_salon["salon"]["id"]

        resp = self.client.post(
            self._url_mesa_guardar(self.tenant.slug),
            data=json.dumps({"salon_id": salon_id, "numero": "T-01", "capacidad": 6}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["success"])

        mesa = Mesa.all_objects.get(numero="T-01", restaurante=self.tenant)
        self.assertEqual(mesa.area_id, salon_id)
        self.assertEqual(mesa.capacidad, 6)
        self.assertTrue(mesa.activo)

        resp_crud = self.client.get(f"/r/{self.tenant.slug}/crud/?tab=mesas")
        self.assertContains(resp_crud, "T-01")

    def test_mesa_codigo_duplicado_mismo_restaurante_rechazado(self):
        data_salon = self._crear_salon(self.tenant.slug, nombre="Salón Principal")
        salon_id = data_salon["salon"]["id"]

        self.client.post(
            self._url_mesa_guardar(self.tenant.slug),
            data=json.dumps({"salon_id": salon_id, "numero": "M-1", "capacidad": 4}),
            content_type="application/json",
        )
        resp = self.client.post(
            self._url_mesa_guardar(self.tenant.slug),
            data=json.dumps({"salon_id": salon_id, "numero": "M-1", "capacidad": 2}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ya existe", resp.json()["error"].lower())
        self.assertEqual(
            Mesa.all_objects.filter(restaurante=self.tenant, numero="M-1").count(), 1
        )

    def test_mesa_mismo_codigo_en_otro_restaurante_permitido(self):
        salon_a = self._crear_salon(self.tenant.slug, nombre="Salón A")
        salon_b = self._crear_salon(self.otro.slug, nombre="Salón B")

        self.client.post(
            self._url_mesa_guardar(self.tenant.slug),
            data=json.dumps({"salon_id": salon_a["salon"]["id"], "numero": "X-1", "capacidad": 4}),
            content_type="application/json",
        )
        resp_b = self.client.post(
            self._url_mesa_guardar(self.otro.slug),
            data=json.dumps({"salon_id": salon_b["salon"]["id"], "numero": "X-1", "capacidad": 4}),
            content_type="application/json",
        )
        self.assertEqual(resp_b.status_code, 200, resp_b.content)
        self.assertTrue(resp_b.json()["success"])

    def test_mesa_con_salon_cross_tenant_rechazada(self):
        data_salon_b = self._crear_salon(self.otro.slug, nombre="Salón B")
        salon_b_id = data_salon_b["salon"]["id"]

        resp = self.client.post(
            self._url_mesa_guardar(self.tenant.slug),
            data=json.dumps({"salon_id": salon_b_id, "numero": "T-99", "capacidad": 4}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(
            Mesa.all_objects.filter(restaurante=self.tenant, numero="T-99").exists()
        )

    def test_mesa_toggle_activo(self):
        salon = self._crear_salon(self.tenant.slug)
        resp = self.client.post(
            self._url_mesa_guardar(self.tenant.slug),
            data=json.dumps({"salon_id": salon["salon"]["id"], "numero": "M-2", "capacidad": 2}),
            content_type="application/json",
        )
        mesa_id = resp.json()["mesa"]["id"]

        resp_toggle = self.client.post(
            self._url_mesa_toggle(self.tenant.slug),
            data=json.dumps({"id": mesa_id}),
            content_type="application/json",
        )
        self.assertEqual(resp_toggle.status_code, 200, resp_toggle.content)
        self.assertFalse(resp_toggle.json()["mesa"]["activo"])
        self.assertFalse(Mesa.all_objects.get(id=mesa_id).activo)

    # -------------------------------------------------------------- Seguridad
    def test_endpoint_salon_requiere_admin(self):
        self.client.logout()
        resp = self.client.post(
            self._url_salon_guardar(self.tenant.slug),
            data=json.dumps({"nombre": "Sin Admin"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Area.all_objects.filter(nombre="Sin Admin").exists())
