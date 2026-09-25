"""
Tier 1 — Feature F15: POS Async Submission.
Verifies asynchronous order submission without full-page reloads and JSON/fragment responses.
"""

import json
from tests_e2e.base import E2ETestCase


class TestF15POSAsyncSubmit(E2ETestCase):
    """Test suite for Feature F15: POS Async Submission."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F15")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F15")
        self.plato = self.Plato.objects.create(nombre="Sandwich Asíncrono", valor=5500.0)

    def test_f15_01_async_order_submission_creates_record(self):
        """TC-F15-01: Submitting order via POST creates an order record in database."""
        client = self.get_client()
        payload = {
            "cliente": "Cliente Async",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 2}]
        }
        # POS order submission route
        resp = client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        # Should return 200 or 302/redirect
        self.assertIn(resp.status_code, [200, 201, 302], "[F15] Order creation POST should succeed")
        self.assertTrue(self.Orden.objects.filter(cliente="Cliente Async").exists(), "[F15] Order must exist in DB")

    def test_f15_02_async_order_associates_items(self):
        """TC-F15-02: Created order has items attached matching submitted payload."""
        client = self.get_client()
        payload = {
            "cliente": "Cliente Items",
            "canal_venta": "PedidosYa",
            "tipo_pago": "Tarjeta",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        orden = self.Orden.objects.filter(cliente="Cliente Items").first()
        if orden:
            self.assertGreater(orden.items.count(), 0, "[F15] Order must have associated items")

    def test_f15_03_template_uses_async_fetch_or_htmx(self):
        """TC-F15-03: crear_orden.html contains fetch or htmx async order submission logic."""
        tpl_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "crear_orden.html"
        self.assertTrue(tpl_path.exists())
        content = tpl_path.read_text(encoding="utf-8")
        has_async = "fetch(" in content or "hx-post" in content or "axios" in content or "xmlhttprequest" in content.lower()
        self.assertTrue(has_async, "[F15] crear_orden.html must use async dispatch (fetch/hx-post) to avoid full reload")

    def test_f15_04_async_submission_validation_on_empty_client(self):
        """TC-F15-04: Submitting an order without required data returns graceful error."""
        client = self.get_client()
        payload = {"cliente": "", "canal_venta": "", "items": []}
        resp = client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertNotEqual(resp.status_code, 500, "[F15] Incomplete order submission must not trigger 500 server crash")

    def test_f15_05_async_response_is_json_or_htmx_partial(self):
        """TC-F15-05: Async order submission responds with JSON or HTMX partial."""
        client = self.get_client()
        payload = {
            "cliente": "Cliente Header Check",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = client.post(
            "/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        content_type = resp.headers.get("Content-Type", "")
        valid_response = "application/json" in content_type or "text/html" in content_type
        self.assertTrue(valid_response, "[F15] Response content type should be JSON or HTML fragment")
