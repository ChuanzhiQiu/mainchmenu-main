"""
Tier 1 — Feature F17: Kitchen Reactive Polling.
Verifies non-destructive HTMX polling (5s) for live kitchen board updates.
"""

from tests_e2e.base import E2ETestCase


class TestF17KDSPolling(E2ETestCase):
    """Test suite for Feature F17: Kitchen Reactive Polling."""

    def setUp(self):
        self.Orden = self.require_model("Menu", "Orden", feature_id="F17")
        self.inicio_tpl = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "inicio.html"
        self.tpl_content = self.inicio_tpl.read_text(encoding="utf-8") if self.inicio_tpl.exists() else ""

    def test_f17_01_htmx_polling_trigger_defined(self):
        """TC-F17-01: inicio.html defines HTMX polling trigger (every 5s or interval)."""
        has_polling = (
            "hx-trigger" in self.tpl_content
            and ("every" in self.tpl_content or "5s" in self.tpl_content or "load" in self.tpl_content)
        ) or "setinterval" in self.tpl_content.lower()
        self.assertTrue(has_polling, "[F17] KDS template must define reactive polling (every 5s / setInterval)")

    def test_f17_02_htmx_target_swap_defined(self):
        """TC-F17-02: Template defines non-destructive swap target (hx-target or hx-swap)."""
        has_swap = "hx-target" in self.tpl_content or "hx-swap" in self.tpl_content or "innerhtml" in self.tpl_content.lower()
        self.assertTrue(has_swap, "[F17] KDS must configure non-destructive DOM swap target")

    def test_f17_03_repeated_polling_returns_200(self):
        """TC-F17-03: Repeated polling requests to kitchen board return 200 OK without errors."""
        client = self.get_client()
        for _ in range(3):
            resp = client.get("/", HTTP_HX_REQUEST="true")
            self.assertEqual(resp.status_code, 200, "[F17] Polling request must succeed with 200 OK")

    def test_f17_04_new_order_rendered_on_subsequent_poll(self):
        """TC-F17-04: A newly injected order appears dynamically in the next poll response."""
        client = self.get_client()
        resp1 = client.get("/")
        self.assertNotIn("Live Polled Order", resp1.content.decode("utf-8"))

        self.Orden.objects.create(cliente="Live Polled Order", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")

        resp2 = client.get("/")
        self.assertIn("Live Polled Order", resp2.content.decode("utf-8"), "[F17] Polling must reflect newly created order")

    def test_f17_05_htmx_request_header_handled(self):
        """TC-F17-05: Server recognizes HX-Request header and processes request gracefully."""
        client = self.get_client()
        resp = client.get("/", HTTP_HX_REQUEST="true")
        self.assertIn(resp.status_code, [200, 204], "[F17] HTMX request header should be supported cleanly")
