"""
Tier 1 — Feature F20: Versioned AI System Prompt.
Verifies existence, content, formatting rules, and guardrails instructions in system prompt.
"""

from tests_e2e.base import E2ESimpleTestCase


class TestF20AIPrompt(E2ESimpleTestCase):
    """Test suite for Feature F20: Versioned AI System Prompt."""

    def setUp(self):
        self.prompt_path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"
        self.assertTrue(self.prompt_path.exists(), "[F20] src/prompts/demand_forecaster_system.md must exist")
        self.content = self.prompt_path.read_text(encoding="utf-8")

    def test_f20_01_prompt_file_readable_and_substantial(self):
        """TC-F20-01: System prompt file is readable and contains sufficient operational instructions."""
        self.assertGreater(len(self.content.strip()), 200, "[F20] System prompt must contain at least 200 characters")

    def test_f20_02_role_and_domain_definition(self):
        """TC-F20-02: Prompt defines domain role (restaurant inventory / supply expert)."""
        has_domain = any(w in self.content.lower() for w in ["inventario", "restaurante", "abastecimiento", "demanda", "chef", "compras"])
        self.assertTrue(has_domain, "[F20] Prompt must define restaurant inventory forecasting role")

    def test_f20_03_json_output_instruction_present(self):
        """TC-F20-03: Prompt strictly specifies JSON format output."""
        self.assertIn("json", self.content.lower(), "[F20] Prompt must instruct JSON output format")

    def test_f20_04_schema_fields_specified_in_prompt(self):
        """TC-F20-04: Prompt outlines required fields (e.g. items_sugeridos, cantidad, justificacion)."""
        has_schema_hints = (
            "items_sugeridos" in self.content
            or "cantidad_sugerida" in self.content
            or "justificacion" in self.content
            or "presupuesto" in self.content
        )
        self.assertTrue(has_schema_hints, "[F20] Prompt must reference required structured schema keys")

    def test_f20_05_guardrail_against_conversational_filler(self):
        """TC-F20-05: Prompt forbids conversational preamble or markdown backticks formatting."""
        has_guard = any(w in self.content.lower() for w in ["únicamente json", "solamente json", "no incluyas texto", "sin formato adicional", "raw json", "only json"])
        self.assertTrue(has_guard, "[F20] Prompt must instruct LLM to avoid conversational filler")
