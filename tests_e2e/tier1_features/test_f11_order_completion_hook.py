"""
Tier 1 — Feature F11: Order Completion Hook Integration.
Verifies confirming an order via HTTP POST triggers stock deduction and returns stock alerts.
"""

import json
from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestF11OrderCompletionHook(E2ETestCase):
    """Test suite for Feature F11: Order Completion Hook."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F11")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F11")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F11")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F11")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F11")

        self.insumo = self.Insumo.objects.create(
            codigo="INS-PANHOOK", nombre="Pan Hook", unidad_medida="un",
            stock_actual=Decimal("20.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("300.000")
        )
        self.plato = self.Plato.objects.create(nombre="Sandwich Hook", valor=4000)
        self.RecetaItem.objects.create(plato=self.plato, insumo=self.insumo, cantidad=Decimal("1.000"))

        self.orden = self.Orden.objects.create(cliente="Cliente Hook", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=self.orden, plato=self.plato, cantidad=3)

    def test_f11_01_confirm_endpoint_updates_order_state(self):
        """TC-F11-01: Confirming order transitions status to 'Completada'."""
        client = self.get_client()
        payload = {"tipo_pago": "Efectivo", "descuento": 0}
        resp = client.post(
            f"/pedidos/{self.orden.id}/confirmar/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200, "[F11] Confirm endpoint must return 200 OK")
        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, "Completada", "[F11] Order state must become 'Completada'")

    def test_f11_02_confirm_triggers_stock_deduction(self):
        """TC-F11-02: Confirming order deducts inventory stock (20 - 3 = 17)."""
        client = self.get_client()
        payload = {"tipo_pago": "Efectivo", "descuento": 0}
        client.post(
            f"/pedidos/{self.orden.id}/confirmar/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.insumo.refresh_from_db()
        self.assertEqual(self.insumo.stock_actual, Decimal("17.000"), "[F11] Stock must be deducted upon order confirmation")

    def test_f11_03_confirm_marks_stock_descontado_true(self):
        """TC-F11-03: Order stock_descontado flag is set to True upon completion."""
        client = self.get_client()
        payload = {"tipo_pago": "Efectivo", "descuento": 0}
        client.post(
            f"/pedidos/{self.orden.id}/confirmar/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.orden.refresh_from_db()
        self.assertTrue(self.orden.stock_descontado, "[F11] stock_descontado must be set to True")

    def test_f11_04_confirm_response_includes_json_status(self):
        """TC-F11-04: Confirmation HTTP response returns JSON with success=True."""
        client = self.get_client()
        payload = {"tipo_pago": "Tarjeta", "descuento": 1000}
        resp = client.post(
            f"/pedidos/{self.orden.id}/confirmar/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        data = resp.json()
        self.assertTrue(data.get("success"), "[F11] Response JSON should have success=True")

    def test_f11_05_repeated_confirmation_idempotent(self):
        """TC-F11-05: Submitting confirmation twice does not double-deduct stock."""
        client = self.get_client()
        payload = {"tipo_pago": "Efectivo", "descuento": 0}
        client.post(f"/pedidos/{self.orden.id}/confirmar/", data=json.dumps(payload), content_type="application/json")
        self.insumo.refresh_from_db()
        stock_first = self.insumo.stock_actual

        client.post(f"/pedidos/{self.orden.id}/confirmar/", data=json.dumps(payload), content_type="application/json")
        self.insumo.refresh_from_db()
        self.assertEqual(self.insumo.stock_actual, stock_first, "[F11] Second confirmation should not alter stock")
