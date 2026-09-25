"""
Tier 3 — Cross-Feature Combination C07: F04 (Hardware Decoupling) + F11 (Confirm Hook) + F16 (KDS).
Verifies that confirming an order from KDS when printer hardware is unplugged succeeds gracefully.
"""

import json
from unittest.mock import patch
from tests_e2e.base import E2ETestCase


class TestC07KDSHardwareResilience(E2ETestCase):
    """Pairwise combination: Hardware Printer Isolation + Confirmation Hook + Kitchen KDS Board."""

    def setUp(self):
        self.Orden = self.require_model("Menu", "Orden", feature_id="C07")

    def test_c07_01_confirm_from_kds_succeeds_without_printer_crash(self):
        """TC-C07-01: Confirming order in cloud environment without USB printer updates state and returns web ticket link."""
        orden = self.Orden.objects.create(cliente="Mesa Cloud Test", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")

        client = self.get_client()
        with patch("Menu.utils.win32print", None):
            resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
            self.assertEqual(resp.status_code, 200, "[C07] Order confirmation must succeed even if physical printer missing")

        orden.refresh_from_db()
        self.assertEqual(orden.estado, "Completada")

        # Verify web ticket endpoint still renders cleanly as fallback
        ticket_resp = client.get(f"/pedidos/{orden.id}/ticket/")
        self.assertEqual(ticket_resp.status_code, 200, "[C07] Digital web comanda must render successfully")
