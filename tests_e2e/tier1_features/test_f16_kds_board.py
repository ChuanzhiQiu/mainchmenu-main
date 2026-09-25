"""
Tier 1 — Feature F16: Kitchen Display Board (KDS).
Verifies that kitchen view displays ordered items/dishes and supports reactive order status transitions.
"""

from tests_e2e.base import E2ETestCase


class TestF16KDSBoard(E2ETestCase):
    """Test suite for Feature F16: Kitchen Display Board (KDS)."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F16")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F16")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F16")
        self.inicio_tpl = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "inicio.html"
        self.tpl_content = self.inicio_tpl.read_text(encoding="utf-8") if self.inicio_tpl.exists() else ""

    def test_f16_01_kds_view_renders_active_orders(self):
        """TC-F16-01: Kitchen board view renders currently active (En curso) orders."""
        orden = self.Orden.objects.create(cliente="KDS Test", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        client = self.get_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("KDS Test", resp.content.decode("utf-8"), "[F16] Active order client name should appear on KDS")

    def test_f16_02_kds_renders_ordered_items_dishes(self):
        """TC-F16-02: Kitchen cards must render individual dishes/items ordered so cooks know what to prepare."""
        plato = self.Plato.objects.create(nombre="Plato Cocina KDS", valor=4500)
        orden = self.Orden.objects.create(cliente="Cook View Test", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=2)

        client = self.get_client()
        resp = client.get("/")
        content = resp.content.decode("utf-8")
        self.assertIn("Plato Cocina KDS", content, "[F16] KDS must render ordered dish names for kitchen visibility")

    def test_f16_03_no_destructive_location_reload(self):
        """TC-F16-03: inicio.html should avoid destructive location.reload() on state transitions."""
        has_location_reload = "location.reload()" in self.tpl_content
        self.assertFalse(has_location_reload, "[F16] inicio.html should use HTMX / async DOM updates instead of location.reload()")

    def test_f16_04_order_state_transitions_available(self):
        """TC-F16-04: KDS template provides action buttons/triggers for completing or deleting orders."""
        has_actions = "confirmar" in self.tpl_content.lower() or "completar" in self.tpl_content.lower()
        self.assertTrue(has_actions, "[F16] KDS must contain order completion trigger/button")

    def test_f16_05_completed_orders_excluded_from_active_board(self):
        """TC-F16-05: Completed orders are excluded from active pending orders board."""
        orden_done = self.Orden.objects.create(cliente="Cliente Finalizado", canal_venta="Local", tipo_pago="Efectivo", estado="Completada")
        client = self.get_client()
        resp = client.get("/")
        # Verify in context that pedidos_en_curso only contains active orders
        pedidos_en_curso = resp.context.get("pedidos_en_curso", [])
        self.assertNotIn(orden_done, pedidos_en_curso, "[F16] Completed orders should not be in active kitchen queue")
