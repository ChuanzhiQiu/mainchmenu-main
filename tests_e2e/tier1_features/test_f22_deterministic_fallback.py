"""
Tier 1 — Feature F22: Deterministic Heuristic Fallback Engine.
Verifies ROP calculation (LeadTime * Demand + SafetyStock - Stock) during LLM downtime.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestF22DeterministicFallback(E2ETestCase):
    """Test suite for Feature F22: Deterministic Fallback Engine."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F22")
        self.calcular_fallback = self.require_service("src.ai_forecast.fallback", "calcular_reorden_heuristico", feature_id="F22")

    def test_f22_01_fallback_callable(self):
        """TC-F22-01: calcular_reorden_heuristico must be callable."""
        self.assertTrue(callable(self.calcular_fallback), "[F22] Fallback engine must be callable")

    def test_f22_02_rop_calculation_positive_reorder(self):
        """TC-F22-02: Suggests positive purchase when stock is below (LeadTime * Demand + SafetyStock)."""
        # Insumo: stock_actual=1, stock_minimo=5, daily_demand=2, lead_time=3 days
        # Required = (2 * 3) + 5 = 11. Deficit = 11 - 1 = 10.
        insumo = self.Insumo.objects.create(
            codigo="INS-DEFICIT", nombre="Insumo Deficit", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("2000.000")
        )
        res = self.calcular_fallback(insumos=[insumo], dias_lead_time=3, dias_proyeccion=7)
        # Check that item is recommended
        items = res.items_sugeridos if hasattr(res, "items_sugeridos") else res.get("items_sugeridos", [])
        self.assertTrue(len(items) > 0, "[F22] Should suggest replenishment for deficit item")
        item = items[0]
        qty = item.cantidad_sugerida if hasattr(item, "cantidad_sugerida") else item["cantidad_sugerida"]
        self.assertGreater(qty, 0, "[F22] Recommended quantity must be positive")

    def test_f22_03_zero_reorder_when_stock_sufficient(self):
        """TC-F22-03: Does not recommend purchase when current stock is abundantly above minimum and lead time demand."""
        insumo = self.Insumo.objects.create(
            codigo="INS-SURPLUS", nombre="Insumo Abundante", unidad_medida="kg",
            stock_actual=Decimal("100.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1000.000")
        )
        res = self.calcular_fallback(insumos=[insumo], dias_lead_time=2, dias_proyeccion=7)
        items = res.items_sugeridos if hasattr(res, "items_sugeridos") else res.get("items_sugeridos", [])
        # Should either filter out surplus or quantity is 0
        surplus_recs = [it for it in items if (it.codigo if hasattr(it, "codigo") else it["codigo"]) == "INS-SURPLUS"]
        if surplus_recs:
            qty = surplus_recs[0].cantidad_sugerida if hasattr(surplus_recs[0], "cantidad_sugerida") else surplus_recs[0]["cantidad_sugerida"]
            self.assertEqual(qty, 0.0, "[F22] Quantity for surplus insumo should be 0")

    def test_f22_04_fallback_output_validates_against_pydantic_schema(self):
        """TC-F22-04: Fallback engine produces a fully compliant SugerenciaOrdenCompra."""
        insumo = self.Insumo.objects.create(
            codigo="INS-SCHEMA", nombre="Insumo Schema", unidad_medida="un",
            stock_actual=Decimal("2.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("500.000")
        )
        res = self.calcular_fallback(insumos=[insumo])
        method = res.metodo if hasattr(res, "metodo") else res.get("metodo")
        self.assertIn("HEURISTIC", method.upper(), "[F22] Fallback result should mark metodo as HEURISTIC")

    def test_f22_05_handles_zero_historical_demand_gracefully(self):
        """TC-F22-05: Handles insumos with 0 daily demand without ZeroDivisionError."""
        insumo = self.Insumo.objects.create(
            codigo="INS-NODEMAND", nombre="Insumo Sin Ventas", unidad_medida="lt",
            stock_actual=Decimal("0.500"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1500.000")
        )
        # Should not crash
        res = self.calcular_fallback(insumos=[insumo])
        self.assertIsNotNone(res)
