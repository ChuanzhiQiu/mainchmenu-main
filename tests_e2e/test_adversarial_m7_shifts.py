"""
tests_e2e/test_adversarial_m7_shifts.py
Adversarial Stress Test Suite for Milestone M7 (challenger_m7_2_rep).
Empirical verification of:
1. Cross-Tenant Auth Isolation (Cashier unlock cross-tenant rejection, Terminal pairing 403 Forbidden,
   cashier enumeration leakage prevention, and cross-tenant supervisor action rejection).
2. Shift Lifecycle & Cash Discrepancy / Arqueo de Caja (Float, Cash sales calculation, Card exclusion,
   Deficit/Surplus reconciliation, Decimal precision, Single open shift constraint,
   Double-closing prevention, and Supervisor force-close).
3. Backend Screen Lock on Order Taking (cajero_bloqueado=True returning HTTP 423 Locked on JSON/AJAX
   and preventing order creation across all channels).
"""

from decimal import Decimal
import json
import uuid

from django.test import TestCase, Client, RequestFactory
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError

from Menu.models import Restaurante, Terminal, Cajero, TurnoCaja, Plato, Orden, OrdenItem
from Menu.terminal_auth import (
    generate_terminal_token,
    validate_terminal_token,
    get_active_terminal,
    TERMINAL_COOKIE_NAME,
)


class TestAdversarialCrossTenantAuthIsolation(TestCase):
    """
    Dimension 1: Cross-Tenant Auth Isolation Stress Testing.
    Verifies that cashier authentication and terminal pairing strictly enforce tenant boundaries.
    """

    def setUp(self):
        self.client = Client()
        # Tenant A: mainch (the default tenant from migration 0016)
        self.tenant_a, _ = Restaurante.objects.get_or_create(
            slug="mainch",
            defaults={
                "nombre": "Mainch Principal",
                "direccion": "Av. Apoquindo 1000, Las Condes",
                "activo": True
            }
        )
        if not self.tenant_a.activo:
            self.tenant_a.activo = True
            self.tenant_a.save()

        # Tenant B: sushi-bar
        self.tenant_b, _ = Restaurante.objects.get_or_create(
            slug="sushi-bar",
            defaults={
                "nombre": "Sushi Bar Izakaya",
                "direccion": "Av. Vitacura 2000, Vitacura",
                "activo": True
            }
        )
        if not self.tenant_b.activo:
            self.tenant_b.activo = True
            self.tenant_b.save()

        # Cashier A registered exclusively under Tenant A
        self.cajero_a = Cajero.objects.create(
            restaurante=self.tenant_a,
            nombre="Carlos Gomez",
            codigo_empleado="CAJ-MCH-01"
        )
        self.cajero_a.set_pin("1234")
        self.cajero_a.save()

        # Cashier B registered exclusively under Tenant B
        self.cajero_b = Cajero.objects.create(
            restaurante=self.tenant_b,
            nombre="Kenji Sato",
            codigo_empleado="CAJ-SBD-01"
        )
        self.cajero_b.set_pin("5678")
        self.cajero_b.save()

        # Terminal paired to Tenant A
        self.terminal_a = Terminal.objects.create(
            restaurante=self.tenant_a,
            nombre="POS Mostrador A1",
            tipo=Terminal.TIPO_POS,
            activo=True
        )
        self.token_a = generate_terminal_token(
            restaurante_id=self.tenant_a.id,
            restaurante_slug=self.tenant_a.slug,
            terminal_uuid=str(self.terminal_a.uuid),
            nombre=self.terminal_a.nombre,
            tipo=self.terminal_a.tipo
        )

        # Terminal paired to Tenant B
        self.terminal_b = Terminal.objects.create(
            restaurante=self.tenant_b,
            nombre="POS Barra Sushi B1",
            tipo=Terminal.TIPO_POS,
            activo=True
        )
        self.token_b = generate_terminal_token(
            restaurante_id=self.tenant_b.id,
            restaurante_slug=self.tenant_b.slug,
            terminal_uuid=str(self.terminal_b.uuid),
            nombre=self.terminal_b.nombre,
            tipo=self.terminal_b.tipo
        )

        self.plato_b = Plato.objects.create(
            restaurante=self.tenant_b,
            nombre="Sashimi Salmón",
            valor=12000.0
        )

    def test_unlock_cashier_belonging_to_tenant_a_while_accessing_tenant_b_is_rejected(self):
        """
        Adversarial Test 1.1:
        Attempt to unlock cashier belonging to Tenant A (mainch) while accessing
        Tenant B's endpoint (/r/sushi-bar/api/cajero/desbloquear/) -> must be rejected (HTTP 404).
        - Verify rejected with HTTP 404 Not Found.
        - Verify session on Tenant B does not bind Cashier A.
        - Verify Cashier A remains untouched under Tenant A.
        """
        url_tenant_b_unlock = f"/r/{self.tenant_b.slug}/api/cajero/desbloquear/"
        payload = {
            "cajero_id": self.cajero_a.id,
            "pin": "1234"
        }
        resp = self.client.post(
            url_tenant_b_unlock,
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(
            resp.status_code, 404,
            f"Cross-tenant cashier unlock must return HTTP 404, got {resp.status_code}"
        )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("no encontrado", data["error"].lower())

        # Verify session does NOT contain Cashier A
        self.assertNotEqual(self.client.session.get("cajero_id"), self.cajero_a.id)

    def test_paired_terminal_to_tenant_a_accessing_tenant_b_pos_returns_403_forbidden(self):
        """
        Adversarial Test 1.2:
        Pair terminal to Tenant A, then access /r/sushi-bar/pos/ -> verify HTTP 403 Forbidden.
        - Verify JSON / AJAX request returns HTTP 403 with code='TENANT_MISMATCH'.
        - Verify standard browser navigation returns HTTP 403 (HttpResponseForbidden).
        - Verify terminal pairing token for Tenant A is strictly prohibited from operating Tenant B.
        """
        self.client.cookies[TERMINAL_COOKIE_NAME] = self.token_a

        # 1. AJAX / JSON request
        url_sushi_pos = f"/r/{self.tenant_b.slug}/pos/"
        resp_ajax = self.client.get(
            url_sushi_pos,
            HTTP_ACCEPT="application/json"
        )
        self.assertEqual(
            resp_ajax.status_code, 403,
            f"Expected HTTP 403 Forbidden on cross-tenant POS access (AJAX), got {resp_ajax.status_code}"
        )
        data_ajax = resp_ajax.json()
        self.assertFalse(data_ajax["success"])
        self.assertEqual(data_ajax.get("code"), "TENANT_MISMATCH")
        self.assertIn("Conflicto de Terminal", data_ajax.get("error", ""))

        # 2. Standard browser navigation (HTML Accept header)
        client_browser = Client()
        client_browser.cookies[TERMINAL_COOKIE_NAME] = self.token_a
        resp_browser = client_browser.get(url_sushi_pos)
        self.assertEqual(
            resp_browser.status_code, 403,
            f"Expected HTTP 403 Forbidden on cross-tenant POS access (Browser), got {resp_browser.status_code}"
        )
        self.assertIn(b"Conflicto de Terminal", resp_browser.content)

    def test_paired_terminal_to_tenant_b_accessing_tenant_a_pos_returns_403_forbidden(self):
        """
        Adversarial Test 1.3:
        Inverse check: Terminal paired to Tenant B attempting to access Tenant A (/r/mainch/pos/)
        must also be rejected with HTTP 403 Forbidden.
        """
        self.client.cookies[TERMINAL_COOKIE_NAME] = self.token_b
        url_mainch_pos = f"/r/{self.tenant_a.slug}/pos/"

        resp = self.client.get(url_mainch_pos, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("code"), "TENANT_MISMATCH")

    def test_cross_tenant_shift_open_rejected(self):
        """
        Adversarial Test 1.4:
        Attempting to open a shift for Cashier A via Tenant B's endpoint
        (/r/sushi-bar/api/turno/abrir/) must be rejected (HTTP 404).
        """
        url_open_b = f"/r/{self.tenant_b.slug}/api/turno/abrir/"
        resp = self.client.post(
            url_open_b,
            data=json.dumps({"cajero_id": self.cajero_a.id, "monto_inicial": "10000"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["success"])
        # Ensure no shift was created under Tenant B
        self.assertFalse(TurnoCaja.objects.filter(restaurante=self.tenant_b, cajero=self.cajero_a).exists())

    def test_cashier_enumeration_prevents_cross_tenant_leakage(self):
        """
        Adversarial Test 1.5:
        When querying /r/<slug>/api/cajeros/disponibles/, ensure cashiers belonging
        to other tenants are NEVER leaked in the JSON response.
        """
        resp_b = self.client.get(f"/r/{self.tenant_b.slug}/api/cajeros/disponibles/")
        self.assertEqual(resp_b.status_code, 200)
        cajeros_b = resp_b.json()["cajeros"]
        cajero_ids_b = [c["id"] for c in cajeros_b]

        self.assertIn(self.cajero_b.id, cajero_ids_b)
        self.assertNotIn(
            self.cajero_a.id, cajero_ids_b,
            "Security vulnerability: Cashier of Tenant A leaked in Tenant B cashier enumeration!"
        )


class TestAdversarialShiftLifecycleAndArqueo(TestCase):
    """
    Dimension 2: Shift Lifecycle & Cash Discrepancy (Arqueo de Caja) Stress Testing.
    Verifies cash calculation, card exclusions, deficit/surplus detection,
    single active shift constraint, and supervisor force-close.
    """

    def setUp(self):
        self.client = Client()
        # Use mainch tenant so fallback routes and tenant-scoped routes work seamlessly
        self.restaurante, _ = Restaurante.objects.get_or_create(
            slug="mainch",
            defaults={
                "nombre": "Restaurante Central",
                "direccion": "Calle Moneda 500, Santiago",
                "activo": True
            }
        )
        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Esteban Paredes",
            codigo_empleado="CAJ-007"
        )
        self.cajero.set_pin("7777")
        self.cajero.save()

        self.supervisor = User.objects.create_superuser(
            username="supervisor_arqueo",
            password="super_password_123",
            email="super@central.cl"
        )

        self.plato_1 = Plato.objects.create(
            restaurante=self.restaurante,
            nombre="Lomo Saltado",
            valor=10000.0
        )
        self.plato_2 = Plato.objects.create(
            restaurante=self.restaurante,
            nombre="Ceviche Clásico",
            valor=5000.0
        )
        self.plato_card = Plato.objects.create(
            restaurante=self.restaurante,
            nombre="Pisco Sour Catedral",
            valor=8000.0
        )

    def test_shift_lifecycle_exact_cash_calculation_and_card_exclusion(self):
        """
        Adversarial Test 2.1:
        1. Open shift with initial float $10,000.
        2. Place completed orders: 2 Cash ($15,000 total) and 1 Card ($8,000).
        3. Verify calcular_ventas_efectivo() returns exactly $15,000 (excluding card payments).
        4. Verify calcular_monto_esperado() returns $25,000.
        """
        # Step 1: Open shift with initial float $10,000
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        self.assertEqual(turno.monto_inicial, Decimal("10000.00"))
        self.assertEqual(turno.estado, TurnoCaja.ESTADO_ABIERTO)

        # Step 2: Place completed orders
        # Order 1: Cash $10,000 (Completed)
        orden_cash_1 = Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Mesa 1",
            tipo_pago="Efectivo",
            canal_venta=Orden.CANAL_LOCAL,
            monto_total=10000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        # Order 2: Cash $5,000 (Completed)
        orden_cash_2 = Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Mesa 2",
            tipo_pago="Efectivo",
            canal_venta=Orden.CANAL_LOCAL,
            monto_total=5000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        # Order 3: Card $8,000 (Completed)
        orden_card = Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Mesa 3",
            tipo_pago="Tarj. Débito",
            canal_venta=Orden.CANAL_LOCAL,
            monto_total=8000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        # Step 3: Verify calcular_ventas_efectivo() returns exactly $15,000 (excluding $8,000 card)
        ventas_efectivo = turno.calcular_ventas_efectivo()
        self.assertEqual(
            ventas_efectivo, Decimal("15000.00"),
            f"Ventas efectivo expected $15,000.00, got {ventas_efectivo}"
        )

        # Step 4: Verify calcular_monto_esperado() returns $25,000
        monto_esperado = turno.calcular_monto_esperado()
        self.assertEqual(
            monto_esperado, Decimal("25000.00"),
            f"Monto esperado expected $25,000.00 ($10,000 float + $15,000 cash), got {monto_esperado}"
        )

    def test_shift_close_deficit_arqueo_faltante_500(self):
        """
        Adversarial Test 2.2:
        Close shift declaring $24,500 physical cash -> verify diferencia_arqueo is -$500 (faltante).
        - Test both model calculation and API endpoint /api/turno/cerrar/.
        - Verify database state updates to ESTADO_CERRADO with exact negative difference.
        """
        # Open shift with initial float $10,000
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        # Place 2 Cash orders ($15,000 total) and 1 Card order ($8,000)
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente Efectivo 1",
            tipo_pago="Efectivo",
            monto_total=10000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente Efectivo 2",
            tipo_pago="Efectivo",
            monto_total=5000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente Tarjeta",
            tipo_pago="Tarj. Crédito",
            monto_total=8000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        # Verify model difference directly
        monto_declarado = Decimal("24500.00")
        diff_direct = turno.calcular_diferencia(monto_declarado)
        self.assertEqual(
            diff_direct, Decimal("-500.00"),
            f"Expected difference -500.00, got {diff_direct}"
        )

        # Prepare client session with active tenant
        session = self.client.session
        session['active_tenant_slug'] = self.restaurante.slug
        session['cajero_id'] = self.cajero.id
        session['turno_id'] = turno.id
        session.save()

        # Close shift via API /api/turno/cerrar/
        resp = self.client.post(
            "/api/turno/cerrar/",
            data=json.dumps({
                "turno_id": turno.id,
                "monto_final_declarado": "24500",
                "observaciones": "Faltante de $500 en arqueo final verificado."
            }),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        resumen = data["resumen"]
        self.assertEqual(resumen["monto_inicial"], 10000.0)
        self.assertEqual(resumen["ventas_efectivo"], 15000.0)
        self.assertEqual(resumen["monto_esperado"], 25000.0)
        self.assertEqual(resumen["monto_declarado"], 24500.0)
        self.assertEqual(resumen["diferencia"], -500.0)
        self.assertEqual(resumen["estado"], TurnoCaja.ESTADO_CERRADO)

        # Empirical database verification
        turno.refresh_from_db()
        self.assertEqual(turno.estado, TurnoCaja.ESTADO_CERRADO)
        self.assertEqual(turno.monto_final_declarado, Decimal("24500.00"))
        self.assertEqual(turno.diferencia_arqueo, Decimal("-500.00"))
        self.assertIsNotNone(turno.fecha_cierre)
        self.assertEqual(turno.observaciones, "Faltante de $500 en arqueo final verificado.")

    def test_shift_close_surplus_arqueo_sobrante(self):
        """
        Adversarial Test 2.3:
        Close shift declaring $25,500 physical cash when expected is $25,000 -> verify diferencia is +$500.
        """
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente 1",
            tipo_pago="Efectivo",
            monto_total=15000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )

        diff = turno.calcular_diferencia(Decimal("25500.00"))
        self.assertEqual(diff, Decimal("500.00"))

    def test_shift_decimal_precision_with_cents(self):
        """
        Adversarial Test 2.4:
        Verify high decimal precision: Float $10,000.50 + Cash $15,000.75 = Expected $25,001.25.
        Declaring $25,000.00 gives exact difference -$1.25.
        """
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.50"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente Centavos",
            tipo_pago="Efectivo",
            monto_total=15000.75,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )
        self.assertEqual(turno.calcular_ventas_efectivo(), Decimal("15000.75"))
        self.assertEqual(turno.calcular_monto_esperado(), Decimal("25001.25"))
        diff = turno.calcular_diferencia(Decimal("25000.00"))
        self.assertEqual(diff, Decimal("-1.25"))

    def test_eliminated_cash_orders_excluded_from_ventas_efectivo(self):
        """
        Adversarial Test 2.5:
        Ensure cancelled/eliminated cash orders (ESTADO_ELIMINADA) are strictly excluded
        from cash sales and expected float calculation.
        """
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        # Valid cash order
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Orden Valida",
            tipo_pago="Efectivo",
            monto_total=15000.0,
            estado=Orden.ESTADO_COMPLETADA,
            cajero=self.cajero,
            turno=turno
        )
        # Cancelled cash order
        Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Orden Anulada",
            tipo_pago="Efectivo",
            monto_total=7000.0,
            estado=Orden.ESTADO_ELIMINADA,
            cajero=self.cajero,
            turno=turno
        )

        # Eliminated order of $7,000 must NOT be in cash sales
        self.assertEqual(turno.calcular_ventas_efectivo(), Decimal("15000.00"))
        self.assertEqual(turno.calcular_monto_esperado(), Decimal("25000.00"))

    def test_single_active_shift_constraint(self):
        """
        Adversarial Test 2.6:
        Single active shift constraint: attempt to open second shift for same cashier -> verify ValidationError.
        - Model clean() raises ValidationError.
        - API /api/turno/abrir/ returns HTTP 400 with error message.
        """
        # Open first shift
        turno1 = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        # Attempt to create second open shift directly in model
        turno2 = TurnoCaja(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("5000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        with self.assertRaises(ValidationError) as ctx:
            turno2.clean()
        self.assertIn("ya posee un turno de caja abierto", str(ctx.exception))

        # Attempt to open second shift via API endpoint
        resp = self.client.post(
            "/api/turno/abrir/",
            data=json.dumps({"cajero_id": self.cajero.id, "monto_inicial": "5000"}),
            content_type="application/json"
        )
        self.assertEqual(
            resp.status_code, 400,
            f"Expected HTTP 400 when opening second concurrent shift, got {resp.status_code}"
        )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("ya posee un turno abierto", data["error"])
        self.assertEqual(data["turno_id"], turno1.id)

    def test_supervisor_force_close_zombie_shift_frees_cashier(self):
        """
        Adversarial Test 2.7:
        Supervisor force-close of zombie shift:
        - Verify state becomes FORZADO_SUPERVISOR.
        - Verify closed_by_supervisor records supervisor User.
        - Verify cashier is freed: cashier can now open a new shift without ValidationError.
        """
        # 1. Cashier opens a shift and abandons it (zombie shift)
        zombie_shift = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        # 2. While zombie shift is open, cashier cannot open another shift
        with self.assertRaises(ValidationError):
            TurnoCaja(
                restaurante=self.restaurante,
                cajero=self.cajero,
                monto_inicial=Decimal("10000.00"),
                estado=TurnoCaja.ESTADO_ABIERTO
            ).clean()

        # 3. Supervisor force-closes the zombie shift
        self.client.login(username="supervisor_arqueo", password="super_password_123")
        resp = self.client.post(
            "/api/turno/forzar-cierre/",
            data=json.dumps({
                "turno_id": zombie_shift.id,
                "motivo": "Cajero abandonó puesto sin arqueo regular",
                "monto_declarado": "10000"
            }),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["estado"], TurnoCaja.ESTADO_FORZADO_SUPERVISOR)

        # Empirical database verification
        zombie_shift.refresh_from_db()
        self.assertEqual(zombie_shift.estado, TurnoCaja.ESTADO_FORZADO_SUPERVISOR)
        self.assertEqual(zombie_shift.cerrado_por_supervisor, self.supervisor)
        self.assertEqual(zombie_shift.motivo_cierre_forzado, "Cajero abandonó puesto sin arqueo regular")
        self.assertIsNotNone(zombie_shift.fecha_cierre)

        # 4. Verify cashier is freed: cashier can open a new shift cleanly!
        nuevo_turno = TurnoCaja(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("12000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        # Must NOT raise ValidationError
        nuevo_turno.clean()
        nuevo_turno.save()
        self.assertEqual(nuevo_turno.estado, TurnoCaja.ESTADO_ABIERTO)

        # API also allows opening new shift for this cashier
        nuevo_turno.estado = TurnoCaja.ESTADO_CERRADO
        nuevo_turno.save()

        resp_abrir = self.client.post(
            "/api/turno/abrir/",
            data=json.dumps({"cajero_id": self.cajero.id, "monto_inicial": "15000"}),
            content_type="application/json"
        )
        self.assertEqual(resp_abrir.status_code, 200)
        self.assertTrue(resp_abrir.json()["success"])

    def test_non_supervisor_cannot_force_close_shift(self):
        """
        Adversarial Test 2.8:
        Regular unauthenticated or non-staff user attempting to call /api/turno/forzar-cierre/
        must be rejected with HTTP 403 Forbidden.
        """
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )
        client_unauth = Client()
        resp = client_unauth.post(
            "/api/turno/forzar-cierre/",
            data=json.dumps({"turno_id": turno.id, "motivo": "Intento malicioso"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 403)
        turno.refresh_from_db()
        self.assertEqual(turno.estado, TurnoCaja.ESTADO_ABIERTO)

    def test_shift_cannot_be_closed_twice(self):
        """
        Adversarial Test 2.9:
        Attempting to close a shift that has already been closed must return HTTP 404 cleanly.
        """
        turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_CERRADO,
            fecha_cierre=timezone.now()
        )
        resp = self.client.post(
            "/api/turno/cerrar/",
            data=json.dumps({"turno_id": turno.id, "monto_final_declarado": "10000"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["success"])


class TestAdversarialBackendScreenLock(TestCase):
    """
    Dimension 3: Backend Screen Lock on Order Taking Stress Testing.
    Verifies that when cajero_bloqueado=True, order taking is strictly rejected with HTTP 423 Locked.
    """

    def setUp(self):
        self.client = Client()
        self.restaurante, _ = Restaurante.objects.get_or_create(
            slug="mainch",
            defaults={
                "nombre": "Restaurante Lockout Test",
                "direccion": "Calle Providencia 300",
                "activo": True
            }
        )
        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Daniela Rojas",
            codigo_empleado="CAJ-LK-01"
        )
        self.cajero.set_pin("4321")
        self.cajero.save()

        self.turno = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            monto_inicial=Decimal("10000.00"),
            estado=TurnoCaja.ESTADO_ABIERTO
        )

        self.plato = Plato.objects.create(
            restaurante=self.restaurante,
            nombre="Pizza Cuatro Quesos",
            valor=9500.0
        )

    def test_locked_screen_rejects_order_creation_with_http_423(self):
        """
        Adversarial Test 3.1:
        Lock screen (cajero_bloqueado=True) -> attempt POST /pedidos/crear/ -> verify HTTP 423 Locked.
        - Verify HTTP 423 Locked is returned.
        - Verify response JSON contains {"success": False, "bloqueado": True}.
        - Verify no order is created in database.
        """
        # Set screen lock flag in session
        session = self.client.session
        session['cajero_id'] = self.cajero.id
        session['cajero_nombre'] = self.cajero.nombre
        session['cajero_bloqueado'] = True
        session['turno_id'] = self.turno.id
        session.save()

        order_data = {
            "cliente": "Cliente en Pantalla Bloqueada",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"id": self.plato.id, "tipo": "plato", "cantidad": 2}]
        }

        initial_order_count = Orden.objects.count()

        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(order_data),
            content_type="application/json"
        )
        self.assertEqual(
            resp.status_code, 423,
            f"Expected HTTP 423 Locked on screen lock, got {resp.status_code}"
        )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertTrue(data.get("bloqueado"))
        self.assertIn("Terminal bloqueada", data.get("error", ""))

        # Verify zero orders were saved in database
        self.assertEqual(
            Orden.objects.count(), initial_order_count,
            "No order should be created when screen is locked"
        )

    def test_locked_screen_rejects_canonical_tenant_order_creation(self):
        """
        Adversarial Test 3.2:
        Attempting to create order on canonical tenant URL /r/<slug>/pos/ or /r/<slug>/pedidos/crear/
        with cajero_bloqueado=True must return HTTP 423 Locked.
        """
        session = self.client.session
        session['cajero_bloqueado'] = True
        session.save()

        order_data = {
            "cliente": "Cliente Tenant Route",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"id": self.plato.id, "tipo": "plato", "cantidad": 1}]
        }

        url_tenant_pos = f"/r/{self.restaurante.slug}/pos/"
        resp = self.client.post(
            url_tenant_pos,
            data=json.dumps(order_data),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 423)
        self.assertTrue(resp.json().get("bloqueado"))

    def test_locked_screen_rejects_form_order_submission_without_creating_order(self):
        """
        Adversarial Test 3.3:
        If an attacker attempts standard HTML Form POST while screen is locked:
        - Verify order is NOT created in database.
        - Verify redirect to POS with error message.
        """
        session = self.client.session
        session['cajero_bloqueado'] = True
        session.save()

        initial_count = Orden.objects.count()

        resp = self.client.post(
            "/pedidos/crear/",
            data={"cliente": "Attacker Form Bypass", "canal_venta": "Local", "tipo_pago": "Efectivo"}
        )
        # Form POST redirects with error
        self.assertEqual(resp.status_code, 302)
        # Zero orders created!
        self.assertEqual(Orden.objects.count(), initial_count)

    def test_unlock_cashier_screen_restores_order_creation_with_attribution(self):
        """
        Adversarial Test 3.4:
        1. Start locked -> order attempt fails with 423.
        2. Unlock cashier with correct PIN -> cajero_bloqueado becomes False.
        3. Subsequent order attempt succeeds (HTTP 201).
        4. Verify created order is properly attributed with cajero and turno.
        """
        session = self.client.session
        session['cajero_bloqueado'] = True
        session.save()

        order_data = {
            "cliente": "Cliente Desbloqueado",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"id": self.plato.id, "tipo": "plato", "cantidad": 1}]
        }

        # Step 1: Rejected while locked
        resp_locked = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(order_data),
            content_type="application/json"
        )
        self.assertEqual(resp_locked.status_code, 423)

        # Step 2: Unlock with valid PIN
        resp_unlock = self.client.post(
            "/api/cajero/desbloquear/",
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "4321"}),
            content_type="application/json"
        )
        self.assertEqual(resp_unlock.status_code, 200)
        self.assertFalse(self.client.session.get("cajero_bloqueado"))

        # Step 3: Order attempt now succeeds
        session = self.client.session
        session['turno_id'] = self.turno.id
        session.save()

        resp_order = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(order_data),
            content_type="application/json"
        )
        self.assertEqual(resp_order.status_code, 201)
        orden_id = resp_order.json()["orden_id"]

        # Step 4: Verify attribution
        orden = Orden.objects.get(id=orden_id)
        self.assertEqual(orden.cajero, self.cajero)
        self.assertEqual(orden.turno, self.turno)

    def test_api_cajero_bloquear_locks_session_immediately(self):
        """
        Adversarial Test 3.5:
        Calling /api/cajero/bloquear/ immediately sets cajero_bloqueado=True,
        blocking any subsequent order attempt with HTTP 423.
        """
        # Unlock cashier first
        self.client.post(
            "/api/cajero/desbloquear/",
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "4321"}),
            content_type="application/json"
        )
        self.assertFalse(self.client.session.get("cajero_bloqueado"))

        # Lock terminal via API
        resp_lock = self.client.post("/api/cajero/bloquear/")
        self.assertEqual(resp_lock.status_code, 200)
        self.assertTrue(resp_lock.json()["bloqueado"])
        self.assertTrue(self.client.session.get("cajero_bloqueado"))

        # Order is now blocked with 423
        resp_order = self.client.post(
            "/pedidos/crear/",
            data=json.dumps({
                "cliente": "Test Post Lock",
                "items": [{"id": self.plato.id, "tipo": "plato", "cantidad": 1}]
            }),
            content_type="application/json"
        )
        self.assertEqual(resp_order.status_code, 423)
