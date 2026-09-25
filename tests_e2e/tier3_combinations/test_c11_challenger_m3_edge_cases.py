"""
Tier 3 — Cross-Feature Combination C11: Extended Adversarial Edge Cases for Milestone 3 (F13, F14, F15, B03).
Stress tests:
1. Negative discounts across models and views.
2. Discount > subtotal edge cases.
3. Fractional CLP values, sub-cent precision, and floating-point stability.
4. Malformed JSON inputs and corrupt payloads to crear_orden.
"""

import json
import math
from decimal import Decimal

from django.test import TestCase, Client
from django.utils import timezone

from Menu.models import Insumo, Menu, MovimientoStock, Orden, OrdenItem, Plato, RecetaItem
from tests_e2e.base import E2EBaseMixin


class TestC11ChallengerM3ExtendedEdgeCases(TestCase, E2EBaseMixin):
    """Deep adversarial stress testing for POS calculation boundaries and crash hardening."""

    def setUp(self):
        MovimientoStock.objects.all().delete()
        OrdenItem.objects.all().delete()
        Orden.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Menu.objects.all().delete()
        Insumo.objects.all().delete()
        self.client = Client()

        self.plato_standard = Plato.objects.create(nombre="Plato Base", valor=5000.0)
        self.plato_fractional = Plato.objects.create(nombre="Plato Decimal", valor=1250.75)
        self.combo_standard = Menu.objects.create(nombre="Combo Base", precio_menus=6500.0)
        self.combo_standard.platos.add(self.plato_standard)

    # =========================================================================
    # SECTION 1: Negative Discount Stress Testing
    # =========================================================================

    def test_negative_discount_model_calculation_sanitized(self):
        """Negative discounts on Orden model must never inflate total above subtotal."""
        for neg_val in [-0.001, -1.0, -500.0, -10000.0, -1e8]:
            orden = Orden.objects.create(cliente="Cliente Neg", descuento=neg_val)
            OrdenItem.objects.create(orden=orden, plato=self.plato_standard, cantidad=2)
            total = orden.calcular_total()
            self.assertEqual(
                total, 10000.0,
                f"Negative discount {neg_val} must be clamped to 0.0, got total {total} for subtotal 10000.0"
            )

    def test_negative_discount_model_save_sanitized(self):
        """Saving an Orden with a negative discount must sanitize descuento to 0.0."""
        orden = Orden(cliente="Cliente Neg Save", descuento=-1500.0)
        orden.save()
        orden.refresh_from_db()
        self.assertEqual(orden.descuento, 0.0, "Negative discount must be clamped to 0.0 on save")

    def test_crear_orden_rejects_negative_discount_numeric(self):
        """crear_orden must reject negative numeric discount with HTTP 400."""
        payload = {
            "cliente": "Cliente Post Neg",
            "descuento": -200.0,
            "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400, "Negative numeric discount must be rejected with HTTP 400")

    def test_crear_orden_rejects_negative_discount_string(self):
        """crear_orden must reject negative string discount with HTTP 400."""
        payload = {
            "cliente": "Cliente Post Neg Str",
            "descuento": "-500.50",
            "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400, "Negative string discount must be rejected with HTTP 400")

    def test_confirmar_orden_sanitizes_negative_discount(self):
        """confirmar_orden must clamp negative discount to 0.0 and not inflate total."""
        orden = Orden.objects.create(cliente="Cliente Confirm Neg", descuento=0.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato_standard, cantidad=1)
        orden.monto_total = orden.calcular_total()
        orden.save()

        resp = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"descuento": -3000.0}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        orden.refresh_from_db()
        self.assertEqual(orden.descuento, 0.0)
        self.assertEqual(orden.monto_total, 5000.0)

    # =========================================================================
    # SECTION 2: Discount > Subtotal Edge Cases
    # =========================================================================

    def test_discount_exceeds_subtotal_model(self):
        """When discount > subtotal, total must be exactly 0.0 (never negative)."""
        orden = Orden.objects.create(cliente="Super Discount", descuento=7000.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato_standard, cantidad=1)  # Subtotal 5000
        self.assertEqual(orden.calcular_total(), 0.0)

    def test_discount_astronomical_model(self):
        """Massive discount (e.g. 1e15) must clamp total to 0.0 without overflow."""
        orden = Orden.objects.create(cliente="Astronomical Discount", descuento=1e15)
        OrdenItem.objects.create(orden=orden, plato=self.plato_standard, cantidad=1)
        self.assertEqual(orden.calcular_total(), 0.0)

    def test_crear_orden_discount_greater_than_subtotal_creates_zero_total(self):
        """crear_orden allows large discount but clamps monto_total to 0.0."""
        payload = {
            "cliente": "Cliente Regalo",
            "descuento": 99999.0,
            "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        orden_id = resp.json()["orden_id"]
        orden = Orden.objects.get(id=orden_id)
        self.assertEqual(orden.monto_total, 0.0, "Total must be 0.0 when discount exceeds subtotal")

    def test_confirmar_orden_discount_greater_than_subtotal_clamps_zero(self):
        """confirmar_orden with discount exceeding subtotal clamps monto_total to 0.0."""
        orden = Orden.objects.create(cliente="Cliente Confirm Oversize", descuento=0.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato_standard, cantidad=1)  # 5000
        resp = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"descuento": 8000.0}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        orden.refresh_from_db()
        self.assertEqual(orden.monto_total, 0.0)

    # =========================================================================
    # SECTION 3: Fractional CLP Values & Precision Stability
    # =========================================================================

    def test_fractional_prices_subtotal_and_total_rounding(self):
        """Verify 2-decimal rounding precision with fractional prices and discounts."""
        orden = Orden.objects.create(cliente="Cliente Fraccion", descuento=125.3333)
        OrdenItem.objects.create(orden=orden, plato=self.plato_fractional, cantidad=3)  # 3 * 1250.75 = 3752.25
        # Subtotal: 3752.25, Descuento: 125.3333 -> Total: 3752.25 - 125.3333 = 3626.9167 -> 3626.92
        self.assertEqual(orden.calcular_total(), 3626.92)

    def test_crear_orden_fractional_discount_preserved_and_rounded(self):
        """crear_orden with fractional discount stores float and rounds monto_total."""
        payload = {
            "cliente": "Cliente Fracc Desc",
            "descuento": 250.75,
            "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        orden = Orden.objects.get(id=resp.json()["orden_id"])
        self.assertEqual(orden.descuento, 250.75)
        self.assertEqual(orden.monto_total, 4749.25)

    def test_crear_orden_fractional_quantity_string_rejected(self):
        """crear_orden with string fractional quantity ('1.5') must return HTTP 400."""
        payload = {
            "cliente": "Cliente Qty Fracc",
            "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": "1.5"}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400, "Fractional string quantity must return HTTP 400")

    # =========================================================================
    # SECTION 4: Malformed JSON Inputs & Crash Hardening
    # =========================================================================

    def test_crear_orden_empty_body_json_content_type(self):
        """Empty POST body with application/json header must return HTTP 400, not 500."""
        resp = self.client.post(
            "/pedidos/crear/",
            data="",
            content_type="application/json",
            headers={"content-type": "application/json"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_orden_whitespace_body_json(self):
        """Whitespace POST body with application/json header must return HTTP 400, not 500."""
        resp = self.client.post("/pedidos/crear/", data="   ", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_crear_orden_truncated_json_syntax(self):
        """Truncated JSON syntax must return HTTP 400."""
        resp = self.client.post(
            "/pedidos/crear/",
            data='{"cliente": "Test", "items": [{"id": 1,',
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_orden_non_utf8_binary_body(self):
        """Non-UTF-8 binary bytes in JSON request must return HTTP 400, not 500."""
        resp = self.client.post(
            "/pedidos/crear/",
            data=b"\xff\xfe\x00\x01\x80\x90",
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_crear_orden_non_dict_json_numbers(self):
        """Top-level JSON number (integer or float) must return HTTP 400."""
        for num in [12345, 99.99]:
            resp = self.client.post("/pedidos/crear/", data=json.dumps(num), content_type="application/json")
            self.assertEqual(resp.status_code, 400)

    def test_crear_orden_non_dict_json_booleans(self):
        """Top-level JSON boolean (true/false) must return HTTP 400."""
        for b in [True, False]:
            resp = self.client.post("/pedidos/crear/", data=json.dumps(b), content_type="application/json")
            self.assertEqual(resp.status_code, 400)

    def test_crear_orden_missing_items_key(self):
        """Missing 'items' field must return HTTP 400."""
        payload = {"cliente": "Sin Items Key"}
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_crear_orden_items_is_not_a_list(self):
        """'items' provided as non-list (e.g. dict or string) must return HTTP 400."""
        for invalid_items in ["a string", 123, {"tipo": "plato", "id": 1}]:
            payload = {"cliente": "Invalid Items Type", "items": invalid_items}
            resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(resp.status_code, 400)

    def test_crear_orden_item_element_not_dict(self):
        """List element in 'items' that is not a dict must return HTTP 400."""
        for elem in [None, "string_item", 42, [1, 2]]:
            payload = {"cliente": "Corrupt Item List", "items": [elem]}
            resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(resp.status_code, 400)

    def test_crear_orden_item_missing_id_and_keys(self):
        """Item dict missing 'id' or with empty string id must return HTTP 400."""
        for item in [{}, {"tipo": "plato"}, {"tipo": "plato", "id": ""}, {"tipo": "plato", "id": None}]:
            payload = {"cliente": "Item Missing ID", "items": [item]}
            resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(resp.status_code, 400)

    def test_crear_orden_item_invalid_tipo(self):
        """Item with unsupported 'tipo' value must return HTTP 400."""
        payload = {
            "cliente": "Item Invalid Tipo",
            "items": [{"tipo": "bebida_magica", "id": 1, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_crear_orden_invalid_discount_types(self):
        """Invalid discount types (non-numeric string, NaN, Inf) must return HTTP 400."""
        for bad_desc in ["descuento_amigo", "NaN", "nan", "-inf", "inf", "Infinity", "1e309"]:
            payload = {
                "cliente": "Bad Discount",
                "descuento": bad_desc,
                "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": 1}]
            }
            resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(
                resp.status_code, 400,
                f"Discount '{bad_desc}' should be rejected with HTTP 400"
            )

    def test_crear_orden_atomic_rollback_on_second_item_failure(self):
        """If one item in items list fails validation, no partial order or items are created."""
        initial_order_count = Orden.objects.count()
        payload = {
            "cliente": "Cliente Rollback Test",
            "items": [
                {"tipo": "plato", "id": self.plato_standard.id, "cantidad": 1},
                {"tipo": "plato", "id": 999999, "cantidad": 1}  # Non-existent
            ]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Orden.objects.count(), initial_order_count, "No phantom order must be created on partial item error")

    def test_crear_orden_unicode_and_emojis(self):
        """Unicode characters and emojis in cliente and fields are handled cleanly without encoding errors."""
        payload = {
            "cliente": "🍔 Chef ñoño & Niño René — Especial 100% 🇨🇱",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"tipo": "plato", "id": self.plato_standard.id, "cantidad": 2}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        orden = Orden.objects.get(id=resp.json()["orden_id"])
        self.assertEqual(orden.cliente, "🍔 Chef ñoño & Niño René — Especial 100% 🇨🇱")

    def test_crear_orden_legacy_form_post_handling(self):
        """Standard HTML form POST with legacy platos JSON string."""
        platos_json = json.dumps({str(self.plato_standard.id): {"cantidad": 2}})
        resp = self.client.post("/pedidos/crear/", data={
            "cliente": "Cliente Legacy Form",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "platos": platos_json
        })
        self.assertEqual(resp.status_code, 302)  # Standard form redirects on success
        orden = Orden.objects.filter(cliente="Cliente Legacy Form").first()
        self.assertIsNotNone(orden)
        self.assertEqual(orden.items.first().cantidad, 2)
        self.assertEqual(orden.monto_total, 10000.0)

