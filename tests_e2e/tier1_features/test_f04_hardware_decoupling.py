"""
Tier 1 — Feature F04: Hardware Decoupling & Thermal Web Ticket.
Verifies graceful printer failure isolation, polymorphic response, and web ticket view.
"""

from unittest.mock import MagicMock, patch
from tests_e2e.base import E2ETestCase


class TestF04HardwareDecoupling(E2ETestCase):
    """Test suite for Feature F04: Hardware Decoupling."""

    def test_f04_01_imprimir_comanda_returns_dict_without_exception(self):
        """TC-F04-01: imprimir_comanda must return a dict and never raise unhandled exceptions on missing hardware."""
        imprimir_comanda = self.require_service("Menu.utils", "imprimir_comanda", feature_id="F04")
        mock_orden = MagicMock()
        mock_orden.id = 999
        mock_orden.cliente = "Test Cliente"
        mock_orden.items.all.return_value = []
        mock_orden.monto_total = 15000.0

        result = imprimir_comanda(mock_orden)
        self.assertIsInstance(result, dict, "[F04] imprimir_comanda must return a dictionary status")
        self.assertIn("success", result, "[F04] Result dict must contain 'success' key")
        self.assertIn("mode", result, "[F04] Result dict must contain 'mode' key")

    def test_f04_02_web_ticket_url_resolves(self):
        """TC-F04-02: Web ticket URL /pedidos/<id>/ticket/ must resolve to a valid view."""
        from django.urls import resolve
        match = resolve('/pedidos/1/ticket/')
        self.assertIsNotNone(match, "[F04] /pedidos/<id>/ticket/ route must resolve")

    def test_f04_03_web_ticket_view_404_for_nonexistent_order(self):
        """TC-F04-03: Web ticket endpoint must return HTTP 404 for nonexistent order ID."""
        client = self.get_client()
        response = client.get('/pedidos/999999/ticket/')
        self.assertEqual(response.status_code, 404, "[F04] Non-existent order ticket should return 404")

    def test_f04_04_ticket_template_exists_with_thermal_styles(self):
        """TC-F04-04: ticket.html template must exist and include thermal print CSS or print trigger."""
        ticket_tpl = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "ticket.html"
        self.assertTrue(ticket_tpl.exists(), "[F04] ticket.html template must exist in Menu/templates/Menu/")
        content = ticket_tpl.read_text(encoding="utf-8")
        has_print = "window.print" in content or "@media print" in content or "58mm" in content or "80mm" in content
        self.assertTrue(has_print, "[F04] ticket.html should include print styles (@media print / window.print)")

    def test_f04_05_imprimir_comanda_safe_under_mocked_driver_error(self):
        """TC-F04-05: imprimir_comanda catches OS/USB/Driver exceptions and returns success=False gracefully."""
        imprimir_comanda = self.require_service("Menu.utils", "imprimir_comanda", feature_id="F04")
        mock_orden = MagicMock()
        mock_orden.id = 101

        with patch("Menu.utils.win32print", create=True) as mock_win:
            mock_win.OpenPrinter.side_effect = Exception("USB printer offline or not found")
            result = imprimir_comanda(mock_orden)
            self.assertIsInstance(result, dict)
            self.assertFalse(result.get("success", False), "[F04] Should report success=False when driver throws error")
