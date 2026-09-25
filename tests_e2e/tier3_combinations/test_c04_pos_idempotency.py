"""
Tier 3 — Cross-Feature Combination C04: F09 (Order Extension) + F11 (Confirm Hook) + F15 (Async POS).
Verifies that rapid duplicate async confirmation requests do not cause double stock deduction.
"""

import json
from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestC04POSIdempotency(E2ETestCase):
    """Pairwise combination: Idempotency Flags + Confirmation Hook + Async POS Submission."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C04")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C04")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C04")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="C04")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="C04")

    def test_c04_01_concurrent_double_post_idempotency(self):
        """TC-C04-01: Simulating double-click rapid confirmation on POS only executes deduction once."""
        insumo = self.Insumo.objects.create(
            codigo="INS-IDEMP", nombre="Palta Idempotente", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("4000.000")
        )
        plato = self.Plato.objects.create(nombre="Sandwich Palta", valor=5000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.200"))

        orden = self.Orden.objects.create(cliente="Mesa Doble Click", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=1)

        client = self.get_client()
        # First request
        r1 = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        # Second immediate request
        r2 = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("9.800"), "[C04] Stock must be deducted only once (10 - 0.200)")

    def test_c04_02_idempotency_flag_and_timestamp_remain_set(self):
        """TC-C04-02: After repeated confirmation calls, fecha_completada and stock_descontado remain unchanged."""
        orden = self.Orden.objects.create(cliente="Mesa Timestamp Test", canal_venta="Local", tipo_pago="Efectivo")
        client = self.get_client()
        client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        orden.refresh_from_db()
        initial_ts = orden.fecha_completada

        client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        orden.refresh_from_db()
        self.assertEqual(orden.fecha_completada, initial_ts)
        self.assertTrue(orden.stock_descontado)
