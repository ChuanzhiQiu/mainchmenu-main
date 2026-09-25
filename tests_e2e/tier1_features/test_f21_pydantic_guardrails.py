"""
Tier 1 — Feature F21: Pydantic Guardrails for AI Outputs.
Verifies strict Pydantic v2 schemas (SugerenciaOrdenCompra, InsumoSugerido) and error rejection.
"""

from tests_e2e.base import E2ESimpleTestCase

try:
    from pydantic import ValidationError
except ImportError:
    ValidationError = Exception


class TestF21PydanticGuardrails(E2ESimpleTestCase):
    """Test suite for Feature F21: Pydantic Guardrails."""

    def setUp(self):
        self.SugerenciaOrdenCompra = self.require_service("src.ai_forecast.schemas", "SugerenciaOrdenCompra", feature_id="F21")
        self.InsumoSugerido = self.require_service("src.ai_forecast.schemas", "InsumoSugerido", feature_id="F21")

    def test_f21_01_valid_schema_instantiation(self):
        """TC-F21-01: Valid dictionary parses into SugerenciaOrdenCompra without error."""
        payload = {
            "items_sugeridos": [
                {
                    "insumo_id": 1,
                    "codigo": "INS-CARNE",
                    "nombre": "Carne Vacuno",
                    "unidad_medida": "kg",
                    "stock_actual": 2.0,
                    "stock_minimo": 5.0,
                    "consumo_diario_estimado": 1.5,
                    "cantidad_sugerida": 8.5,
                    "costo_unitario": 7000.0,
                    "costo_subtotal": 59500.0,
                    "justificacion": "Demanda proyectada supera stock actual."
                }
            ],
            "presupuesto_estimado_total": 59500.0,
            "periodo_dias": 7,
            "metodo": "LLM_GENERATED"
        }
        obj = self.SugerenciaOrdenCompra(**payload)
        self.assertEqual(len(obj.items_sugeridos), 1)
        self.assertEqual(obj.presupuesto_estimado_total, 59500.0)

    def test_f21_02_negative_quantity_rejected(self):
        """TC-F21-02: Negative cantidad_sugerida triggers Pydantic ValidationError."""
        item_data = {
            "insumo_id": 2,
            "codigo": "INS-PAN",
            "nombre": "Pan",
            "unidad_medida": "un",
            "stock_actual": 10.0,
            "stock_minimo": 5.0,
            "consumo_diario_estimado": 1.0,
            "cantidad_sugerida": -5.0,  # Invalid negative
            "costo_unitario": 300.0,
            "costo_subtotal": -1500.0,
            "justificacion": "Error negativo"
        }
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**item_data)

    def test_f21_03_missing_required_fields_rejected(self):
        """TC-F21-03: Missing essential fields like items_sugeridos raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.SugerenciaOrdenCompra(periodo_dias=7)

    def test_f21_04_empty_justification_rejected_or_validated(self):
        """TC-F21-04: InsumoSugerido must include a meaningful non-empty justification."""
        item_data = {
            "insumo_id": 3,
            "codigo": "INS-QUESO",
            "nombre": "Queso",
            "unidad_medida": "kg",
            "stock_actual": 1.0,
            "stock_minimo": 2.0,
            "consumo_diario_estimado": 0.5,
            "cantidad_sugerida": 3.0,
            "costo_unitario": 6000.0,
            "costo_subtotal": 18000.0,
            "justificacion": ""  # Blank justification
        }
        try:
            item = self.InsumoSugerido(**item_data)
            self.assertGreater(len(item.justificacion.strip()), 0, "[F21] Blank justification should be disallowed")
        except ValidationError:
            pass

    def test_f21_05_serialization_to_dict_and_json(self):
        """TC-F21-05: Model instance serializes cleanly to JSON dictionary."""
        payload = {
            "items_sugeridos": [],
            "presupuesto_estimado_total": 0.0,
            "periodo_dias": 7,
            "metodo": "HEURISTIC_FALLBACK"
        }
        obj = self.SugerenciaOrdenCompra(**payload)
        dump = obj.model_dump() if hasattr(obj, "model_dump") else obj.dict()
        self.assertIsInstance(dump, dict)
        self.assertEqual(dump["metodo"], "HEURISTIC_FALLBACK")
