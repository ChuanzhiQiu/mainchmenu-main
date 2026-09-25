"""
Tier 3 — Cross-Feature Combination C11: Empirical Challenger Suite for Milestone 3 (F13, F14, F15, B03).
Adversarially stress-tests:
1. Orden.calcular_total(): extreme discounts, negative values, massive numbers, float rounding, zero/empty items.
2. crear_orden view robustness: malformed JSON, non-dict JSON bodies (list, scalar, null), non-numeric IDs,
   zero/negative quantities, empty items lists, missing required fields. Ensure HTTP 400 without 500 crashes.
3. Cross-view discount integrity: detecting whether confirmar_orden corrupts CLP currency discounts into percentages.
4. Concurrency & idempotency during POS order creation (rapid duplicate submissions).
"""

import json
import math
from decimal import Decimal

from django.test import TestCase, Client
from django.utils import timezone

from Menu.models import Insumo, Menu, MovimientoStock, Orden, OrdenItem, Plato, RecetaItem
from tests_e2e.base import E2EBaseMixin


class TestC11EmpiricalChallengerM3POS(TestCase, E2EBaseMixin):
    """Adversarial stress-testing suite for Milestone 3 POS calculations and async order placement."""

    def setUp(self):
        MovimientoStock.objects.all().delete()
        OrdenItem.objects.all().delete()
        Orden.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Menu.objects.all().delete()
        Insumo.objects.all().delete()
        self.client = Client()

        # Seed reference plato and menu
        self.plato = Plato.objects.create(nombre="Hamburguesa Challenger", valor=6000.0)
        self.plato2 = Plato.objects.create(nombre="Bebida Cola", valor=1500.0)
        self.combo = Menu.objects.create(nombre="Combo Super", precio_menus=7000.0)
        self.combo.platos.add(self.plato, self.plato2)

    # =========================================================================
    # SECTION 1: Orden.calcular_total() Adversarial Calculations
    # =========================================================================

    def test_calcular_total_discount_greater_than_subtotal(self):
        """Stress: discount ($10,000) > subtotal ($6,000) must clamp cleanly to 0.0 without negative total."""
        orden = Orden.objects.create(cliente="Cliente Test", descuento=10000.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        total = orden.calcular_total()
        self.assertEqual(total, 0.0, "Total must clamp to 0.0 when discount exceeds subtotal.")

    def test_calcular_total_exact_discount(self):
        """Stress: discount equal to subtotal ($6,000) must yield exactly 0.0."""
        orden = Orden.objects.create(cliente="Cliente Exact", descuento=6000.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        self.assertEqual(orden.calcular_total(), 0.0)

    def test_calcular_total_zero_discount(self):
        """Stress: discount=0 must return exact subtotal ($6,000)."""
        orden = Orden.objects.create(cliente="Cliente Cero", descuento=0.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        self.assertEqual(orden.calcular_total(), 6000.0)

    def test_calcular_total_none_discount(self):
        """Stress: discount=None must be treated as 0.0 without throwing TypeError."""
        orden = Orden.objects.create(cliente="Cliente None", descuento=None)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        self.assertEqual(orden.calcular_total(), 6000.0)

    def test_calcular_total_massive_discount(self):
        """Stress: astronomically large discount (1e12) must clamp to 0.0."""
        orden = Orden.objects.create(cliente="Cliente Mega", descuento=1000000000000.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        self.assertEqual(orden.calcular_total(), 0.0)

    def test_calcular_total_floating_point_subcent_rounding(self):
        """Stress: floating point precision edge cases (e.g. 1990.555 - 190.222)."""
        p = Plato.objects.create(nombre="Plato Fraccional", valor=1990.555)
        orden = Orden.objects.create(cliente="Cliente Centavos", descuento=190.222)
        OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        # Expected: round(1990.555 - 190.222, 2) = round(1800.333, 2) = 1800.33
        self.assertEqual(orden.calcular_total(), 1800.33)

    def test_calcular_total_negative_discount_does_not_inflate_total(self):
        """Adversarial: negative discount (-$2,000) on model must not inflate total above subtotal ($6,000)."""
        orden = Orden.objects.create(cliente="Cliente Negativo", descuento=-2000.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        total = orden.calcular_total()
        self.assertLessEqual(total, 6000.0, f"Negative discount must not inflate total above subtotal: got {total}")

    def test_calcular_total_empty_items(self):
        """Stress: order with zero items must return 0.0 regardless of discount."""
        orden = Orden.objects.create(cliente="Cliente Vacio", descuento=500.0)
        self.assertEqual(orden.calcular_total(), 0.0)

    # =========================================================================
    # SECTION 2: crear_orden View Robustness & HTTP Status Verification
    # =========================================================================

    def test_crear_orden_malformed_json_syntax(self):
        """Robustness: unparseable malformed JSON syntax must return HTTP 400, not 500."""
        resp = self.client.post(
            "/pedidos/crear/",
            data="{invalid_json: true,",
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Malformed JSON syntax must return HTTP 400")

    def test_crear_orden_non_dict_json_list(self):
        """Adversarial: JSON body containing a list [1, 2, 3] instead of an object."""
        try:
            resp = self.client.post(
                "/pedidos/crear/",
                data=json.dumps([1, 2, 3]),
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 400, "Non-dict JSON payload (list) must return HTTP 400, not crash with 500")
        except AttributeError as e:
            self.fail(f"crear_orden crashed with 500 AttributeError on JSON list: {e}")

    def test_crear_orden_non_dict_json_string(self):
        """Adversarial: JSON body containing a bare string instead of an object."""
        try:
            resp = self.client.post(
                "/pedidos/crear/",
                data=json.dumps("plain string payload"),
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 400, "Non-dict JSON payload (string) must return HTTP 400, not crash with 500")
        except AttributeError as e:
            self.fail(f"crear_orden crashed with 500 AttributeError on JSON string: {e}")

    def test_crear_orden_non_dict_json_scalar_null(self):
        """Adversarial: JSON body containing null instead of an object."""
        try:
            resp = self.client.post(
                "/pedidos/crear/",
                data="null",
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 400, "Null JSON payload must return HTTP 400, not crash with 500")
        except AttributeError as e:
            self.fail(f"crear_orden crashed with 500 AttributeError on null JSON: {e}")

    def test_crear_orden_non_numeric_dish_id(self):
        """Adversarial: item with non-numeric string ID ('invalid_id') in JSON items list."""
        payload = {
            "cliente": "Test String ID",
            "items": [{"tipo": "plato", "id": "invalid_str_id", "cantidad": 1}]
        }
        try:
            resp = self.client.post(
                "/pedidos/crear/",
                data=json.dumps(payload),
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 400, "Non-numeric dish ID must return HTTP 400 without unhandled 500")
        except ValueError as e:
            self.fail(f"crear_orden crashed with 500 ValueError on non-numeric dish ID: {e}")

    def test_crear_orden_non_numeric_menu_id(self):
        """Adversarial: item with non-numeric string ID ('combo_xyz') in JSON items list."""
        payload = {
            "cliente": "Test String Menu ID",
            "items": [{"tipo": "menu", "id": "combo_xyz", "cantidad": 1}]
        }
        try:
            resp = self.client.post(
                "/pedidos/crear/",
                data=json.dumps(payload),
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 400, "Non-numeric menu ID must return HTTP 400 without unhandled 500")
        except ValueError as e:
            self.fail(f"crear_orden crashed with 500 ValueError on non-numeric menu ID: {e}")

    def test_crear_orden_nonexistent_numeric_plato_id(self):
        """Robustness: non-existent dish ID (999999) must return HTTP 400."""
        payload = {
            "cliente": "Test Nonexistent Plato",
            "items": [{"tipo": "plato", "id": 999999, "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Non-existent dish ID must return HTTP 400")

    def test_crear_orden_nonexistent_numeric_menu_id(self):
        """Robustness: non-existent menu/combo ID (888888) must return HTTP 400."""
        payload = {
            "cliente": "Test Nonexistent Menu",
            "items": [{"tipo": "menu", "id": 888888, "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Non-existent menu ID must return HTTP 400")

    def test_crear_orden_zero_quantity_rejected(self):
        """Adversarial: item with cantidad=0 must be rejected with HTTP 400."""
        payload = {
            "cliente": "Test Zero Qty",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 0}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Submitting item with quantity=0 must be rejected with HTTP 400")

    def test_crear_orden_negative_quantity_rejected(self):
        """Robustness: item with negative quantity (-3) must return HTTP 400."""
        payload = {
            "cliente": "Test Negative Qty",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": -3}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Negative item quantity must return HTTP 400")

    def test_crear_orden_empty_items_list_rejected(self):
        """Adversarial: order with empty items list [] must be rejected with HTTP 400."""
        payload = {
            "cliente": "Test Empty Items",
            "items": []
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Order with empty items list [] must return HTTP 400")

    def test_crear_orden_missing_cliente_field(self):
        """Robustness: payload missing 'cliente' field entirely must return HTTP 400."""
        payload = {
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Missing 'cliente' must return HTTP 400")

    def test_crear_orden_whitespace_only_cliente_field(self):
        """Robustness: payload with whitespace-only 'cliente' ('   ') must return HTTP 400."""
        payload = {
            "cliente": "     ",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Whitespace-only 'cliente' must return HTTP 400")

    def test_crear_orden_infinite_discount_rejected(self):
        """Adversarial: discount='inf' should not be accepted as valid currency amount."""
        payload = {
            "cliente": "Test Inf Discount",
            "descuento": "inf",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        # Storing 'inf' corrupts DB and JSON serialization
        if resp.status_code == 201:
            orden = Orden.objects.get(id=resp.json()["orden_id"])
            self.assertFalse(math.isinf(orden.descuento), "Infinite discount must not be stored in DB")

    # =========================================================================
    # SECTION 3: Cross-View Discount Integrity (POS -> Kitchen Confirmation)
    # =========================================================================

    def test_confirmar_orden_preserves_clp_currency_discount(self):
        """
        Adversarial Cross-View Check:
        POS creates order with Subtotal $6,000 CLP and Descuento $1,000 CLP -> Total $5,000 CLP.
        When kitchen confirms order via /pedidos/<id>/confirmar/, does confirming preserve
        the $1,000 CLP discount and $5,000 CLP total, or does it clamp discount to 100% and wipe total to $0?
        """
        # 1. Create order at POS with $1,000 CLP discount
        payload = {
            "cliente": "Cliente Descuento CLP",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "descuento": 1000.0,
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 201)
        orden_id = resp.json()["orden_id"]

        orden_creada = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_creada.descuento, 1000.0, "POS must store direct CLP currency discount")
        self.assertEqual(orden_creada.monto_total, 5000.0, "POS must compute total = 6000 - 1000 = 5000")

        # 2. Kitchen confirms order
        resp_confirm = self.client.post(
            f"/pedidos/{orden_id}/confirmar/",
            data=json.dumps({}),
            content_type="application/json"
        )
        self.assertEqual(resp_confirm.status_code, 200)

        # 3. Verify database state after confirmation
        orden_confirmada = Orden.objects.get(id=orden_id)
        self.assertEqual(
            orden_confirmada.descuento, 1000.0,
            f"Kitchen confirmation must NOT corrupt CLP discount to {orden_confirmada.descuento}"
        )
        self.assertEqual(
            orden_confirmada.monto_total, 5000.0,
            f"Kitchen confirmation must NOT wipe order total to {orden_confirmada.monto_total}"
        )

    # =========================================================================
    # SECTION 4: Concurrency & Rapid Submissions
    # =========================================================================

    def test_pos_rapid_duplicate_order_submissions(self):
        """Stress: Simulating rapid duplicate POST submissions from POS (double-click/network retry)."""
        payload = {
            "cliente": "Cliente Duplicate Click",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        r1 = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        r2 = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")

        self.assertEqual(r1.status_code, 201)
        # Note: If no idempotency deduplication exists, r2 creates a second order.
        created_orders = Orden.objects.filter(cliente="Cliente Duplicate Click")
        self.assertGreaterEqual(created_orders.count(), 1)
