"""
Menu/tests_m7_auth.py: Comprehensive Verification Suite for Milestone M7.
Covers:
- Layer 1: Cryptographic Terminal Token Signing, Cookie persistence over admin logout,
           cross-tenant isolation, and hybrid revocation.
- Layer 2: PBKDF2 PIN security, 5-attempt rate-limiting lockout (HTTP 423), supervisor unlock.
- Shift Tracking: TurnoCaja lifecycle, single open shift constraint, cash discrepancy arqueo,
                 supervisor force-close.
- POS Security: Backend HTTP 423 on locked screen, automatic order tagging (cajero_id, turno_id),
                safe unassigned delivery fallback.
- RBAC Elevation: Admin-only protection on sugerencias_compra_api_view.
"""

from decimal import Decimal
import json
import uuid

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from Menu.models import Restaurante, Plato, Orden, OrdenItem, Terminal, Cajero, TurnoCaja
from Menu.terminal_auth import (
    generate_terminal_token,
    validate_terminal_token,
    TERMINAL_COOKIE_NAME,
    set_terminal_cookie,
    get_active_terminal
)


class Layer1TerminalSecurityTests(TestCase):
    """Pruebas de seguridad de Capa 1: Persistencia de Terminal y Token Criptográfico."""

    def setUp(self):
        self.client = Client()
        self.restaurante_a = Restaurante.objects.create(
            nombre="Restaurante A", slug="rest-a", direccion="Santiago"
        )
        self.restaurante_b = Restaurante.objects.create(
            nombre="Restaurante B", slug="rest-b", direccion="Valparaíso"
        )
        self.admin_user = User.objects.create_superuser(
            username="admin_term", password="password123", email="admin@test.com"
        )

    def test_layer1_token_generation_and_valid_verification(self):
        """Verifica que el token firmado se genere y valide correctamente."""
        token = generate_terminal_token(
            restaurante_id=self.restaurante_a.id,
            restaurante_slug=self.restaurante_a.slug,
            nombre="Terminal Caja 1",
            tipo="POS"
        )
        self.assertIsInstance(token, str)
        payload = validate_terminal_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["restaurante_id"], self.restaurante_a.id)
        self.assertEqual(payload["restaurante_slug"], self.restaurante_a.slug)
        self.assertEqual(payload["tipo"], "POS")

    def test_layer1_token_tampering_rejection(self):
        """Cualquier alteración a la firma criptográfica invalida el token."""
        token = generate_terminal_token(self.restaurante_a.id, self.restaurante_a.slug)
        tampered = token[:-4] + "ABCD"
        self.assertIsNone(validate_terminal_token(tampered))

    def test_layer1_cookie_survives_admin_logout(self):
        """R2: El logout del administrador flushea la sesión pero la cookie de terminal sobrevive."""
        terminal = Terminal.objects.create(
            restaurante=self.restaurante_a, nombre="POS Principal", activo=True
        )
        token = generate_terminal_token(
            self.restaurante_a.id, self.restaurante_a.slug, terminal_uuid=str(terminal.uuid)
        )
        self.client.cookies[TERMINAL_COOKIE_NAME] = token

        # Iniciar sesión de admin
        self.client.login(username="admin_term", password="password123")
        self.assertIn('_auth_user_id', self.client.session)

        # Cerrar sesión de admin
        resp = self.client.get(reverse('Menu:logout'))
        self.assertEqual(resp.status_code, 302)

        # La cookie de terminal sigue presente en el cliente
        self.assertIn(TERMINAL_COOKIE_NAME, self.client.cookies)
        self.assertEqual(self.client.cookies[TERMINAL_COOKIE_NAME].value, token)

    def test_layer1_cross_tenant_terminal_mismatch_rejected(self):
        """Una terminal configurada para Tenant A debe ser rechazada (403) al operar en Tenant B."""
        term_a = Terminal.objects.create(
            restaurante=self.restaurante_a, nombre="Caja A", activo=True
        )
        token_a = generate_terminal_token(
            self.restaurante_a.id, self.restaurante_a.slug, terminal_uuid=str(term_a.uuid)
        )
        self.client.cookies[TERMINAL_COOKIE_NAME] = token_a

        # Intentar acceder al POS canónico de Tenant B
        url_b = f"/r/{self.restaurante_b.slug}/pos/"
        resp = self.client.get(url_b, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 403)

    def test_layer1_hybrid_revocation(self):
        """Desactivar la terminal en la base de datos revoca el acceso inmediatamente."""
        term = Terminal.objects.create(
            restaurante=self.restaurante_a, nombre="Tablet Robada", activo=True
        )
        token = generate_terminal_token(
            self.restaurante_a.id, self.restaurante_a.slug, terminal_uuid=str(term.uuid)
        )
        self.client.cookies[TERMINAL_COOKIE_NAME] = token

        # Desactivar en base de datos
        term.activo = False
        term.save()

        # Al consultar la terminal activa, el chequeo híbrido devuelve None
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get(f"/r/{self.restaurante_a.slug}/pos/")
        req.COOKIES[TERMINAL_COOKIE_NAME] = token
        payload = get_active_terminal(req)
        self.assertIsNone(payload)

    def test_layer1_pairing_and_unpairing_ceremony(self):
        """Ceremonia de vinculación y desvinculación de terminal por administrador."""
        self.client.login(username="admin_term", password="password123")

        # Vincular
        resp_act = self.client.post(
            f"/r/{self.restaurante_a.slug}/terminal/activar/",
            data={"nombre": "Caja 1 Mostrador", "tipo": "POS"}
        )
        self.assertEqual(resp_act.status_code, 302)
        self.assertIn(TERMINAL_COOKIE_NAME, resp_act.cookies)
        self.assertTrue(Terminal.objects.filter(nombre="Caja 1 Mostrador", activo=True).exists())

        # Desvincular
        token = resp_act.cookies[TERMINAL_COOKIE_NAME].value
        self.client.cookies[TERMINAL_COOKIE_NAME] = token
        resp_deact = self.client.post(f"/r/{self.restaurante_a.slug}/terminal/desactivar/")
        self.assertEqual(resp_deact.status_code, 302)
        # Terminal en DB debe estar desactivada
        self.assertFalse(Terminal.objects.filter(nombre="Caja 1 Mostrador", activo=True).exists())


class Layer2CashierPINAndLockoutTests(TestCase):
    """Pruebas de seguridad de Capa 2: PIN PBKDF2, Rate-Limiting de 5 intentos y Desbloqueo."""

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(
            nombre="Pizzería Central", slug="pizzeria-central"
        )
        self.restaurante_otro = Restaurante.objects.create(
            nombre="Sushi Bar", slug="sushi-bar"
        )
        self.admin = User.objects.create_superuser(
            username="supervisor_caja", password="password123"
        )

        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Diego Morales",
            codigo_empleado="CAJ-001"
        )
        self.cajero.set_pin("1234")
        self.cajero.save()

    def test_cajero_pbkdf2_pin_hashing(self):
        """El PIN se almacena como hash PBKDF2 y nunca en texto plano."""
        self.assertTrue(self.cajero.pin_hash.startswith("pbkdf2_sha256$"))
        self.assertTrue(self.cajero.check_pin("1234"))
        self.assertFalse(self.cajero.check_pin("0000"))

    def test_cajero_pin_requires_4_digits(self):
        """El PIN debe ser estrictamente de 4 dígitos numéricos."""
        with self.assertRaises(ValidationError):
            self.cajero.set_pin("123")
        with self.assertRaises(ValidationError):
            self.cajero.set_pin("12345")
        with self.assertRaises(ValidationError):
            self.cajero.set_pin("abcd")

    def test_pin_rate_limiting_5_attempts_and_lockout(self):
        """5 intentos fallidos consecutivos provocan un bloqueo HTTP 423 por 60 segundos."""
        url = "/api/cajero/desbloquear/"

        # Primeros 4 intentos fallidos -> HTTP 401 con intentos restantes decrecientes
        for i in range(1, 5):
            resp = self.client.post(
                url,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "9999"}),
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 401)
            data = resp.json()
            self.assertFalse(data["success"])
            self.assertEqual(data["intentos_restantes"], 5 - i)

        # 5to intento fallido -> HTTP 423 Locked con timer de 60 segundos
        resp_5 = self.client.post(
            url,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "9999"}),
            content_type="application/json"
        )
        self.assertEqual(resp_5.status_code, 423)
        data_5 = resp_5.json()
        self.assertTrue(data_5["bloqueado"])
        self.assertGreaterEqual(data_5["segundos_restantes"], 59)

        # 6to intento, incluso con el PIN correcto, debe ser rechazado con 423 por bloqueo activo
        resp_6 = self.client.post(
            url,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "1234"}),
            content_type="application/json"
        )
        self.assertEqual(resp_6.status_code, 423)

    def test_supervisor_reset_lockout(self):
        """Un supervisor con permisos de administración puede desbloquear inmediatamente al cajero."""
        # Forzar bloqueo del cajero
        self.cajero.intentos_fallidos = 5
        self.cajero.bloqueado_hasta = timezone.now() + timezone.timedelta(seconds=60)
        self.cajero.save()
        self.assertTrue(self.cajero.is_locked())

        # Supervisor desbloquea
        self.client.login(username="supervisor_caja", password="password123")
        resp = self.client.post(
            "/api/cajero/desbloquear-supervisor/",
            data=json.dumps({"cajero_id": self.cajero.id}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)

        # Cajero ya no está bloqueado y puede ingresar con su PIN correcto
        self.cajero.refresh_from_db()
        self.assertFalse(self.cajero.is_locked())
        self.assertEqual(self.cajero.intentos_fallidos, 0)

        resp_unlock = self.client.post(
            "/api/cajero/desbloquear/",
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "1234"}),
            content_type="application/json"
        )
        self.assertEqual(resp_unlock.status_code, 200)

    def test_cross_tenant_cashier_isolation(self):
        """Un cajero del Restaurante A no puede autenticarse ni desbloquear en el Restaurante B."""
        url_otro_tenant = f"/r/{self.restaurante_otro.slug}/api/cajero/desbloquear/"
        resp = self.client.post(
            url_otro_tenant,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "1234"}),
            content_type="application/json"
        )
        # Cajero no pertenece al tenant -> 404 No encontrado
        self.assertEqual(resp.status_code, 404)


class ShiftLifecycleAndArqueoTests(TestCase):
    """Pruebas de ciclo de vida de TurnoCaja, conciliación de efectivo y arqueo."""

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(nombre="Burger Bar", slug="burger-bar")
        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante, nombre="Valentina", codigo_empleado="CAJ-002"
        )
        self.cajero.set_pin("4321")
        self.cajero.save()

        self.supervisor = User.objects.create_superuser(
            username="super_shift", password="password123"
        )

        self.plato = Plato.objects.create(
            restaurante=self.restaurante, nombre="Hamburguesa Clásica", valor=10000.0
        )

    def test_open_shift_single_open_constraint(self):
        """Un cajero no puede abrir dos turnos simultáneamente."""
        turno1 = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("30000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        # Intentar abrir segundo turno debe fallar en clean() o vía API
        turno2 = TurnoCaja(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("20000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        with self.assertRaises(ValidationError):
            turno2.clean()

    def test_shift_cash_reconciliation_exact(self):
        """Apertura $50.000 + Venta Efectivo $25.000 = Esperado $75.000. Conteo exacto -> Diferencia 0."""
        # Desbloquear cajero y abrir turno vía API
        self.client.post(
            "/api/cajero/desbloquear/",
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "4321"}),
            content_type="application/json"
        )
        resp_abrir = self.client.post(
            "/api/turno/abrir/",
            data=json.dumps({"monto_inicial": "50000"}),
            content_type="application/json"
        )
        self.assertEqual(resp_abrir.status_code, 200)
        turno_id = resp_abrir.json()["turno"]["id"]
        turno = TurnoCaja.objects.get(id=turno_id)

        # Registrar venta en efectivo asignada al turno
        orden = Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Mesa 1",
            tipo_pago="Efectivo",
            monto_total=25000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        # Cerrar turno declarando $75.000 exactos
        resp_cerrar = self.client.post(
            "/api/turno/cerrar/",
            data=json.dumps({"turno_id": turno_id, "monto_final_declarado": "75000"}),
            content_type="application/json"
        )
        self.assertEqual(resp_cerrar.status_code, 200)
        resumen = resp_cerrar.json()["resumen"]
        self.assertEqual(resumen["monto_inicial"], 50000.0)
        self.assertEqual(resumen["ventas_efectivo"], 25000.0)
        self.assertEqual(resumen["monto_esperado"], 75000.0)
        self.assertEqual(resumen["monto_declarado"], 75000.0)
        self.assertEqual(resumen["diferencia"], 0.0)
        self.assertEqual(resumen["estado"], TurnoCaja.ESTADO_CERRADO)

    def test_shift_cash_reconciliation_deficit(self):
        """Apertura $50.000 + Venta $20.000 = $70.000. Declara $68.000 -> Faltante -$2.000."""
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("50000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente Faltante",
            tipo_pago="Efectivo",
            monto_total=20000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        diferencia = turno.calcular_diferencia(Decimal("68000.00"))
        self.assertEqual(diferencia, Decimal("-2000.00"))

    def test_supervisor_force_close_zombie_shift(self):
        """Un supervisor puede forzar el cierre de un turno zombie especificando el motivo."""
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("40000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        self.client.login(username="super_shift", password="password123")
        resp = self.client.post(
            "/api/turno/forzar-cierre/",
            data=json.dumps({
                "turno_id": turno.id,
                "motivo": "Cajero se retiró sin arqueo de caja",
                "monto_declarado": "40000"
            }),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        turno.refresh_from_db()
        self.assertEqual(turno.estado, TurnoCaja.ESTADO_FORZADO_SUPERVISOR)
        self.assertEqual(turno.cerrado_por_supervisor, self.supervisor)
        self.assertEqual(turno.motivo_cierre_forzado, "Cajero se retiró sin arqueo de caja")


class POSScreenLockAndOrderAttributionTests(TestCase):
    """Pruebas de bloqueo de pantalla POS y asignación obligatoria de cajero y turno a órdenes."""

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(nombre="Cafe Paris", slug="cafe-paris")
        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante, nombre="Camila", codigo_empleado="CAJ-003"
        )
        self.cajero.set_pin("5555")
        self.cajero.save()
        self.plato = Plato.objects.create(
            restaurante=self.restaurante, nombre="Croissant", valor=3500.0
        )

    def test_order_creation_blocked_when_screen_locked(self):
        """Si la terminal está bloqueada en sesión, crear orden devuelve HTTP 423 Locked."""
        session = self.client.session
        session['cajero_bloqueado'] = True
        session.save()

        order_data = {
            "cliente": "Cliente Bloqueado",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"id": self.plato.id, "tipo": "plato", "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(order_data),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 423)
        self.assertTrue(resp.json().get("bloqueado"))

    def test_order_tagged_with_cashier_and_shift(self):
        """Una orden creada con sesión activa de cajero registra cajero_id y turno_id en base de datos."""
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        session = self.client.session
        session['cajero_id'] = self.cajero.id
        session['cajero_nombre'] = self.cajero.nombre
        session['cajero_bloqueado'] = False
        session['turno_id'] = turno.id
        session.save()

        order_data = {
            "cliente": "Juan Perez",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"id": self.plato.id, "tipo": "plato", "cantidad": 2}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(order_data),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 201)
        orden_id = resp.json()["orden_id"]

        orden = Orden.objects.get(id=orden_id)
        self.assertEqual(orden.cajero, self.cajero)
        self.assertEqual(orden.turno, turno)

    def test_delivery_orders_unassigned_safely(self):
        """Órdenes creadas sin cajero (ej. delivery webhooks) se guardan limpiamente con cajero=None."""
        orden = Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Uber Eats Delivery #998",
            canal_venta=Orden.CANAL_UBER_EATS,
            tipo_pago="Delivery",
            monto_total=15000.0
        )
        self.assertIsNone(orden.cajero)
        self.assertIsNone(orden.turno)


class AdminElevationTests(TestCase):
    """Pruebas de control de acceso RBAC y elevación de permisos en sugerencias_compra_api_view."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin_rbac", password="password123", email="admin@rbac.com"
        )
        self.cajero_user = User.objects.create_user(
            username="cajero_sin_staff", password="password123"
        )

    def test_cashier_rejected_from_ai_suggestions(self):
        """Un usuario con rol cajero (sin staff) es rechazado con HTTP 403 en sugerencias de compra."""
        self.client.login(username="cajero_sin_staff", password="password123")
        resp = self.client.get("/api/sugerencias-compra/")
        self.assertEqual(resp.status_code, 403)

    def test_cashier_session_rejected_from_ai_suggestions(self):
        """Una sesión con cajero_id activo es rechazada con HTTP 403 en sugerencias de compra."""
        session = self.client.session
        session['cajero_id'] = 123
        session.save()
        resp = self.client.get("/api/sugerencias-compra/")
        self.assertEqual(resp.status_code, 403)

    def test_admin_allowed_in_ai_suggestions(self):
        """Un usuario administrador (is_staff=True) tiene acceso permitido a sugerencias de compra."""
        self.client.login(username="admin_rbac", password="password123")
        resp = self.client.get("/api/sugerencias-compra/")
        self.assertEqual(resp.status_code, 200)
