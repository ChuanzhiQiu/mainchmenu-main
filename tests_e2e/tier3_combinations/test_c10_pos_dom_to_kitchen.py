"""
Tier 3 — Cross-Feature Combination C10: F12 (Layout) + F14 (Calc) + F15 (Async POS) + F16 (KDS).
Verifies complete end-to-end POS order dispatch arriving on Kitchen Display System reactively.
"""

import json
from tests_e2e.base import E2ETestCase


class TestC10POSDOMToKitchen(E2ETestCase):
    """Pairwise combination: Base Layout + Calculations + Async POS + Kitchen KDS."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C10")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C10")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C10")

    def test_c10_01_pos_order_submission_visible_in_kitchen(self):
        """TC-C10-01: Placing an order via POS immediately makes it queryable and visible on Kitchen Display."""
        plato = self.Plato.objects.create(nombre="Churrasco Especial KDS", valor=6500)
        client = self.get_client()

        # Step 1: Submit order via POS
        payload = {
            "cliente": "Mesa E2E Flow",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"tipo": "plato", "id": plato.id, "cantidad": 2}]
        }
        resp = client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertIn(resp.status_code, [200, 201, 302])

        # Step 2: Query Kitchen Board (KDS)
        kds_resp = client.get("/")
        content = kds_resp.content.decode("utf-8")
        self.assertIn("Mesa E2E Flow", content, "[C10] Submitted order must appear in Kitchen view")
        self.assertIn("Churrasco Especial KDS", content, "[C10] Ordered items must be visible to cooks")
