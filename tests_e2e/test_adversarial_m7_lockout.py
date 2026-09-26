"""
tests_e2e/test_adversarial_m7_lockout.py
Adversarial Stress Test Suite for Milestone M7 (challenger_m7_1).
Empirical verification of PIN Brute-Force Protection, Lockout, Supervisor Override,
and Layer 1 Token Tampering / Hybrid Revocation.

Requirements Tested:
1. PIN brute-force protection:
   - Rapidly send 4 incorrect PINs -> verify attempts recorded, terminal remains unlocked (401).
   - Send 5th incorrect PIN -> verify HTTP 423 Locked returned, 60s lockout triggered.
   - Send 6th attempt immediately -> verify immediate HTTP 423 Locked with remaining seconds.
   - Verify lockout is enforced even across new sessions if targeting the same locked cashier.
2. Supervisor Override:
   - Call supervisor unlock endpoint with admin credentials -> verify cashier and terminal immediately unlocked.
3. Layer 1 Token Tampering & Hybrid Revocation:
   - Modify signature of mainch_terminal_token cookie -> verify token rejected cleanly without crash.
   - Test terminal revocation: set Terminal.activo=False -> verify token immediately rejected (hybrid revocation).
"""

from decimal import Decimal
import json
import uuid
import time

from django.test import TestCase, Client, RequestFactory
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse
from django.core import signing

from Menu.models import Restaurante, Terminal, Cajero, TurnoCaja, Plato
from Menu.terminal_auth import (
    generate_terminal_token,
    validate_terminal_token,
    get_active_terminal,
    TERMINAL_COOKIE_NAME,
    TERMINAL_COOKIE_SALT,
)
from Menu.services.auth_service import (
    verify_and_unlock_cashier,
    supervisor_reset_lockout,
)


class TestAdversarialPINBruteForceAndLockout(TestCase):
    """
    Dimension 1: PIN Brute-Force Protection & Lockout Stress Testing.
    Verifies that 4-digit PIN authentication is strictly protected against brute-force
    attacks through progressive rate-limiting and a 60-second database/session lockout.
    """

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(
            nombre="Trattoria Bella",
            slug="trattoria-bella",
            direccion="Av. Providencia 1234, Santiago",
            activo=True
        )
        self.restaurante_rival = Restaurante.objects.create(
            nombre="Sushi Master",
            slug="sushi-master",
            direccion="Av. Vitacura 5678, Santiago",
            activo=True
        )

        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Alonso Herrera",
            codigo_empleado="CAJ-701"
        )
        self.cajero.set_pin("4321")
        self.cajero.save()

        self.cajero_dos = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Beatriz Silva",
            codigo_empleado="CAJ-702"
        )
        self.cajero_dos.set_pin("8888")
        self.cajero_dos.save()

        self.url_desbloquear = "/api/cajero/desbloquear/"
        self.url_tenant_desbloquear = f"/r/{self.restaurante.slug}/api/cajero/desbloquear/"

    def test_rapid_4_incorrect_pins_recorded_and_terminal_unlocked(self):
        """
        Adversarial Test 1.1: Rapidly send 4 incorrect PINs.
        - Verify attempts are incremented from 1 to 4.
        - Verify each response returns HTTP 401 Unauthorized with correct remaining attempts.
        - Verify cashier and terminal remain unlocked (bloqueado=False).
        """
        for i in range(1, 5):
            wrong_pin = f"999{i}"
            resp = self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": wrong_pin}),
                content_type="application/json"
            )
            self.assertEqual(
                resp.status_code, 401,
                f"Attempt {i} expected HTTP 401, got {resp.status_code}"
            )
            data = resp.json()
            self.assertFalse(data["success"])
            self.assertFalse(data["bloqueado"])
            self.assertEqual(data["intentos_restantes"], 5 - i)

            # Empirical database verification
            self.cajero.refresh_from_db()
            self.assertEqual(self.cajero.intentos_fallidos, i)
            self.assertFalse(self.cajero.is_locked())
            self.assertIsNone(self.cajero.bloqueado_hasta)

            # Terminal session must NOT be locked yet
            session_lock = self.client.session.get('terminal_bloqueado_hasta')
            self.assertIsNone(session_lock)

    def test_5th_incorrect_pin_triggers_http_423_and_60s_lockout(self):
        """
        Adversarial Test 1.2: Send 5th incorrect PIN.
        - Verify HTTP 423 Locked is returned.
        - Verify response reports bloqueado=True and ~60s lockout timer.
        - Verify cashier record in DB has bloqueado_hasta set ~60s into future.
        - Verify terminal session has terminal_bloqueado_hasta set.
        """
        # Exhaust first 4 attempts
        for i in range(4):
            self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "0000"}),
                content_type="application/json"
            )

        # 5th attempt: the threshold trigger
        resp_5 = self.client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "0000"}),
            content_type="application/json"
        )
        self.assertEqual(
            resp_5.status_code, 423,
            f"5th attempt expected HTTP 423 Locked, got {resp_5.status_code}"
        )
        data_5 = resp_5.json()
        self.assertFalse(data_5["success"])
        self.assertTrue(data_5["bloqueado"])
        self.assertEqual(data_5["intentos_restantes"], 0)
        self.assertGreaterEqual(data_5["segundos_restantes"], 58)
        self.assertLessEqual(data_5["segundos_restantes"], 60)

        # Empirical database verification
        self.cajero.refresh_from_db()
        self.assertEqual(self.cajero.intentos_fallidos, 5)
        self.assertTrue(self.cajero.is_locked())
        self.assertIsNotNone(self.cajero.bloqueado_hasta)
        diff_seconds = (self.cajero.bloqueado_hasta - timezone.now()).total_seconds()
        self.assertGreater(diff_seconds, 55.0)
        self.assertLessEqual(diff_seconds, 60.0)

        # Session lock verification
        self.assertIsNotNone(self.client.session.get('terminal_bloqueado_hasta'))

    def test_6th_attempt_immediate_rejection_http_423_with_remaining_seconds(self):
        """
        Adversarial Test 1.3: Send 6th attempt immediately during lockout.
        - Immediate rejection with HTTP 423 Locked.
        - Rejection holds even if the attacker submits the CORRECT PIN ("4321").
        - Rejection holds if the attacker submits another WRONG PIN ("1111").
        - Remaining seconds count down accurately.
        """
        # Trigger lockout with 5 failed attempts
        for _ in range(5):
            self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "9999"}),
                content_type="application/json"
            )

        # 6th attempt with CORRECT PIN -> MUST be rejected with HTTP 423!
        resp_correct_during_lockout = self.client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "4321"}),
            content_type="application/json"
        )
        self.assertEqual(
            resp_correct_during_lockout.status_code, 423,
            "Correct PIN submitted during active lockout must return HTTP 423 Locked"
        )
        data_correct = resp_correct_during_lockout.json()
        self.assertTrue(data_correct["bloqueado"])
        self.assertGreater(data_correct["segundos_restantes"], 0)

        # 7th attempt with WRONG PIN -> MUST be rejected with HTTP 423!
        resp_wrong_during_lockout = self.client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "0000"}),
            content_type="application/json"
        )
        self.assertEqual(resp_wrong_during_lockout.status_code, 423)
        self.assertTrue(resp_wrong_during_lockout.json()["bloqueado"])

    def test_lockout_enforced_across_new_sessions_targeting_same_cashier(self):
        """
        Adversarial Test 1.4: Cross-Session Lockout Enforcement.
        An attacker who drops cookies, opens an incognito window, or launches a new HTTP client
        targeting the same locked cashier MUST still be blocked by database-level enforcement.
        """
        # Session 1 triggers the 60s lockout
        for _ in range(5):
            self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "9999"}),
                content_type="application/json"
            )
        self.cajero.refresh_from_db()
        self.assertTrue(self.cajero.is_locked())

        # Session 2: A brand new Client instance (zero cookies, zero session state)
        new_client = Client()
        self.assertNotIn('terminal_bloqueado_hasta', new_client.session)

        # Session 2 attempts unlock with the CORRECT PIN
        resp_new_session = new_client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "4321"}),
            content_type="application/json"
        )
        self.assertEqual(
            resp_new_session.status_code, 423,
            "Fresh session without cookies must be blocked with HTTP 423 when targeting locked cashier"
        )
        data_new = resp_new_session.json()
        self.assertTrue(data_new["bloqueado"])
        self.assertGreater(data_new["segundos_restantes"], 0)

        # Verify that Session 2 also adopts the session-level terminal lockout
        self.assertIsNotNone(new_client.session.get('terminal_bloqueado_hasta'))

        # Session 3: Another fresh client attempts unlock with an INCORRECT PIN
        another_client = Client()
        resp_session_3 = another_client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "0000"}),
            content_type="application/json"
        )
        self.assertEqual(resp_session_3.status_code, 423)
        self.assertTrue(resp_session_3.json()["bloqueado"])

    def test_physical_terminal_session_lockout_blocks_other_cashiers(self):
        """
        Adversarial Test 1.5: Terminal Session Defense-in-Depth.
        When a terminal is locked after 5 failed attempts on Cashier A, an attacker on the same
        browser/terminal session CANNOT immediately switch to try brute-forcing Cashier B.
        """
        # Attacker locks Cashier A on self.client
        for _ in range(5):
            self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "9999"}),
                content_type="application/json"
            )

        # Cashier B is completely unlocked in the database
        self.assertFalse(self.cajero_dos.is_locked())
        self.assertEqual(self.cajero_dos.intentos_fallidos, 0)

        # Attacker attempts to unlock Cashier B using the same physical terminal session
        resp_switch = self.client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero_dos.id, "pin": "8888"}),
            content_type="application/json"
        )
        self.assertEqual(
            resp_switch.status_code, 423,
            "Physical terminal locked session must reject attempts on ANY cashier until timeout expires"
        )
        self.assertTrue(resp_switch.json()["bloqueado"])
        self.assertIn("Terminal bloqueada", resp_switch.json()["error"])

    def test_adversarial_malformed_and_injection_pins(self):
        """
        Adversarial Test 1.6: Hostile and Malformed PIN payloads.
        Assert SQL injection, string length abuse, empty strings, and non-numeric inputs
        are handled gracefully (400 or 401) and NEVER trigger unhandled 500 exceptions.
        Also verify that malformed PINs count towards the 5-attempt rate-limit threshold.
        """
        hostile_payloads = [
            {"cajero_id": self.cajero.id, "pin": "' OR '1'='1"},
            {"cajero_id": self.cajero.id, "pin": "admin'--"},
            {"cajero_id": self.cajero.id, "pin": "abcd"},
            {"cajero_id": self.cajero.id, "pin": "123"},
            {"cajero_id": self.cajero.id, "pin": "12345"},
            {"cajero_id": self.cajero.id, "pin": ""},
            {"cajero_id": self.cajero.id, "pin": "    "},
            {"cajero_id": self.cajero.id, "pin": None},
            {"cajero_id": "malicious_string", "pin": "1234"},
            {"cajero_id": -99, "pin": "1234"},
            {"cajero_id": 999999, "pin": "1234"},
            {},
        ]

        # Test each malformed payload independently with cleared attempts
        for payload in hostile_payloads:
            self.cajero.limpiar_intentos_fallidos()
            self.client.session.pop('terminal_bloqueado_hasta', None)
            resp = self.client.post(
                self.url_desbloquear,
                data=json.dumps(payload),
                content_type="application/json"
            )
            self.assertIn(
                resp.status_code, [400, 401, 404],
                f"Payload {payload} caused unexpected HTTP status {resp.status_code}"
            )
            try:
                data = resp.json()
                self.assertFalse(data["success"])
            except Exception as e:
                self.fail(f"Payload {payload} returned non-JSON response: {e}")

        # Now test that 5 consecutive malformed PIN attempts trigger lockout (HTTP 423)
        self.cajero.limpiar_intentos_fallidos()
        self.client.session.pop('terminal_bloqueado_hasta', None)
        for _ in range(4):
            self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "malicious_pin"}),
                content_type="application/json"
            )
        resp_5_malformed = self.client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "malicious_pin"}),
            content_type="application/json"
        )
        self.assertEqual(resp_5_malformed.status_code, 423)
        self.assertTrue(resp_5_malformed.json()["bloqueado"])

    def test_successful_pin_resets_failed_attempts_counter(self):
        """
        Adversarial Test 1.7: Successful PIN resets failed attempts counter.
        If a user fails 3 times, then enters the correct PIN on the 4th attempt,
        intentos_fallidos resets to 0 and session is established.
        """
        for i in range(3):
            self.client.post(
                self.url_desbloquear,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "0000"}),
                content_type="application/json"
            )
        self.cajero.refresh_from_db()
        self.assertEqual(self.cajero.intentos_fallidos, 3)

        # 4th attempt with correct PIN
        resp = self.client.post(
            self.url_desbloquear,
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "4321"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        # Empirical database verification: counter cleared
        self.cajero.refresh_from_db()
        self.assertEqual(self.cajero.intentos_fallidos, 0)
        self.assertFalse(self.cajero.is_locked())
        self.assertIsNone(self.cajero.bloqueado_hasta)

        # Session verification
        self.assertEqual(self.client.session.get('cajero_id'), self.cajero.id)
        self.assertFalse(self.client.session.get('cajero_bloqueado'))


class TestAdversarialSupervisorOverride(TestCase):
    """
    Dimension 2: Supervisor Lockout Override & RBAC Protection.
    Verifies that only authenticated administrators/supervisors can clear an active lockout,
    and that supervisor override immediately unlocks both the database record and terminal session.
    """

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(
            nombre="Bistro Santiago",
            slug="bistro-santiago",
            direccion="Calle Central 100",
            activo=True
        )
        self.restaurante_otro = Restaurante.objects.create(
            nombre="Café del Parque",
            slug="cafe-del-parque",
            direccion="Parque Forestal 200",
            activo=True
        )

        self.supervisor = User.objects.create_superuser(
            username="supervisor_m7", password="password_super_123", email="super@bistro.cl"
        )
        self.standard_user = User.objects.create_user(
            username="empleado_sin_privilegios", password="password_emp_123"
        )

        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Camila Vega",
            codigo_empleado="CAJ-801"
        )
        self.cajero.set_pin("7777")
        self.cajero.save()

        self.url_supervisor_unlock = "/api/cajero/desbloquear-supervisor/"
        self.url_tenant_supervisor_unlock = f"/r/{self.restaurante.slug}/api/cajero/desbloquear-supervisor/"

    def _lock_cashier(self):
        """Helper to lock cashier via 5 consecutive failed attempts."""
        url_unlock = "/api/cajero/desbloquear/"
        for _ in range(5):
            self.client.post(
                url_unlock,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": "0000"}),
                content_type="application/json"
            )
        self.cajero.refresh_from_db()
        self.assertTrue(self.cajero.is_locked())
        self.assertIsNotNone(self.client.session.get('terminal_bloqueado_hasta'))

    def test_supervisor_unlock_with_admin_credentials_clears_db_and_session(self):
        """
        Adversarial Test 2.1: Call supervisor unlock endpoint with admin credentials.
        - Cashier in DB is immediately cleared: intentos_fallidos=0, bloqueado_hasta=None, is_locked()=False.
        - Terminal session lockout (terminal_bloqueado_hasta) is removed.
        - Cashier can immediately authenticate with their correct PIN.
        """
        self._lock_cashier()

        # Login as supervisor (is_staff=True)
        self.client.login(username="supervisor_m7", password="password_super_123")

        # Call supervisor unlock endpoint
        resp = self.client.post(
            self.url_supervisor_unlock,
            data=json.dumps({"cajero_id": self.cajero.id}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        # Empirical database verification
        self.cajero.refresh_from_db()
        self.assertFalse(self.cajero.is_locked())
        self.assertEqual(self.cajero.intentos_fallidos, 0)
        self.assertIsNone(self.cajero.bloqueado_hasta)

        # Empirical session verification
        self.assertNotIn('terminal_bloqueado_hasta', self.client.session)

        # Cashier can now unlock immediately with their 4-digit PIN ("7777")
        resp_cashier = self.client.post(
            "/api/cajero/desbloquear/",
            data=json.dumps({"cajero_id": self.cajero.id, "pin": "7777"}),
            content_type="application/json"
        )
        self.assertEqual(resp_cashier.status_code, 200)
        self.assertTrue(resp_cashier.json()["success"])
        self.assertEqual(self.client.session.get('cajero_id'), self.cajero.id)

    def test_supervisor_unlock_canonical_tenant_endpoint(self):
        """
        Adversarial Test 2.2: Supervisor unlock through canonical tenant route /r/<slug>/...
        Verifies tenant-routed supervisor unlock succeeds for matching tenant.
        """
        self._lock_cashier()
        self.client.login(username="supervisor_m7", password="password_super_123")

        resp = self.client.post(
            self.url_tenant_supervisor_unlock,
            data=json.dumps({"cajero_id": self.cajero.id}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        self.cajero.refresh_from_db()
        self.assertFalse(self.cajero.is_locked())

    def test_supervisor_unlock_rejected_for_unauthenticated_and_non_staff_users(self):
        """
        Adversarial Test 2.3: Non-staff or unauthenticated users cannot call supervisor unlock.
        - Unauthenticated request: returns HTTP 403.
        - Authenticated non-staff user: returns HTTP 403.
        - Cashier in DB remains locked!
        """
        self._lock_cashier()

        # 1. Unauthenticated request with cashier session
        client_unauth = Client()
        # Set cashier session to trigger admin_required branch
        session = client_unauth.session
        session['cajero_id'] = self.cajero.id
        session.save()

        resp_unauth = client_unauth.post(
            self.url_supervisor_unlock,
            data=json.dumps({"cajero_id": self.cajero.id}),
            content_type="application/json"
        )
        self.assertEqual(resp_unauth.status_code, 403)
        self.cajero.refresh_from_db()
        self.assertTrue(self.cajero.is_locked())

        # 2. Authenticated non-staff user
        client_non_staff = Client()
        client_non_staff.login(username="empleado_sin_privilegios", password="password_emp_123")

        resp_non_staff = client_non_staff.post(
            self.url_supervisor_unlock,
            data=json.dumps({"cajero_id": self.cajero.id}),
            content_type="application/json"
        )
        self.assertEqual(resp_non_staff.status_code, 403)
        self.cajero.refresh_from_db()
        self.assertTrue(self.cajero.is_locked())

    def test_supervisor_unlock_cross_tenant_cashier_rejected(self):
        """
        Adversarial Test 2.4: Cross-tenant supervisor unlock attack.
        A supervisor attempting to unlock Cashier A (belonging to Bistro Santiago)
        via the URL of Café del Parque (/r/cafe-del-parque/...) MUST be rejected (HTTP 404).
        """
        self._lock_cashier()
        self.client.login(username="supervisor_m7", password="password_super_123")

        url_rival_supervisor = f"/r/{self.restaurante_otro.slug}/api/cajero/desbloquear-supervisor/"
        resp = self.client.post(
            url_rival_supervisor,
            data=json.dumps({"cajero_id": self.cajero.id}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)
        # Cashier remains locked in their home restaurant
        self.cajero.refresh_from_db()
        self.assertTrue(self.cajero.is_locked())

    def test_supervisor_unlock_nonexistent_cashier_graceful_handling(self):
        """
        Adversarial Test 2.5: Calling supervisor unlock on non-existent cashier ID.
        Must return HTTP 404 cleanly, without 500 error.
        """
        self.client.login(username="supervisor_m7", password="password_super_123")
        resp = self.client.post(
            self.url_supervisor_unlock,
            data=json.dumps({"cajero_id": 999999}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["success"])


class TestAdversarialLayer1TokenTamperingAndRevocation(TestCase):
    """
    Dimension 3: Layer 1 Terminal Pairing Security, Token Tampering & Hybrid Revocation.
    Verifies that:
    1. Cryptographically tampered terminal cookies are rejected cleanly without crashes.
    2. Revoking the terminal in the database (Terminal.activo=False) immediately invalidates
       the token regardless of cryptographic signature validity (hybrid revocation).
    """

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(
            nombre="Pizzería Romana",
            slug="pizzeria-romana",
            direccion="Av. Italia 456, Santiago",
            activo=True
        )
        self.restaurante_otro = Restaurante.objects.create(
            nombre="Gelatería Bella",
            slug="gelateria-bella",
            direccion="Av. Italia 789, Santiago",
            activo=True
        )

        self.terminal = Terminal.objects.create(
            restaurante=self.restaurante,
            nombre="Terminal POS Mostrador 1",
            tipo=Terminal.TIPO_POS,
            activo=True
        )

        self.valid_token = generate_terminal_token(
            restaurante_id=self.restaurante.id,
            restaurante_slug=self.restaurante.slug,
            terminal_uuid=str(self.terminal.uuid),
            nombre=self.terminal.nombre,
            tipo=self.terminal.tipo
        )

        self.plato = Plato.objects.create(
            restaurante=self.restaurante,
            nombre="Pizza Margherita",
            valor=8500.0
        )

    def test_layer1_token_tampered_signature_rejected_without_crash(self):
        """
        Adversarial Test 3.1: Modify signature of mainch_terminal_token cookie.
        - Verify token rejected cleanly by validate_terminal_token without unhandled exceptions.
        - Verify get_active_terminal returns None.
        - Verify protected POS request returns HTTP 401 Unauthorized or redirects without 500 error.
        """
        parts = self.valid_token.rsplit(":", 1)
        sig = parts[1]
        inverted_sig = ("B" if sig[0] == "A" else "A") + sig[1:]
        tampered_sig_token = f"{parts[0]}:{inverted_sig}"

        tampered_variants = [
            # 1. Signature altered by replacing trailing bytes
            self.valid_token[:-6] + "ABCDEF",
            # 2. Inverted head character in signature
            tampered_sig_token,
            # 3. Truncated token
            self.valid_token[:30],
            # 4. Null byte injection
            self.valid_token + "\x00",
            # 5. Forged payload signed with wrong salt
            signing.dumps(
                {
                    "restaurante_id": self.restaurante.id,
                    "terminal_uuid": str(self.terminal.uuid),
                },
                salt="wrong_salt",
            ),
            # 6. Completely bogus string
            "totally.fake.and.unauthorized.terminal.token",
            # 7. Empty string
            "",
        ]

        rf = RequestFactory()

        for idx, tampered in enumerate(tampered_variants):
            # A. Low-level cryptographic validation check
            result = validate_terminal_token(tampered)
            self.assertIsNone(
                result,
                f"Tampered variant #{idx} '{tampered[:20]}' should return None"
            )

            # B. Middleware / Request validation check
            req = rf.get(f"/r/{self.restaurante.slug}/pos/")
            req.COOKIES[TERMINAL_COOKIE_NAME] = tampered
            active = get_active_terminal(req)
            self.assertIsNone(
                active,
                f"Tampered variant #{idx} must not produce an active terminal"
            )

            # C. HTTP Client End-to-End verification: protected POS endpoint
            client_tampered = Client()
            client_tampered.cookies[TERMINAL_COOKIE_NAME] = tampered
            resp = client_tampered.get(
                f"/r/{self.restaurante.slug}/pos/",
                HTTP_ACCEPT="application/json"
            )
            self.assertEqual(
                resp.status_code, 401,
                f"Tampered variant #{idx} must receive HTTP 401, got {resp.status_code}"
            )
            data = resp.json()
            self.assertFalse(data["success"])
            self.assertEqual(data.get("code"), "TERMINAL_NOT_PAIRED")

    def test_layer1_hybrid_revocation_db_terminal_deactivation(self):
        """
        Adversarial Test 3.2: Terminal Revocation (Hybrid Revocation).
        - With terminal active: token is valid and POS is accessible.
        - Set Terminal.activo = False in database (e.g. tablet lost or stolen).
        - Verify token is IMMEDIATELY rejected even though cryptographic signature is 100% intact.
        - Verify re-enabling Terminal.activo = True immediately restores access without re-pairing.
        """
        # Step 1: Pre-condition: Terminal is active and accessible
        self.client.cookies[TERMINAL_COOKIE_NAME] = self.valid_token
        rf = RequestFactory()
        req_active = rf.get(f"/r/{self.restaurante.slug}/pos/")
        req_active.COOKIES[TERMINAL_COOKIE_NAME] = self.valid_token

        payload_active = get_active_terminal(req_active)
        self.assertIsNotNone(payload_active)
        self.assertEqual(payload_active["terminal"].id, self.terminal.id)

        # Step 2: Revocation ceremony: mark Terminal inactive in DB
        self.terminal.activo = False
        self.terminal.save()
        self.terminal.refresh_from_db()
        self.assertFalse(self.terminal.activo)

        # Step 3: Verify cryptographic signature alone is NOT enough (hybrid check fails)
        crypto_payload = validate_terminal_token(self.valid_token)
        self.assertIsNotNone(
            crypto_payload,
            "Cryptographic signature itself remains valid (stateless property)"
        )

        # Hybrid validation check
        req_revoked = rf.get(f"/r/{self.restaurante.slug}/pos/")
        req_revoked.COOKIES[TERMINAL_COOKIE_NAME] = self.valid_token
        payload_revoked = get_active_terminal(req_revoked)
        self.assertIsNone(
            payload_revoked,
            "Hybrid validation must return None when Terminal.activo=False in database"
        )

        # HTTP Client End-to-End check on POS endpoint
        resp_revoked = self.client.get(
            f"/r/{self.restaurante.slug}/pos/",
            HTTP_ACCEPT="application/json"
        )
        self.assertEqual(
            resp_revoked.status_code, 401,
            "Revoked terminal must be rejected with HTTP 401 on POS access"
        )
        self.assertEqual(resp_revoked.json().get("code"), "TERMINAL_NOT_PAIRED")

        # Step 4: Reactivate terminal: access is immediately restored
        self.terminal.activo = True
        self.terminal.save()

        req_reactivated = rf.get(f"/r/{self.restaurante.slug}/pos/")
        req_reactivated.COOKIES[TERMINAL_COOKIE_NAME] = self.valid_token
        payload_reactivated = get_active_terminal(req_reactivated)
        self.assertIsNotNone(payload_reactivated)
        self.assertEqual(payload_reactivated["terminal"].id, self.terminal.id)

    def test_layer1_terminal_uuid_spoofing_rejected(self):
        """
        Adversarial Test 3.3: Terminal UUID spoofing.
        An attacker generates a signed token with a random UUID that does NOT exist in the
        Terminal table. Hybrid DB check MUST reject it.
        """
        fake_uuid = str(uuid.uuid4())
        fake_token = generate_terminal_token(
            restaurante_id=self.restaurante.id,
            restaurante_slug=self.restaurante.slug,
            terminal_uuid=fake_uuid
        )

        rf = RequestFactory()
        req = rf.get(f"/r/{self.restaurante.slug}/pos/")
        req.COOKIES[TERMINAL_COOKIE_NAME] = fake_token
        active = get_active_terminal(req)
        self.assertIsNone(
            active,
            "Token with non-existent Terminal UUID in database must be rejected"
        )

    def test_layer1_deactivated_restaurant_revokes_token(self):
        """
        Adversarial Test 3.4: Target Restaurant Deactivation.
        If a restaurant tenant is deactivated (activo=False), all its terminal tokens
        must be rejected immediately.
        """
        self.restaurante.activo = False
        self.restaurante.save()

        rf = RequestFactory()
        req = rf.get(f"/r/{self.restaurante.slug}/pos/")
        req.COOKIES[TERMINAL_COOKIE_NAME] = self.valid_token
        active = get_active_terminal(req)
        self.assertIsNone(
            active,
            "Terminal token for inactive restaurant tenant must be rejected"
        )

    def test_layer1_cross_tenant_token_replay_rejected_http_403(self):
        """
        Adversarial Test 3.5: Cross-Tenant Token Replay Attack.
        A valid terminal token paired with Restaurant A used against Restaurant B's
        canonical POS endpoint MUST be rejected with HTTP 403 Forbidden (TENANT_MISMATCH).
        """
        self.client.cookies[TERMINAL_COOKIE_NAME] = self.valid_token
        url_rival_pos = f"/r/{self.restaurante_otro.slug}/pos/"

        resp = self.client.get(url_rival_pos, HTTP_ACCEPT="application/json")
        self.assertEqual(
            resp.status_code, 403,
            "Replaying Restaurant A terminal token against Restaurant B must return HTTP 403"
        )
        self.assertEqual(resp.json().get("code"), "TENANT_MISMATCH")


class TestAdversarialRapidBruteForceMatrix(TestCase):
    """
    Dimension 4: Rapid Automated Dictionary Brute-Force Attack Simulation.
    Simulates an automated script attempting to brute-force a cashier's 4-digit PIN
    by sequentially sending 15 rapid guesses from a dictionary of common PINs.
    Verifies that the rate-limiting defense immediately shuts down the attack on attempt 5.
    """

    def setUp(self):
        self.client = Client()
        self.restaurante = Restaurante.objects.create(
            nombre="Burger Express",
            slug="burger-express",
            direccion="Av. Los Leones 890",
            activo=True
        )
        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Cristóbal Concha",
            codigo_empleado="CAJ-999"
        )
        self.cajero.set_pin("6543")
        self.cajero.save()
        self.url = "/api/cajero/desbloquear/"

    def test_rapid_automated_dictionary_attack_shutdown_at_attempt_5(self):
        """
        Adversarial Test 4.1: Automated dictionary attack iterating 15 PINs.
        - Guesses 1-4: returned 401 Unauthorized (attempt counter accurately increments).
        - Guess 5: returns HTTP 423 Locked (60-second penalty applied).
        - Guesses 6-15: all immediately blocked with HTTP 423 Locked without checking PIN.
        - Out of 15 automated attack guesses, exactly 5 were evaluated before total lockout.
        """
        dictionary = [
            "0000", "1234", "1111", "2222",  # Guesses 1 to 4 -> 401
            "3333",                          # Guess 5 -> 423 (Lockout triggered)
            "4444", "5555", "6666", "7777",  # Guesses 6 to 9 -> 423
            "8888", "9999", "1212", "6543",  # Guesses 10 to 13 -> 423 (including correct PIN!)
            "9876", "2580"                   # Guesses 14 to 15 -> 423
        ]

        status_codes = []
        for pin in dictionary:
            resp = self.client.post(
                self.url,
                data=json.dumps({"cajero_id": self.cajero.id, "pin": pin}),
                content_type="application/json"
            )
            status_codes.append(resp.status_code)

        # Verify exact behavior per attempt phase
        self.assertEqual(status_codes[:4], [401, 401, 401, 401], "First 4 guesses must return 401")
        self.assertEqual(status_codes[4], 423, "5th guess must trigger HTTP 423 Lockout")
        self.assertEqual(
            status_codes[5:], [423] * 10,
            "Guesses 6-15 must all be immediately rejected with HTTP 423 Locked"
        )

        # Verify database state after dictionary attack
        self.cajero.refresh_from_db()
        self.assertTrue(self.cajero.is_locked())
        self.assertEqual(self.cajero.intentos_fallidos, 5)
        self.assertIsNotNone(self.cajero.bloqueado_hasta)
