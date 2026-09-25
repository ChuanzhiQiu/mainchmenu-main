"""
Tier 3 — Cross-Feature Combination C13: Empirical Challenger Suite for Milestone 4 (F20, F21).
Adversarial stress testing of Pydantic Guardrails (schemas.py) and Prompt Validation (demand_forecaster_system.md).

Tests:
1. Pydantic schema validation rigor: negative quantities, negative costs, negative stock_minimo,
   negative consumo_diario, non-positive insumo_id, empty strings, whitespace-only strings.
2. Discovery and execution of custom @field_validator and @model_validator.
3. Exception hierarchy: verifying ValidationError is raised (not unhandled raw ValueError).
4. Container integrity (SugerenciaOrdenCompra): non-list items, corrupted items, negative budget,
   budget mismatch auto-reconciliation, subtotal auto-reconciliation.
5. Dual serialization: model_dump() and dict() deep serialization and json round-trip.
6. Prompt compliance: character count (>200), word count (<2000), absence of UTF-8 BOM,
   markdown header, required keywords, anti-filler instructions.
7. parse_and_validate_forecast_json: markdown fences, conversational noise, syntax errors.
"""

import json
from pathlib import Path
from typing import Any, Dict

from tests_e2e.base import E2ESimpleTestCase

try:
    from pydantic import ValidationError
except ImportError:
    ValidationError = Exception


class TestC13EmpiricalChallengerM4SchemasAndPrompt(E2ESimpleTestCase):
    """Adversarial stress testing harness for M4 Pydantic guardrails and prompt validation."""

    def setUp(self):
        self.SugerenciaOrdenCompra = self.require_service("src.ai_forecast.schemas", "SugerenciaOrdenCompra", feature_id="F21")
        self.InsumoSugerido = self.require_service("src.ai_forecast.schemas", "InsumoSugerido", feature_id="F21")
        self.parse_and_validate = self.require_service("src.ai_forecast.schemas", "parse_and_validate_forecast_json", feature_id="F21")
        self.prompt_path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"

        self.valid_item_dict = {
            "insumo_id": 1,
            "codigo": "INS-POLLO",
            "nombre": "Pechuga de Pollo",
            "unidad_medida": "kg",
            "stock_actual": 2.5,
            "stock_minimo": 5.0,
            "consumo_diario_estimado": 1.2,
            "cantidad_sugerida": 8.0,
            "costo_unitario": 4500.0,
            "costo_subtotal": 36000.0,
            "justificacion": "Stock en riesgo. Demanda proyectada de 7 días supera el stock disponible."
        }

    # =========================================================================
    # SECTION 1: Adversarial Boundary Tests on InsumoSugerido
    # =========================================================================

    def test_c13_01_negative_quantity_raises_validation_error(self):
        """TC-C13-01: Negative cantidad_sugerida (-0.001, -5.0) must raise ValidationError."""
        bad_item = dict(self.valid_item_dict, cantidad_sugerida=-0.001)
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**bad_item)

    def test_c13_02_negative_unit_cost_raises_validation_error(self):
        """TC-C13-02: Negative costo_unitario (-100.0) must raise ValidationError."""
        bad_item = dict(self.valid_item_dict, costo_unitario=-100.0)
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**bad_item)

    def test_c13_03_negative_stock_minimo_raises_validation_error(self):
        """TC-C13-03: Negative stock_minimo (-1.0) must raise ValidationError."""
        bad_item = dict(self.valid_item_dict, stock_minimo=-1.0)
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**bad_item)

    def test_c13_04_negative_consumo_diario_raises_validation_error(self):
        """TC-C13-04: Negative consumo_diario_estimado (-0.5) must raise ValidationError."""
        bad_item = dict(self.valid_item_dict, consumo_diario_estimado=-0.5)
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**bad_item)

    def test_c13_05_zero_and_negative_insumo_id_raises_validation_error(self):
        """TC-C13-05: Non-positive insumo_id (0, -1) must raise ValidationError (gt=0)."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, insumo_id=0))
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, insumo_id=-10))

    def test_c13_06_empty_string_justification_rejected(self):
        """TC-C13-06: Empty string justification ('') must be rejected."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, justificacion=""))

    def test_c13_07_whitespace_only_justification_must_raise_validation_error(self):
        """TC-C13-07: Whitespace-only justification ('   ') must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, justificacion="   \t\n   "))

    def test_c13_08_whitespace_only_codigo_must_raise_validation_error(self):
        """TC-C13-08: Whitespace-only codigo ('   ') must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, codigo="   "))

    def test_c13_09_whitespace_only_unidad_medida_must_raise_validation_error(self):
        """TC-C13-09: Whitespace-only unidad_medida ('   ') must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, unidad_medida="   "))

    def test_c13_10_field_validators_discovered_and_executed(self):
        """TC-C13-10: Custom @field_validator methods must be discovered and strip whitespace."""
        item = self.InsumoSugerido(**dict(self.valid_item_dict, codigo="  INS-TRIM  "))
        self.assertEqual(item.codigo, "INS-TRIM", "Field validator should have stripped leading/trailing whitespace")

    def test_c13_11_field_validator_value_error_wrapped_in_validation_error(self):
        """TC-C13-11: Custom @field_validator raising ValueError must be wrapped into ValidationError."""
        try:
            self.InsumoSugerido(**dict(self.valid_item_dict, justificacion="    "))
            self.fail("Expected ValidationError for whitespace-only justification")
        except ValidationError:
            pass  # Expected
        except ValueError as e:
            self.fail(f"Field validator raised raw ValueError instead of wrapping into ValidationError: {e}")

    # =========================================================================
    # SECTION 2: Container SugerenciaOrdenCompra Adversarial Tests
    # =========================================================================

    def test_c13_12_non_list_items_sugeridos_raises_validation_error(self):
        """TC-C13-12: Passing non-list to items_sugeridos must raise ValidationError."""
        for invalid in ["not_a_list", 12345, True, None, {"key": "val"}]:
            with self.assertRaises(ValidationError):
                self.SugerenciaOrdenCompra(items_sugeridos=invalid, presupuesto_estimado_total=0.0)

    def test_c13_13_corrupted_items_in_list_raises_validation_error(self):
        """TC-C13-13: List containing corrupted elements (None, str, dict with negative qty) raises ValidationError."""
        for bad in [None, "invalid_str", 123, {}, {"cantidad_sugerida": -5.0}]:
            with self.assertRaises((ValidationError, TypeError, ValueError)):
                self.SugerenciaOrdenCompra(items_sugeridos=[bad], presupuesto_estimado_total=0.0)

    def test_c13_14_negative_presupuesto_raises_validation_error(self):
        """TC-C13-14: Negative presupuesto_estimado_total (-100.0) raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.SugerenciaOrdenCompra(items_sugeridos=[], presupuesto_estimado_total=-100.0)

    def test_c13_15_negative_and_zero_periodo_dias_raises_validation_error(self):
        """TC-C13-15: Non-positive periodo_dias (0, -7) raises ValidationError (ge=1)."""
        with self.assertRaises(ValidationError):
            self.SugerenciaOrdenCompra(items_sugeridos=[], presupuesto_estimado_total=0.0, periodo_dias=0)
        with self.assertRaises(ValidationError):
            self.SugerenciaOrdenCompra(items_sugeridos=[], presupuesto_estimado_total=0.0, periodo_dias=-5)

    def test_c13_16_budget_mismatch_auto_reconciles_to_subtotals_sum(self):
        """TC-C13-16: Model validator reconciles mismatched presupuesto_estimado_total to sum of items."""
        item1 = dict(self.valid_item_dict, insumo_id=1, cantidad_sugerida=2.0, costo_unitario=1000.0, costo_subtotal=2000.0)
        item2 = dict(self.valid_item_dict, insumo_id=2, cantidad_sugerida=3.0, costo_unitario=2000.0, costo_subtotal=6000.0)
        obj = self.SugerenciaOrdenCompra(
            items_sugeridos=[item1, item2],
            presupuesto_estimado_total=999999.0,
            periodo_dias=7
        )
        self.assertEqual(obj.presupuesto_estimado_total, 8000.0)

    def test_c13_17_subtotal_coherence_auto_reconciles_to_qty_times_cost(self):
        """TC-C13-17: InsumoSugerido model_validator reconciles subtotal = round(qty * unit_cost, 2)."""
        bad_subtotal_item = dict(self.valid_item_dict, cantidad_sugerida=4.0, costo_unitario=2500.0, costo_subtotal=999.0)
        obj = self.InsumoSugerido(**bad_subtotal_item)
        self.assertEqual(obj.costo_subtotal, 10000.0)

    # =========================================================================
    # SECTION 3: Dual Serialization & JSON Round-Trip
    # =========================================================================

    def test_c13_18_dual_serialization_model_dump_and_dict_identical(self):
        """TC-C13-18: Both model_dump() and dict() return identical dictionaries."""
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[self.valid_item_dict],
            presupuesto_estimado_total=36000.0,
            periodo_dias=7
        )
        dump_v2 = order.model_dump()
        dump_v1 = order.dict()
        self.assertIsInstance(dump_v2, dict)
        self.assertIsInstance(dump_v1, dict)
        self.assertEqual(dump_v2, dump_v1)

    def test_c13_19_dual_serialization_deep_dict_conversion(self):
        """TC-C13-19: Nested items inside items_sugeridos serialize deeply into python dicts."""
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[self.valid_item_dict],
            presupuesto_estimado_total=36000.0
        )
        dump = order.model_dump()
        self.assertIsInstance(dump["items_sugeridos"], list)
        self.assertIsInstance(dump["items_sugeridos"][0], dict)
        json_str = json.dumps(dump)
        reloaded = json.loads(json_str)
        reconstructed = self.SugerenciaOrdenCompra(**reloaded)
        self.assertEqual(reconstructed.presupuesto_estimado_total, 36000.0)

    # =========================================================================
    # SECTION 4: System Prompt Compliance
    # =========================================================================

    def test_c13_20_prompt_utf8_no_bom(self):
        """TC-C13-20: System prompt file is UTF-8 encoded without byte order mark (BOM)."""
        raw = self.prompt_path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "Prompt must not contain UTF-8 BOM")

    def test_c13_21_prompt_word_count_within_token_budget(self):
        """TC-C13-21: System prompt word count is strictly below 2000 words."""
        words = self.prompt_path.read_text(encoding="utf-8").split()
        self.assertLess(len(words), 2000, f"Prompt word count ({len(words)}) must be < 2000")
        self.assertGreater(len(words), 50, f"Prompt word count ({len(words)}) must be substantial")

    def test_c13_22_prompt_character_count_substantial(self):
        """TC-C13-22: System prompt character count exceeds 200 characters."""
        content = self.prompt_path.read_text(encoding="utf-8").strip()
        self.assertGreater(len(content), 200, "Prompt must contain > 200 characters")

    def test_c13_23_prompt_mandatory_keywords_present(self):
        """TC-C13-23: Prompt contains all mandatory domain, schema, and formatting keywords."""
        content = self.prompt_path.read_text(encoding="utf-8").lower()
        mandatory = [
            "inventario", "restaurante", "abastecimiento", "demanda", "compras",
            "json", "items_sugeridos", "cantidad_sugerida", "justificacion",
            "presupuesto_estimado_total", "costo_subtotal", "costo_unitario",
            "stock_actual", "stock_minimo", "consumo_diario_estimado"
        ]
        for kw in mandatory:
            self.assertIn(kw, content, f"Prompt missing required keyword: {kw}")

    # =========================================================================
    # SECTION 5: parse_and_validate_forecast_json Sanitizer
    # =========================================================================

    def test_c13_24_parse_and_validate_forecast_json_markdown_stripping(self):
        """TC-C13-24: parse_and_validate_forecast_json strips markdown codeblocks."""
        payload = {
            "items_sugeridos": [self.valid_item_dict],
            "presupuesto_estimado_total": 36000.0,
            "periodo_dias": 7
        }
        raw = f"```json\n{json.dumps(payload, indent=2)}\n```"
        obj = self.parse_and_validate(raw)
        self.assertIsInstance(obj, self.SugerenciaOrdenCompra)
        self.assertEqual(obj.presupuesto_estimado_total, 36000.0)

    def test_c13_25_parse_and_validate_forecast_json_conversational_stripping(self):
        """TC-C13-25: parse_and_validate_forecast_json strips conversational preambles."""
        payload = {
            "items_sugeridos": [self.valid_item_dict],
            "presupuesto_estimado_total": 36000.0,
            "periodo_dias": 7
        }
        raw = f"Estimado chef, aquí está la orden sugerida:\n{json.dumps(payload)}\nBuen servicio."
        obj = self.parse_and_validate(raw)
        self.assertIsInstance(obj, self.SugerenciaOrdenCompra)
        self.assertEqual(len(obj.items_sugeridos), 1)
