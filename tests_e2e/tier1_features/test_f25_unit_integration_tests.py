"""
Tier 1 — Feature F25: Django Unit & Integration Test Suite.
Verifies Menu/tests.py coverage, non-zero test execution, and pass status in manage.py test.
"""

import subprocess
import sys
from tests_e2e.base import E2ESimpleTestCase


class TestF25UnitIntegrationTests(E2ESimpleTestCase):
    """Test suite for Feature F25: Django Unit & Integration Tests."""

    def setUp(self):
        self.tests_path = self.PROJECT_ROOT / "Menu" / "tests.py"
        self.assertTrue(self.tests_path.exists(), "[F25] Menu/tests.py must exist")
        self.content = self.tests_path.read_text(encoding="utf-8")

    def test_f25_01_menu_tests_has_test_cases(self):
        """TC-F25-01: Menu/tests.py contains test method definitions (def test_)."""
        test_defs = [line for line in self.content.splitlines() if line.strip().startswith("def test_")]
        self.assertGreaterEqual(len(test_defs), 5, "[F25] Menu/tests.py must contain at least 5 unit/integration test methods")

    def test_f25_02_stock_deduction_tested(self):
        """TC-F25-02: Menu/tests.py includes test cases exercising stock deduction."""
        has_stock = "stock" in self.content.lower() or "descontar" in self.content.lower() or "insumo" in self.content.lower()
        self.assertTrue(has_stock, "[F25] Menu/tests.py must test stock deduction functionality")

    def test_f25_03_order_lifecycle_tested(self):
        """TC-F25-03: Menu/tests.py includes test cases exercising order lifecycle."""
        has_order = "orden" in self.content.lower() or "pedido" in self.content.lower()
        self.assertTrue(has_order, "[F25] Menu/tests.py must test order creation or completion")

    def test_f25_04_ai_or_guardrails_tested(self):
        """TC-F25-04: Menu/tests.py tests AI schemas or fallback engine."""
        has_ai = "sugerencia" in self.content.lower() or "guardrail" in self.content.lower() or "fallback" in self.content.lower() or "forecast" in self.content.lower()
        self.assertTrue(has_ai, "[F25] Menu/tests.py should cover AI validation or forecasting")

    def test_f25_05_manage_test_menu_runs_and_passes(self):
        """TC-F25-05: Running python manage.py test Menu executes and passes with code 0."""
        result = subprocess.run(
            [sys.executable, "manage.py", "test", "Menu", "--noinput"],
            cwd=str(self.PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0, f"[F25] 'manage.py test Menu' failed: {result.stderr or result.stdout}")
        self.assertNotIn("Ran 0 tests", result.stderr + result.stdout, "[F25] 'manage.py test Menu' must run non-zero tests")
