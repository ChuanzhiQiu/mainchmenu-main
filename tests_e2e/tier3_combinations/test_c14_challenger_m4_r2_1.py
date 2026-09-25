"""
Tier 3 — Cross-Feature Combination C14: Empirical Challenger Suite for Milestone 4 Iteration 2 (F21).
Adversarial stress testing of Pydantic Guardrails (schemas.py), whitespace rejection,
exception wrapping, model validators, boundary conditions, and budget reconciliation.

Agent: challenger_m4_r2_1
"""

import json
from decimal import Decimal
from typing import Any, Dict, List

from tests_e2e.base import E2ESimpleTestCase

try:
    from pydantic import ValidationError
except ImportError:
    ValidationError = Exception


class TestC14EmpiricalChallengerM4R2Guardrails(E2ESimpleTestCase):
    """Empirical challenger test suite verifying M4 Iteration 2 Pydantic guardrail remediation."""

    def setUp(self):
        self.SugerenciaOrdenCompra = self.require_service("src.ai_forecast.schemas", "SugerenciaOrdenCompra", feature_id="F21")
        self.InsumoSugerido = self.require_service("src.ai_forecast.schemas", "InsumoSugerido", feature_id="F21")
        self.parse_and_validate = self.require_service("src.ai_forecast.schemas", "parse_and_validate_forecast_json", feature_id="F21")

        self.valid_item_dict = {
            "insumo_id": 10,
            "codigo": "INS-CARNE",
            "nombre": "Carne Vacuno Molida",
            "unidad_medida": "kg",
            "stock_actual": 3.0,
            "stock_minimo": 6.0,
            "consumo_diario_estimado": 1.5,
            "cantidad_sugerida": 7.5,
            "costo_unitario": 5000.0,
            "costo_subtotal": 37500.0,
            "justificacion": "Stock bajo nivel mínimo de seguridad; demanda semanal requiere reposición."
        }

    # =========================================================================
    # SECTION 1: Adversarial Whitespace Rejection and Sanitization
    # =========================================================================

    def test_c14_01_whitespace_only_strings_rejected_on_all_fields(self):
        """TC-C14-01: Whitespace strings must raise ValidationError on all string fields."""
        fields = ["codigo", "nombre", "unidad_medida", "justificacion"]
        whitespace_variations = [
            " ",
            "   ",
            "\t",
            "\n",
            "\r\n",
            "\t\n  \r ",
            "\u00a0\u00a0",  # Non-breaking space
        ]
        for field in fields:
            for ws in whitespace_variations:
                bad_data = dict(self.valid_item_dict, **{field: ws})
                with self.assertRaises(ValidationError, msg=f"Field '{field}' did not reject whitespace string {repr(ws)}"):
                    self.InsumoSugerido(**bad_data)

    def test_c14_02_empty_strings_rejected_on_all_fields(self):
        """TC-C14-02: Empty strings ('') must raise ValidationError on all string fields."""
        fields = ["codigo", "nombre", "unidad_medida", "justificacion"]
        for field in fields:
            bad_data = dict(self.valid_item_dict, **{field: ""})
            with self.assertRaises(ValidationError, msg=f"Field '{field}' did not reject empty string"):
                self.InsumoSugerido(**bad_data)

    def test_c14_03_none_values_rejected_on_all_string_fields(self):
        """TC-C14-03: None values must raise ValidationError on all string fields."""
        fields = ["codigo", "nombre", "unidad_medida", "justificacion"]
        for field in fields:
            bad_data = dict(self.valid_item_dict, **{field: None})
            with self.assertRaises(ValidationError, msg=f"Field '{field}' did not reject None"):
                self.InsumoSugerido(**bad_data)

    def test_c14_04_leading_trailing_whitespace_stripped_on_all_fields(self):
        """TC-C14-04: Leading/trailing whitespace must be automatically trimmed on all string fields."""
        raw_data = dict(
            self.valid_item_dict,
            codigo="   INS-POLLO-01   ",
            nombre="\t  Pechuga de Pollo Deshuesada  \n",
            unidad_medida="  kg \t",
            justificacion=" \n Reposición preventiva fin de semana. \r "
        )
        item = self.InsumoSugerido(**raw_data)
        self.assertEqual(item.codigo, "INS-POLLO-01")
        self.assertEqual(item.nombre, "Pechuga de Pollo Deshuesada")
        self.assertEqual(item.unidad_medida, "kg")
        self.assertEqual(item.justificacion, "Reposición preventiva fin de semana.")

    def test_c14_05_internal_whitespace_preserved_correctly(self):
        """TC-C14-05: Multi-word names and justifications must preserve internal spacing."""
        desc = "Insumo premium especial importado para recetas gourmet"
        item = self.InsumoSugerido(**dict(self.valid_item_dict, nombre=f"  {desc}  "))
        self.assertEqual(item.nombre, desc)

    # =========================================================================
    # SECTION 2: Boundary Checks on Quantities & Numeric Values
    # =========================================================================

    def test_c14_06_zero_quantity_accepted(self):
        """TC-C14-06: cantidad_sugerida = 0.0 is valid (no purchase required)."""
        item = self.InsumoSugerido(**dict(self.valid_item_dict, cantidad_sugerida=0.0, costo_subtotal=0.0))
        self.assertEqual(item.cantidad_sugerida, 0.0)
        self.assertEqual(item.costo_subtotal, 0.0)

    def test_c14_07_negative_quantity_rejected(self):
        """TC-C14-07: Negative quantities must raise ValidationError."""
        for neg_q in [-0.0001, -0.1, -1.0, -9999.0]:
            with self.assertRaises(ValidationError, msg=f"Negative quantity {neg_q} was not rejected"):
                self.InsumoSugerido(**dict(self.valid_item_dict, cantidad_sugerida=neg_q))

    def test_c14_08_decimal_precision_handling(self):
        """TC-C14-08: High-precision decimal quantities must be preserved and calculated accurately."""
        item = self.InsumoSugerido(**dict(
            self.valid_item_dict,
            cantidad_sugerida=0.375,
            costo_unitario=4000.0,
            costo_subtotal=1500.0
        ))
        self.assertAlmostEqual(item.cantidad_sugerida, 0.375)
        self.assertEqual(item.costo_subtotal, 1500.0)

    def test_c14_09_zero_unit_cost_accepted(self):
        """TC-C14-09: costo_unitario = 0.0 is valid (e.g. promotional or zero-cost supply)."""
        item = self.InsumoSugerido(**dict(self.valid_item_dict, costo_unitario=0.0, costo_subtotal=0.0))
        self.assertEqual(item.costo_unitario, 0.0)
        self.assertEqual(item.costo_subtotal, 0.0)

    def test_c14_10_negative_unit_cost_rejected(self):
        """TC-C14-10: Negative unit cost must raise ValidationError."""
        for neg_cost in [-0.01, -100.0]:
            with self.assertRaises(ValidationError):
                self.InsumoSugerido(**dict(self.valid_item_dict, costo_unitario=neg_cost))

    def test_c14_11_negative_stock_actual_allowed_for_kitchen_drift(self):
        """TC-C14-11: Negative stock_actual is permissible due to real kitchen operational drift."""
        item = self.InsumoSugerido(**dict(self.valid_item_dict, stock_actual=-4.5))
        self.assertEqual(item.stock_actual, -4.5)

    def test_c14_12_negative_stock_minimo_rejected(self):
        """TC-C14-12: Negative stock_minimo must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, stock_minimo=-0.5))

    def test_c14_13_negative_consumo_diario_rejected(self):
        """TC-C14-13: Negative consumo_diario_estimado must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**dict(self.valid_item_dict, consumo_diario_estimado=-1.0))

    def test_c14_14_non_positive_insumo_id_rejected(self):
        """TC-C14-14: insumo_id <= 0 must raise ValidationError."""
        for bad_id in [0, -1, -99]:
            with self.assertRaises(ValidationError):
                self.InsumoSugerido(**dict(self.valid_item_dict, insumo_id=bad_id))

    # =========================================================================
    # SECTION 3: Cost Calculations and Budget Reconciliation
    # =========================================================================

    def test_c14_15_subtotal_reconciled_when_divergent(self):
        """TC-C14-15: Subtotal must be auto-reconciled when difference exceeds 0.05 tolerance."""
        bad_item = dict(self.valid_item_dict, cantidad_sugerida=5.0, costo_unitario=1200.0, costo_subtotal=999.0)
        item = self.InsumoSugerido(**bad_item)
        self.assertEqual(item.costo_subtotal, 6000.0)

    def test_c14_16_subtotal_tolerance_preserved_within_limit(self):
        """TC-C14-16: Subtotal difference within 0.05 is preserved as valid rounding."""
        tolerance_item = dict(self.valid_item_dict, cantidad_sugerida=2.0, costo_unitario=1000.0, costo_subtotal=2000.03)
        item = self.InsumoSugerido(**tolerance_item)
        self.assertEqual(item.costo_subtotal, 2000.03)

    def test_c14_17_order_budget_auto_reconciled_to_items_sum(self):
        """TC-C14-17: SugerenciaOrdenCompra must reconcile budget to sum of item subtotals."""
        item1 = dict(self.valid_item_dict, insumo_id=1, cantidad_sugerida=2.0, costo_unitario=1500.0, costo_subtotal=3000.0)
        item2 = dict(self.valid_item_dict, insumo_id=2, cantidad_sugerida=4.0, costo_unitario=2500.0, costo_subtotal=10000.0)
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[item1, item2],
            presupuesto_estimado_total=500.0  # Divergent budget
        )
        self.assertEqual(order.presupuesto_estimado_total, 13000.0)

    def test_c14_18_order_budget_tolerance_preserved_within_one_peso(self):
        """TC-C14-18: Budget difference <= 1.0 CLP is preserved."""
        item = dict(self.valid_item_dict, cantidad_sugerida=2.0, costo_unitario=1000.0, costo_subtotal=2000.0)
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[item],
            presupuesto_estimado_total=2000.75
        )
        self.assertEqual(order.presupuesto_estimado_total, 2000.75)

    def test_c14_19_empty_order_budget_reconciles_to_zero(self):
        """TC-C14-19: Order with empty items list must reconcile divergent budget to 0.0."""
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[],
            presupuesto_estimado_total=50000.0
        )
        self.assertEqual(order.presupuesto_estimado_total, 0.0)

    def test_c14_20_negative_order_budget_rejected(self):
        """TC-C14-20: Negative presupuesto_estimado_total must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.SugerenciaOrdenCompra(items_sugeridos=[], presupuesto_estimado_total=-100.0)

    def test_c14_21_periodo_dias_minimum_boundary(self):
        """TC-C14-21: periodo_dias < 1 must raise ValidationError."""
        for bad_p in [0, -1, -30]:
            with self.assertRaises(ValidationError):
                self.SugerenciaOrdenCompra(items_sugeridos=[], presupuesto_estimado_total=0.0, periodo_dias=bad_p)

    # =========================================================================
    # SECTION 4: Exception Wrapping and Hierarchy
    # =========================================================================

    def test_c14_22_field_validator_exceptions_wrapped_in_validation_error(self):
        """TC-C14-22: All validation failures raise ValidationError (inherits from ValueError)."""
        try:
            self.InsumoSugerido(**dict(self.valid_item_dict, justificacion="   "))
            self.fail("Expected ValidationError")
        except ValidationError as e:
            self.assertIsInstance(e, ValueError, "ValidationError must be a subclass of ValueError")
            errors = e.errors()
            self.assertIsInstance(errors, list)
            self.assertGreater(len(errors), 0)

    def test_c14_23_invalid_item_type_in_container_raises_validation_error(self):
        """TC-C14-23: Items that are neither dict nor InsumoSugerido raise ValidationError."""
        for invalid_elem in [42, "string_item", None, True]:
            with self.assertRaises((ValidationError, TypeError)):
                self.SugerenciaOrdenCompra(items_sugeridos=[invalid_elem], presupuesto_estimado_total=0.0)

    # =========================================================================
    # SECTION 5: Dual Serialization Equivalence
    # =========================================================================

    def test_c14_24_dual_serialization_model_dump_vs_dict(self):
        """TC-C14-24: model_dump() and dict() produce identical deep dictionaries."""
        item1 = dict(self.valid_item_dict, insumo_id=1, codigo="INS-01", costo_subtotal=37500.0)
        item2 = dict(self.valid_item_dict, insumo_id=2, codigo="INS-02", costo_subtotal=10000.0)
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[item1, item2],
            presupuesto_estimado_total=47500.0,
            periodo_dias=14,
            metodo="HEURISTIC_FALLBACK"
        )
        d_v2 = order.model_dump()
        d_v1 = order.dict()

        self.assertEqual(d_v2, d_v1)
        self.assertIsInstance(d_v2["items_sugeridos"][0], dict)
        self.assertIsInstance(d_v2["items_sugeridos"][1], dict)
        self.assertEqual(d_v2["metodo"], "HEURISTIC_FALLBACK")
        self.assertEqual(d_v2["periodo_dias"], 14)

    def test_c14_25_json_roundtrip_equivalence(self):
        """TC-C14-25: JSON serialization roundtrip preserves all schema values exactly."""
        order = self.SugerenciaOrdenCompra(
            items_sugeridos=[self.valid_item_dict],
            presupuesto_estimado_total=37500.0,
            periodo_dias=7,
            metodo="LLM_GENERATED"
        )
        dumped = order.model_dump()
        json_serialized = json.dumps(dumped)
        reloaded_dict = json.loads(json_serialized)
        reconstructed = self.SugerenciaOrdenCompra.model_validate(reloaded_dict)

        self.assertEqual(reconstructed.model_dump(), dumped)
        self.assertEqual(len(reconstructed.items_sugeridos), 1)
        self.assertEqual(reconstructed.items_sugeridos[0].codigo, "INS-CARNE")

    # =========================================================================
    # SECTION 6: Parser & Sanitizer Robustness (parse_and_validate_forecast_json)
    # =========================================================================

    def test_c14_26_parser_strips_nested_markdown_and_conversational_text(self):
        """TC-C14-26: parse_and_validate_forecast_json recovers JSON inside markdown fences and text."""
        order_dict = {
            "items_sugeridos": [self.valid_item_dict],
            "presupuesto_estimado_total": 37500.0,
            "periodo_dias": 7,
            "metodo": "LLM_GENERATED"
        }
        test_inputs = [
            f"```json\n{json.dumps(order_dict)}\n```",
            f"```\n{json.dumps(order_dict)}\n```",
            f"Estimado equipo de cocina, adjunto reporte:\n```json\n{json.dumps(order_dict)}\n```\nSaludos.",
            f"A continuación el JSON sugerido: {json.dumps(order_dict)} Que tenga buen turno."
        ]
        for inp in test_inputs:
            obj = self.parse_and_validate(inp)
            self.assertIsInstance(obj, self.SugerenciaOrdenCompra)
            self.assertEqual(len(obj.items_sugeridos), 1)
            self.assertEqual(obj.items_sugeridos[0].insumo_id, 10)

    def test_c14_27_parser_rejects_empty_or_whitespace_strings(self):
        """TC-C14-27: Empty or whitespace LLM response raises ValueError."""
        for bad_inp in ["", "   ", "\n\t  \r"]:
            with self.assertRaises(ValueError):
                self.parse_and_validate(bad_inp)

    def test_c14_28_parser_rejects_corrupted_json_syntax(self):
        """TC-C14-28: Malformed JSON syntax raises JSONDecodeError / ValueError."""
        for corrupted in ["{bad json", "{'items_sugeridos': [}", ""]:
            with self.assertRaises((json.JSONDecodeError, ValueError)):
                self.parse_and_validate(corrupted)

    def test_c14_29_parser_rejects_schema_violations_in_valid_json(self):
        """TC-C14-29: Syntactically valid JSON with schema violation raises ValidationError."""
        bad_payload = {
            "items_sugeridos": [dict(self.valid_item_dict, cantidad_sugerida=-50.0)],
            "presupuesto_estimado_total": 0.0
        }
        with self.assertRaises(ValidationError):
            self.parse_and_validate(json.dumps(bad_payload))
