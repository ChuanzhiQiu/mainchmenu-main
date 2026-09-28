"""
QA de aceptación: Flujo de Autenticación de Restaurante (Nivel 1) vs Administrador (Nivel 2).

Cubre:
1. Acceso inicial sin cookies -> redirige a login_restaurante.
2. Sesión de restaurante -> acceso a Cocina/POS del tenant correcto.
3. Cajero opera por PIN sin pedir login de admin.
4. Elevación de administrador restringida al restaurante en sesión (403 cross-tenant).
5. Logout de restaurante -> vuelve a login_restaurante; nuevo login cambia de tenant.
6. Logout de administrador no destruye la sesión de restaurante ni el turno de caja.
"""
import json

from django.test import TestCase, Client
from django.contrib.auth.models import User

from Menu.models import Cajero, CredencialRestaurante, PerfilAdministrador, Restaurante, TurnoCaja


class RestauranteAdminAuthFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

        # --- Nivel 1: locales ---
        self.hamburguesas = Restaurante.objects.create(
            nombre="Hamburguesas Juan", slug="hamburguesas-juan"
        )
        self.credencial_juan = CredencialRestaurante.objects.create(
            restaurante=self.hamburguesas, usuario="hamburguesas_juan"
        )
        self.credencial_juan.set_password("local123")
        self.credencial_juan.save()

        # El tenant 'mainch' ya puede existir (data migration 0016).
        self.mainch, _ = Restaurante.objects.get_or_create(
            slug="mainch", defaults={"nombre": "Mainch", "direccion": "Valparaíso, Chile"}
        )
        self.credencial_mainch = CredencialRestaurante.objects.create(
            restaurante=self.mainch, usuario="mainch"
        )
        self.credencial_mainch.set_password("mainch123")
        self.credencial_mainch.save()

        # --- Nivel 2: administradores ---
        self.admin_juan = User.objects.create_superuser(
            username="admin_juan", password="admin123", email="juan@test.cl"
        )
        PerfilAdministrador.objects.create(user=self.admin_juan, restaurante=self.hamburguesas)

        self.admin_mainch = User.objects.create_superuser(
            username="admin_mainch", password="admin123", email="mainch@test.cl"
        )
        PerfilAdministrador.objects.create(user=self.admin_mainch, restaurante=self.mainch)

        # --- Cajeros por local ---
        self.cajero_juan = Cajero.objects.create(
            restaurante=self.hamburguesas,
            nombre="Cajero Juan",
            codigo_empleado="CAJ-JUAN",
            pin_hash="hash",
        )
        self.cajero_juan.set_pin("1234")
        self.cajero_juan.save()

        self.cajero_mainch = Cajero.objects.create(
            restaurante=self.mainch,
            nombre="Cajero Mainch",
            codigo_empleado="CAJ-MAINCH",
            pin_hash="hash",
        )
        self.cajero_mainch.set_pin("5678")
        self.cajero_mainch.save()

    def _login_restaurante(self, usuario, clave):
        return self.client.post(
            "/login_restaurante/",
            data={"username": usuario, "password": clave},
        )

    # ------------------------------------------------------------------ 1. Acceso inicial
    def test_sin_cookies_raiz_redirige_login_restaurante(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login_restaurante/", resp.url)

    def test_sin_cookies_pos_redirige_login_restaurante(self):
        resp = self.client.get("/pos/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login_restaurante/", resp.url)

    def test_sin_sesion_api_turno_abrir_devuelve_401(self):
        resp = self.client.post(
            "/api/turno/abrir/",
            data=json.dumps({"monto_inicial": "5000"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------ 2. Carga operativa
    def test_login_restaurante_carga_cocina_del_tenant(self):
        resp = self._login_restaurante("hamburguesas_juan", "local123")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.session["restaurante_id"], self.hamburguesas.id)

        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hamburguesas Juan")

    def test_login_restaurante_invalido_no_establece_sesion(self):
        resp = self._login_restaurante("hamburguesas_juan", "clave-mala")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("restaurante_id", self.client.session)

    # ------------------------------------------------------------------ 3. Cajero por PIN
    def test_cajero_opera_por_pin_sin_login_admin(self):
        self._login_restaurante("hamburguesas_juan", "local123")

        resp = self.client.post(
            "/api/cajero/desbloquear/",
            data=json.dumps({"cajero_id": self.cajero_juan.id, "pin": "1234"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("cajero_id", self.client.session)

        resp = self.client.post(
            "/api/turno/abrir/",
            data=json.dumps({"monto_inicial": "5000"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["success"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lista_cajeros_solo_del_restaurante_en_sesion(self):
        self._login_restaurante("hamburguesas_juan", "local123")
        resp = self.client.get("/api/cajeros/disponibles/")
        self.assertEqual(resp.status_code, 200)
        ids = [c["id"] for c in resp.json()["cajeros"]]
        self.assertIn(self.cajero_juan.id, ids)
        self.assertNotIn(self.cajero_mainch.id, ids)

    # ------------------------------------------------------------------ 4. Cruce de administradores
    def test_admin_de_otro_restaurante_rechazado_403(self):
        self._login_restaurante("hamburguesas_juan", "local123")

        resp = self.client.post(
            "/login_admin/",
            data={"username": "admin_mainch", "password": "admin123"},
        )
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_admin_sin_sesion_restaurante_403(self):
        resp = self.client.post(
            "/login_admin/",
            data={"username": "admin_juan", "password": "admin123"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_del_mismo_restaurante_eleva_y_preserva_sesion(self):
        self._login_restaurante("hamburguesas_juan", "local123")
        session = self.client.session
        session["cajero_id"] = self.cajero_juan.id
        session.save()

        resp = self.client.post(
            "/login_admin/",
            data={"username": "admin_juan", "password": "admin123"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        # La sesión operativa del restaurante y el cajero se preservan.
        self.assertEqual(self.client.session["restaurante_id"], self.hamburguesas.id)
        self.assertEqual(self.client.session["cajero_id"], self.cajero_juan.id)

    def test_logout_admin_preserva_restaurante_y_turno(self):
        self._login_restaurante("hamburguesas_juan", "local123")

        turno = TurnoCaja.objects.create(
            restaurante=self.hamburguesas,
            cajero=self.cajero_juan,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )
        session = self.client.session
        session["cajero_id"] = self.cajero_juan.id
        session["turno_id"] = turno.id
        session.save()

        self.client.post(
            "/login_admin/",
            data={"username": "admin_juan", "password": "admin123"},
        )
        self.assertIn("_auth_user_id", self.client.session)

        resp = self.client.get("/logout_admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        # Restaurante y turno intactos.
        self.assertEqual(self.client.session["restaurante_id"], self.hamburguesas.id)
        self.assertEqual(self.client.session["turno_id"], turno.id)

    # ------------------------------------------------------------------ 5. Logout y cambio de tenant
    def test_logout_restaurante_vuelve_a_login_y_permite_cambiar_tenant(self):
        self._login_restaurante("hamburguesas_juan", "local123")
        resp = self.client.get("/logout_restaurante/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login_restaurante/", resp.url)
        self.assertNotIn("restaurante_id", self.client.session)

        # Ingresar con Mainch: la app opera bajo Mainch.
        resp = self._login_restaurante("mainch", "mainch123")
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mainch")

        resp = self.client.get("/api/cajeros/disponibles/")
        ids = [c["id"] for c in resp.json()["cajeros"]]
        self.assertIn(self.cajero_mainch.id, ids)
        self.assertNotIn(self.cajero_juan.id, ids)
