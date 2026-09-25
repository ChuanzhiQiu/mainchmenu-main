"""
Tier 1 — Feature F07: Recipe Model (RecetaItem).
Verifies Plato to Insumo bill of materials, required amounts, and unique constraints.
"""

from decimal import Decimal
from django.db import IntegrityError
from tests_e2e.base import E2ETestCase


class TestF07RecetaModel(E2ETestCase):
    """Test suite for Feature F07: Recipe Model (RecetaItem)."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F07")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F07")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F07")

    def test_f07_01_receta_item_fields_exist(self):
        """TC-F07-01: RecetaItem must have plato, insumo, and cantidad fields."""
        self.assert_model_has_field(self.RecetaItem, "plato")
        self.assert_model_has_field(self.RecetaItem, "insumo")
        self.assert_model_has_field(self.RecetaItem, "cantidad")

    def test_f07_02_create_recipe_item_and_precision(self):
        """TC-F07-02: RecetaItem links plato to insumo with 3 decimal places amount."""
        plato = self.Plato.objects.create(nombre="Hamburguesa Clásica", valor=6500)
        insumo = self.Insumo.objects.create(
            codigo="INS-PAN", nombre="Pan Brioche", unidad_medida="un",
            stock_actual=Decimal("50.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("350.000")
        )
        receta = self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("1.000"))
        receta.refresh_from_db()
        self.assertEqual(receta.cantidad, Decimal("1.000"), "[F07] Recipe amount should match 1.000")

    def test_f07_03_unique_together_constraint(self):
        """TC-F07-03: An Insumo cannot be added twice to the same Plato recipe."""
        plato = self.Plato.objects.create(nombre="Churrasco Italiano", valor=7500)
        insumo = self.Insumo.objects.create(
            codigo="INS-PALTA", nombre="Palta Hass", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("4500.000")
        )
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.100"))
        with self.assertRaises(IntegrityError):
            self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.050"))

    def test_f07_04_query_ingredients_for_dish(self):
        """TC-F07-04: Querying all recipe items for a dish returns all its raw materials."""
        plato = self.Plato.objects.create(nombre="Completo Italiano", valor=3500)
        ins1 = self.Insumo.objects.create(codigo="INS-VIENESA", nombre="Vienesa", unidad_medida="un",
                                         stock_actual=Decimal("30.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("300.000"))
        ins2 = self.Insumo.objects.create(codigo="INS-TOMATE", nombre="Tomate", unidad_medida="kg",
                                         stock_actual=Decimal("15.000"), stock_minimo=Decimal("3.000"), costo_unitario=Decimal("1200.000"))

        self.RecetaItem.objects.create(plato=plato, insumo=ins1, cantidad=Decimal("1.000"))
        self.RecetaItem.objects.create(plato=plato, insumo=ins2, cantidad=Decimal("0.080"))

        items = self.RecetaItem.objects.filter(plato=plato)
        self.assertEqual(items.count(), 2, "[F07] Dish should have exactly 2 recipe items")

    def test_f07_05_fractional_recipe_quantities(self):
        """TC-F07-05: Supporting fractional quantities like grams or milliliters (0.015 kg, 0.030 lt)."""
        plato = self.Plato.objects.create(nombre="Papas Fritas Medianas", valor=2800)
        insumo = self.Insumo.objects.create(
            codigo="INS-ACEITE", nombre="Aceite Freír", unidad_medida="lt",
            stock_actual=Decimal("50.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("2000.000")
        )
        receta = self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.045"))
        self.assertEqual(receta.cantidad, Decimal("0.045"), "[F07] Fractional quantity 0.045 lt must be retained")
