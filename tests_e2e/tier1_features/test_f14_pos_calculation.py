"""
Tier 1 — Feature F14: POS Dynamic Calculation.
Verifies real-time subtotal, discount handling, and total calculations in POS and backend models.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestF14POSCalculation(E2ETestCase):
    """Test suite for Feature F14: POS Dynamic Calculation."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F14")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F14")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F14")
        self.tpl_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "crear_orden.html"
        self.tpl_content = self.tpl_path.read_text(encoding="utf-8") if self.tpl_path.exists() else ""

    def test_f14_01_backend_total_calculation_single_item(self):
        """TC-F14-01: Order calcular_total accurately computes price * quantity."""
        plato = self.Plato.objects.create(nombre="Pizza Individual", valor=5000.0)
        orden = self.Orden.objects.create(cliente="Mesa Calc", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=3)

        total = orden.calcular_total()
        self.assertEqual(total, 15000.0, "[F14] Total should be 3 * 5000 = 15000")

    def test_f14_02_backend_total_with_discount(self):
        """TC-F14-02: Order with discount subtracts discount amount."""
        plato = self.Plato.objects.create(nombre="Sushi Roll", valor=8000.0)
        orden = self.Orden.objects.create(cliente="Mesa Promo", canal_venta="Local", tipo_pago="Efectivo", descuento=2000.0)
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=2)

        total = orden.calcular_total()
        self.assertEqual(total, 14000.0, "[F14] Total should be (2 * 8000) - 2000 = 14000")

    def test_f14_03_discount_cannot_make_total_negative(self):
        """TC-F14-03: Excessive discount clamps total to zero or throws validation."""
        plato = self.Plato.objects.create(nombre="Bebida", valor=1500.0)
        orden = self.Orden.objects.create(cliente="Mesa Free", canal_venta="Local", tipo_pago="Efectivo", descuento=5000.0)
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=1)

        total = orden.calcular_total()
        self.assertGreaterEqual(total, 0.0, "[F14] Total must not be negative")

    def test_f14_04_mixed_items_calculation(self):
        """TC-F14-04: Order with multiple items correctly aggregates all line items."""
        p1 = self.Plato.objects.create(nombre="Entrada", valor=3000.0)
        p2 = self.Plato.objects.create(nombre="Fondo", valor=7000.0)
        orden = self.Orden.objects.create(cliente="Mesa Completa", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p1, cantidad=2)
        self.OrdenItem.objects.create(orden=orden, plato=p2, cantidad=1)

        total = orden.calcular_total()
        self.assertEqual(total, 13000.0, "[F14] Total should be (2*3000) + (1*7000) = 13000")

    def test_f14_05_client_side_calculation_functions_in_template(self):
        """TC-F14-05: Template contains dynamic calculation logic (subtotal/total/descuento)."""
        has_calc = any(w in self.tpl_content.lower() for w in ["calcular", "subtotal", "descuento", "total", "updatecart", "calculartotal"])
        self.assertTrue(has_calc, "[F14] crear_orden.html must contain client-side calculation triggers")
