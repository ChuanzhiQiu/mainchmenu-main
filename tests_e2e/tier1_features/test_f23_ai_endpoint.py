"""
Tier 1 — Feature F23: AI Suggestion API & Endpoint.
Verifies HTTP endpoint returning structured purchase order recommendations.
"""

from decimal import Decimal
from django.urls import resolve
from tests_e2e.base import E2ETestCase


class TestF23AISuggestionEndpoint(E2ETestCase):
    """Test suite for Feature F23: AI Suggestion API & Endpoint."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F23")

    def test_f23_01_endpoint_url_resolves(self):
        """TC-F23-01: AI forecasting endpoint route resolves in Django URL patterns."""
        # Check standard endpoint route
        try:
            match = resolve("/api/sugerencias-compra/")
            self.assertIsNotNone(match)
        except Exception:
            try:
                match = resolve("/inventario/sugerencias-ia/")
                self.assertIsNotNone(match)
            except Exception as e:
                self.fail(f"[F23] Neither /api/sugerencias-compra/ nor /inventario/sugerencias-ia/ resolved: {e}")

    def test_f23_02_endpoint_returns_json_200(self):
        """TC-F23-02: Endpoint responds with HTTP 200 and application/json Content-Type."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, f"[F23] Endpoint {url} should return 200 OK")
        self.assertIn("application/json", resp.headers.get("Content-Type", ""))

    def test_f23_03_response_contains_structured_schema(self):
        """TC-F23-03: Endpoint response JSON includes items_sugeridos and presupuesto_estimado_total."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(url)
        data = resp.json()
        self.assertIn("items_sugeridos", data, "[F23] Response must include 'items_sugeridos'")
        self.assertIn("presupuesto_estimado_total", data, "[F23] Response must include 'presupuesto_estimado_total'")

    def test_f23_04_horizon_query_parameter_supported(self):
        """TC-F23-04: Endpoint accepts dias parameter (?dias=14) for projection horizon."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(f"{url}?dias=14")
        self.assertEqual(resp.status_code, 200)

    def test_f23_05_graceful_handling_on_empty_inventory(self):
        """TC-F23-05: When inventory is empty, endpoint returns empty items list without server crash."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data.get("items_sugeridos"), list)
