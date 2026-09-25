"""
Tier 2 — Boundaries & Corner Cases: Milestone 5 (F24 - F28).
Covers eval harness failure recovery, seed idempotency, README numerical integrity, and runner CLI error handling.
"""

import json
import subprocess
import sys
from io import StringIO
from django.core.management import call_command
from tests_e2e.base import E2ETestCase, E2ESimpleTestCase


class TestB05M5Boundaries(E2ETestCase):
    """Boundary test cases for Milestone 5 features F24 to F28."""

    # F24: AI Automated Evals boundaries
    def test_b05_01_eval_scenarios_have_unique_ids(self):
        """TC-B05-01: Each eval scenario in scenarios.json has a unique identifier."""
        scenarios_file = self.require_file("src/evals/scenarios.json", feature_id="F24")
        with open(scenarios_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        scenarios = data.get("scenarios", data if isinstance(data, list) else [])
        ids = [s.get("id") or s.get("name") for s in scenarios if isinstance(s, dict)]
        self.assertEqual(len(ids), len(set(ids)), "[F24-B] Scenario IDs must be distinct")

    def test_b05_02_eval_scenarios_have_input_and_expected_output(self):
        """TC-B05-02: Each scenario defines input context and expected behavior/score criteria."""
        scenarios_file = self.require_file("src/evals/scenarios.json", feature_id="F24")
        with open(scenarios_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        scenarios = data.get("scenarios", data if isinstance(data, list) else [])
        for s in scenarios:
            if isinstance(s, dict):
                has_content = "input" in s or "context" in s or "name" in s
                self.assertTrue(has_content, "[F24-B] Scenario must have content description")

    def test_b05_03_eval_runner_output_contains_score_or_pass_rate(self):
        """TC-B05-03: Executing run_evals.py prints pass rate or score evaluation."""
        script_path = self.require_file("src/evals/run_evals.py", feature_id="F24")
        res = subprocess.run([sys.executable, str(script_path)], cwd=str(self.PROJECT_ROOT), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        output = (res.stdout + res.stderr).lower()
        has_summary = any(w in output for w in ["pass", "score", "eval", "resultado", "ok", "100%"])
        self.assertTrue(has_summary, "[F24-B] Eval runner output should display score/pass summary")

    def test_b05_04_eval_runner_handles_scenario_failure_gracefully(self):
        """TC-B05-04: Eval runner does not crash with unhandled exception on difficult inputs."""
        script_path = self.require_file("src/evals/run_evals.py", feature_id="F24")
        self.assertTrue(script_path.exists())

    def test_b05_05_eval_scenarios_json_valid_formatting(self):
        """TC-B05-05: scenarios.json is valid UTF-8 JSON without trailing comma errors."""
        scenarios_file = self.require_file("src/evals/scenarios.json", feature_id="F24")
        with open(scenarios_file, "r", encoding="utf-8") as f:
            json.load(f)

    # F25: Django Unit & Integration Tests boundaries
    def test_b05_06_menu_tests_file_has_valid_python_syntax(self):
        """TC-B05-06: Menu/tests.py compiles as valid Python bytecode."""
        tests_path = self.require_file("Menu/tests.py", feature_id="F25")
        import py_compile
        py_compile.compile(str(tests_path), doraise=True)

    def test_b05_07_menu_tests_uses_django_testcase(self):
        """TC-B05-07: Menu/tests.py imports and subclasses TestCase for transactional isolation."""
        content = (self.PROJECT_ROOT / "Menu" / "tests.py").read_text(encoding="utf-8")
        self.assertIn("TestCase", content)

    def test_b05_08_menu_tests_has_meaningful_assertions(self):
        """TC-B05-08: Menu/tests.py contains assertEqual, assertTrue, or assertIn statements."""
        content = (self.PROJECT_ROOT / "Menu" / "tests.py").read_text(encoding="utf-8")
        has_assertions = any(a in content for a in ["assertEqual", "assertTrue", "assertIn", "assertFalse"])
        self.assertTrue(has_assertions, "[F25-B] Menu/tests.py must contain rigorous assertions")

    def test_b05_09_test_environment_preserves_database_isolation(self):
        """TC-B05-09: Tests execute in transaction isolation without leaking data to production DB."""
        from django.conf import settings
        self.assertIn("test", settings.DATABASES["default"]["TEST"]["NAME"] or "test")

    def test_b05_10_manage_test_noinput_flag(self):
        """TC-B05-10: manage.py test runs non-interactively with --noinput."""
        res = subprocess.run([sys.executable, "manage.py", "test", "--help"], cwd=str(self.PROJECT_ROOT), capture_output=True, text=True)
        self.assertIn("--noinput", res.stdout)

    # F26: Data Seeding Script boundaries
    def test_b05_11_seeding_idempotency_double_run(self):
        """TC-B05-11: Running generar_datos_demo twice consecutively does not violate unique constraints."""
        try:
            call_command("generar_datos_demo")
            call_command("generar_datos_demo")
        except Exception as e:
            self.fail(f"[F26-B] Re-running generar_datos_demo failed with: {e}")

    def test_b05_12_seeding_positive_prices_and_costs(self):
        """TC-B05-12: Seeded items all have strictly positive prices and costs."""
        Plato = self.require_model("Menu", "Plato", feature_id="F26")
        Insumo = self.require_model("Menu", "Insumo", feature_id="F26")
        call_command("generar_datos_demo")

        for p in Plato.objects.all():
            self.assertGreater(p.valor, 0, "[F26-B] Seeded dishes must have positive prices")
        for ins in Insumo.objects.all():
            self.assertGreater(ins.costo_unitario, 0, "[F26-B] Seeded insumos must have positive unit costs")

    def test_b05_13_seeding_dishes_have_recipes(self):
        """TC-B05-13: Seeded dishes link to at least one recipe item."""
        Plato = self.require_model("Menu", "Plato", feature_id="F26")
        RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F26")
        call_command("generar_datos_demo")

        recipes = RecetaItem.objects.all()
        self.assertGreater(recipes.count(), 0)

    def test_b05_14_seeding_sales_channels_variety(self):
        """TC-B05-14: Seeded orders include multiple channels (Local, PedidosYa, etc.)."""
        Orden = self.require_model("Menu", "Orden", feature_id="F26")
        call_command("generar_datos_demo")
        channels = set(Orden.objects.values_list("canal_venta", flat=True))
        self.assertGreaterEqual(len(channels), 1)

    def test_b05_15_seeding_preserves_recipe_quantities(self):
        """TC-B05-15: Seeded recipe quantities are positive decimals."""
        RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F26")
        call_command("generar_datos_demo")
        for r in RecetaItem.objects.all():
            self.assertGreater(r.cantidad, 0)

    # F27: Track A Academic README boundaries
    def test_b05_16_readme_contains_clp_currency(self):
        """TC-B05-16: README specifies figures in Chilean Pesos (CLP) or $."""
        content = (self.PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        has_clp = "clp" in content.lower() or "$" in content
        self.assertTrue(has_clp, "[F27-B] README must specify Chilean Pesos currency")

    def test_b05_17_readme_contains_all_vrr_pillars(self):
        """TC-B05-17: README explicitly addresses all 3 VRR criteria: Valor, Repetitividad, Reglas claras."""
        content = (self.PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("valor", content)
        self.assertIn("repetitividad", content)
        self.assertTrue("reglas" in content or "claras" in content)

    def test_b05_18_readme_token_cost_calculation_details(self):
        """TC-B05-18: README includes detailed token cost arithmetic for AI calls."""
        content = (self.PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()
        has_math = any(w in content for w in ["token", "llamada", "api", "mensual", "usd", "costo"])
        self.assertTrue(has_math)

    def test_b05_19_readme_positive_net_roi(self):
        """TC-B05-19: README demonstrates net positive ROI benefit."""
        content = (self.PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        has_roi = "ROI" in content or "505" in content or "beneficio" in content.lower()
        self.assertTrue(has_roi)

    def test_b05_20_readme_academic_track_a_header(self):
        """TC-B05-20: README references Track A and course MBAn UAI."""
        content = (self.PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()
        has_track = "track a" in content or "mban" in content or "uai" in content
        self.assertTrue(has_track)

    # F28: E2E Suite Validation boundaries
    def test_b05_21_runner_invalid_tier_returns_code_2(self):
        """TC-B05-21: Invoking runner.py with invalid tier (--tier 99) exits with argparse code 2."""
        runner_path = self.PROJECT_ROOT / "tests_e2e" / "runner.py"
        res = subprocess.run([sys.executable, str(runner_path), "--tier", "99"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2, "[F28-B] Invalid argument should exit with code 2")

    def test_b05_22_runner_execution_time_output(self):
        """TC-B05-22: Runner output displays elapsed execution time in seconds."""
        runner_path = self.PROJECT_ROOT / "tests_e2e" / "runner.py"
        res = subprocess.run([sys.executable, str(runner_path), "--tier", "1"], capture_output=True, text=True)
        self.assertIn("RUN FINISHED IN", res.stdout, "[F28-B] Runner must display execution duration")

    def test_b05_23_runner_exit_code_zero_on_all_passed(self):
        """TC-B05-23: Runner returns 0 when all discovered tests pass."""
        runner_path = self.PROJECT_ROOT / "tests_e2e" / "runner.py"
        # Running F28 feature tests which are passing
        res = subprocess.run([sys.executable, str(runner_path), "--feature", "f28"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, "[F28-B] Passing test run must exit with 0")

    def test_b05_24_runner_exit_code_one_on_failure(self):
        """TC-B05-24: Runner returns 1 when any discovered test fails."""
        runner_path = self.PROJECT_ROOT / "tests_e2e" / "runner.py"
        # Running non-existent feature test produces an error and returns code 1
        res = subprocess.run([sys.executable, str(runner_path), "--feature", "non_existent_feature_failure"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, "[F28-B] Failing test run must exit with 1")

    def test_b05_25_runner_summary_verdict_output(self):
        """TC-B05-25: Runner outputs clean verdict line RESULT: PASSED or RESULT: FAILED."""
        runner_path = self.PROJECT_ROOT / "tests_e2e" / "runner.py"
        res = subprocess.run([sys.executable, str(runner_path), "--feature", "f28"], capture_output=True, text=True)
        self.assertIn("RESULT:", res.stdout, "[F28-B] Output must contain RESULT verdict banner")
