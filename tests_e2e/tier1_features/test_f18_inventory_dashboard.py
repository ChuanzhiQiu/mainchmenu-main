"""
Tier 1 — Feature F18: Inventory Dashboard View.
Verifies inventory dashboard (inventario.html), low stock alerts, and manual stock adjustments.
"""

from decimal import Decimal
from django.urls import resolve
from tests_e2e.base import E2ETestCase


class TestF18InventoryDashboard(E2ETestCase):
    """Test suite for Feature F18: Inventory Dashboard View."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F18")
        self.tpl_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "inventario.html"

    def test_f18_01_template_exists(self):
        """TC-F18-01: inventario.html template must exist in Menu/templates/Menu/."""
        self.assertTrue(self.tpl_path.exists(), "[F18] inventario.html must exist")

    def test_f18_02_inventory_url_resolves(self):
        """TC-F18-02: /inventario/ route resolves to a valid view."""
        try:
            match = resolve("/inventario/")
            self.assertIsNotNone(match, "[F18] /inventario/ route must resolve")
        except Exception as e:
            self.fail(f"[F18] /inventario/ route resolution failed: {e}")

    def test_f18_03_inventory_view_renders_insumos(self):
        """TC-F18-03: GET /inventario/ displays insumo records and current stock levels."""
        self.Insumo.objects.create(
            codigo="INS-PANINV", nombre="Pan Brioche Especial", unidad_medida="un",
            stock_actual=Decimal("45.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("350.000")
        )
        client = self.get_client()
        resp = client.get("/inventario/")
        self.assertEqual(resp.status_code, 200, "[F18] /inventario/ should return 200 OK")
        content = resp.content.decode("utf-8")
        self.assertIn("Pan Brioche Especial", content, "[F18] Insumo name should appear in inventory view")

    def test_f18_04_critical_stock_indicator_present(self):
        """TC-F18-04: Items below stock_minimo are visually flagged or badged."""
        self.Insumo.objects.create(
            codigo="INS-LOW", nombre="Carne Escasa", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("6000.000")
        )
        client = self.get_client()
        resp = client.get("/inventario/")
        content = resp.content.decode("utf-8")
        # Check for alert, badge, or warning indicators
        has_warning = any(w in content.lower() for w in ["alerta", "crítico", "critico", "bajo", "danger", "warning", "badge", "quiebre"])
        self.assertTrue(has_warning, "[F18] Inventory dashboard must visually flag low stock items")

    def test_f18_05_manual_stock_adjustment_structure(self):
        """TC-F18-05: Template or endpoint provides manual stock adjustment mechanism."""
        content = self.tpl_path.read_text(encoding="utf-8") if self.tpl_path.exists() else ""
        has_adj = any(w in content.lower() for w in ["ajuste", "ajustar", "movimiento", "stock", "modal", "cantidad"])
        self.assertTrue(has_adj, "[F18] inventario.html should include stock adjustment modal or form")
