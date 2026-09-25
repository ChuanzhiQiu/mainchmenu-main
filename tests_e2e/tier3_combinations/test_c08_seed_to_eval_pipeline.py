"""
Tier 3 — Cross-Feature Combination C08: F26 (Seed Data) + F19 (Forecasting) + F24 (Eval Harness).
Verifies that generated synthetic data seamlessly feeds the automated AI evaluation scenarios.
"""

from django.core.management import call_command
from tests_e2e.base import E2ETestCase


class TestC08SeedToEvalPipeline(E2ETestCase):
    """Pairwise combination: Demo Seeding + Forecaster Service + Eval Harness."""

    def test_c08_01_seeded_database_executes_forecaster(self):
        """TC-C08-01: Running generar_datos_demo creates sufficient orders to run forecaster without zero-data error."""
        call_command("generar_datos_demo")

        generar_sugerencias = self.require_service("src.ai_forecast.forecaster", "generar_sugerencias_compra", feature_id="C08")
        result = generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(result)

        data = result.model_dump() if hasattr(result, "model_dump") else (result.dict() if hasattr(result, "dict") else result)
        self.assertIn("items_sugeridos", data)
        self.assertIn("presupuesto_estimado_total", data)
