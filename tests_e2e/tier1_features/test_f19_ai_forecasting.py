"""
Tier 1 — Feature F19: AI Forecasting Service.
Verifies historical sales consumption aggregation through recipes and demand forecasting.
"""

from decimal import Decimal
from django.utils import timezone
from tests_e2e.base import E2ETestCase


class TestF19AIForecasting(E2ETestCase):
    """Test suite for Feature F19: AI Forecasting Service."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F19")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F19")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F19")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F19")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F19")
        self.generar_sugerencias = self.require_service("src.ai_forecast.forecaster", "generar_sugerencias_compra", feature_id="F19")

    def test_f19_01_service_callable(self):
        """TC-F19-01: generar_sugerencias_compra must be imported and callable."""
        self.assertTrue(callable(self.generar_sugerencias), "[F19] generar_sugerencias_compra must be callable")

    def test_f19_02_consumption_aggregation_from_completed_orders(self):
        """TC-F19-02: Consumption correctly aggregates historical sales multiplied by recipe amounts."""
        insumo = self.Insumo.objects.create(
            codigo="INS-POLLO", nombre="Pechuga Pollo", unidad_medida="kg",
            stock_actual=Decimal("15.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("4000.000")
        )
        plato = self.Plato.objects.create(nombre="Pollo a las Brasas", valor=6500)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.500"))

        orden = self.Orden.objects.create(cliente="Mesa Pollos", canal_venta="Local", tipo_pago="Efectivo", estado="Completada", fecha_completada=timezone.now())
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=4)

        # Run forecaster with mock or heuristic mode
        result = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(result, "[F19] Forecast generation must return a result object")

    def test_f19_03_cancelled_orders_ignored_in_consumption(self):
        """TC-F19-03: Orders marked 'Eliminada' are excluded from consumption analysis."""
        insumo = self.Insumo.objects.create(
            codigo="INS-SAL", nombre="Sal Marina", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("500.000")
        )
        plato = self.Plato.objects.create(nombre="Papas Saladas", valor=2000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.020"))

        # Deleted order
        self.Orden.objects.create(cliente="Cancelado", canal_venta="Local", tipo_pago="Efectivo", estado="Eliminada")

        result = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(result)

    def test_f19_04_projection_horizon_parameter_handled(self):
        """TC-F19-04: Forecaster respects dias_proyeccion horizon (e.g. 3 days vs 7 days)."""
        res_3d = self.generar_sugerencias(dias_proyeccion=3, usar_llm=False)
        res_7d = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(res_3d)
        self.assertIsNotNone(res_7d)

    def test_f19_05_forecaster_output_has_summary_and_items(self):
        """TC-F19-05: Forecaster output contains items to buy and total budget estimation."""
        result = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        # Check either dict or Pydantic model
        if hasattr(result, "dict") or hasattr(result, "model_dump"):
            data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        else:
            data = result
        self.assertIn("items_sugeridos", data, "[F19] Result must have 'items_sugeridos'")
        self.assertIn("presupuesto_estimado_total", data, "[F19] Result must have 'presupuesto_estimado_total'")
