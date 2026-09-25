"""
Tier 1 — Feature F24: AI Automated Evaluation Suite.
Verifies existence of scenarios.json (5 scenarios) and execution of src/evals/run_evals.py harness.
"""

import json
import subprocess
import sys
from tests_e2e.base import E2ESimpleTestCase


class TestF24EvalsSuite(E2ESimpleTestCase):
    """Test suite for Feature F24: AI Automated Evals Suite."""

    def test_f24_01_scenarios_json_exists(self):
        """TC-F24-01: src/evals/scenarios.json must exist in repository."""
        self.require_file("src/evals/scenarios.json", feature_id="F24")

    def test_f24_02_scenarios_json_contains_five_scenarios(self):
        """TC-F24-02: scenarios.json contains at least 5 distinct test scenarios."""
        scenarios_file = self.require_file("src/evals/scenarios.json", feature_id="F24")
        with open(scenarios_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        scenarios = data.get("scenarios", data if isinstance(data, list) else [])
        self.assertGreaterEqual(len(scenarios), 5, "[F24] scenarios.json must contain at least 5 operational test cases")

    def test_f24_03_run_evals_script_exists(self):
        """TC-F24-03: src/evals/run_evals.py test harness script exists."""
        self.require_file("src/evals/run_evals.py", feature_id="F24")

    def test_f24_04_run_evals_script_callable_as_process(self):
        """TC-F24-04: Executing run_evals.py with python finishes without crashing."""
        script_path = self.require_file("src/evals/run_evals.py", feature_id="F24")
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(self.PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0, f"[F24] run_evals.py failed with stderr: {result.stderr}")

    def test_f24_05_eval_scenarios_cover_quiebre_and_fallback(self):
        """TC-F24-05: Scenarios verify critical cases: quiebre, normal demand, and corrupted/fallback."""
        scenarios_file = self.require_file("src/evals/scenarios.json", feature_id="F24")
        with open(scenarios_file, "r", encoding="utf-8") as f:
            content = f.read().lower()
        self.assertTrue(
            "quiebre" in content or "stockout" in content or "crisis" in content,
            "[F24] Scenarios must include stockout crisis"
        )
        self.assertTrue(
            "fallback" in content or "corrupt" in content or "heuristico" in content,
            "[F24] Scenarios must include fallback/malformed output recovery"
        )
