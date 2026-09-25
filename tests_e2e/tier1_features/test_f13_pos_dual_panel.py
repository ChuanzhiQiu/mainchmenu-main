"""
Tier 1 — Feature F13: POS Dual-Panel Interface (Dishes & Combos).
Verifies that crear_orden.html displays both dishes and menus (combos) and provides dual-panel UX.
"""

from tests_e2e.base import E2ETestCase


class TestF13POSDualPanel(E2ETestCase):
    """Test suite for Feature F13: POS Dual-Panel Interface."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F13")
        self.Menu = self.require_model("Menu", "Menu", feature_id="F13")
        self.tpl_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "crear_orden.html"
        self.assertTrue(self.tpl_path.exists(), "[F13] crear_orden.html must exist")
        self.tpl_content = self.tpl_path.read_text(encoding="utf-8")

    def test_f13_01_menus_por_letra_rendered_in_template(self):
        """TC-F13-01: crear_orden.html must iterate over and render menus_por_letra (combos)."""
        self.assertIn("menus_por_letra", self.tpl_content, "[F13] crear_orden.html must render 'menus_por_letra'")

    def test_f13_02_pos_view_returns_both_dishes_and_combos(self):
        """TC-F13-02: GET /pedidos/crear/ context contains both dishes and combos."""
        plato = self.Plato.objects.create(nombre="Completo", valor=3000)
        combo = self.Menu.objects.create(nombre="Combo Completo + Bebida", precio_menus=4500)
        combo.platos.add(plato)

        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("menus_por_letra", resp.context, "[F13] Context must include menus_por_letra")
        self.assertIn("platos_por_letra", resp.context, "[F13] Context must include platos_por_letra")

    def test_f13_03_rendered_html_contains_dish_and_combo_cards(self):
        """TC-F13-03: Rendered POS HTML displays both dish names and combo names."""
        plato = self.Plato.objects.create(nombre="Churrasco Unico", valor=5000)
        combo = self.Menu.objects.create(nombre="Mega Promo Combo", precio_menus=8000)
        combo.platos.add(plato)

        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        content = resp.content.decode("utf-8")
        self.assertIn("Churrasco Unico", content, "[F13] Dish should appear in POS DOM")
        self.assertIn("Mega Promo Combo", content, "[F13] Combo should appear in POS DOM")

    def test_f13_04_dual_panel_structure_present(self):
        """TC-F13-04: POS layout defines catalog area and cart/order summary panel."""
        has_cart_or_order = any(w in self.tpl_content.lower() for w in ["carrito", "cart", "orden", "resumen", "items-orden", "ticket"])
        self.assertTrue(has_cart_or_order, "[F13] Template must contain order/cart summary panel")

    def test_f13_05_canal_de_ventas_selector_present(self):
        """TC-F13-05: POS includes sales channel selector (e.g. Local, PedidosYa, UberEats)."""
        self.assertIn("canal", self.tpl_content.lower(), "[F13] POS template must contain sales channel input/select")
