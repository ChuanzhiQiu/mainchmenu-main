"""
Tier 4 — Real-World Scenario S04: "Fallo de Conexión de IA y Recuperación Heurística" (AI Failure & Graceful Degradation).
Simulates an unexpected LLM network timeout or corrupted JSON response during store manager purchasing decision;
the forecaster catches the error and executes deterministic heuristic ROP fallback seamlessly.
"""

from decimal import Decimal
from unittest.mock import patch
from tests_e2e.base import E2ETestCase


class TestS04AIFailureRecovery(E2ETestCase):
    """Scenario 4: External LLM outage and fallback recovery."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="S04")
        self.generar_sugerencias = self.require_service("src.ai_forecast.forecaster", "generar_sugerencias_compra", feature_id="S04")

    def test_s04_llm_outage_graceful_recovery(self):
        """TC-S04-01: When LLM API throws ConnectionError / 503, forecaster transparently falls back to heuristic ROP."""
        # Critical insumo: stock=1.0, min=10.0
        insumo = self.Insumo.objects.create(
            codigo="OUTAGE-INS", nombre="Café Grano Italiano", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("12000.000")
        )

        # Simulate LLM raising an unexpected network exception
        with patch("src.ai_forecast.forecaster.llm_call", create=True, side_effect=ConnectionError("Gemini API connection timeout")):
            result = self.generar_sugerencias(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(result, "[S04] Forecaster must return valid recommendation despite LLM crash")

        # Result should indicate fallback method
        method = result.metodo if hasattr(result, "metodo") else result.get("metodo", "")
        self.assertIn("HEURISTIC", method.upper(), "[S04] Execution method should reflect fallback")

        # Insumo should be recommended for purchase
        items = result.items_sugeridos if hasattr(result, "items_sugeridos") else result.get("items_sugeridos", [])
        self.assertTrue(len(items) > 0, "[S04] Fallback should suggest purchase for depleted item")

        # Budget should be positive
        total = result.presupuesto_estimado_total if hasattr(result, "presupuesto_estimado_total") else result.get("presupuesto_estimado_total", 0)
        self.assertGreater(total, 0, "[S04] Fallback budget must be non-zero")
