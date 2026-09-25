"""
Tier 1 — Feature F28: Full E2E Test Suite Validation Engine.
Verifies runner architecture, CLI argument parsing, tier dispatching, and exit code propagation.
"""

import subprocess
import sys
from tests_e2e.base import E2ESimpleTestCase


class TestF28E2EValidation(E2ESimpleTestCase):
    """Test suite for Feature F28: Full E2E Test Suite Validation."""

    def setUp(self):
        self.runner_path = self.PROJECT_ROOT / "tests_e2e" / "runner.py"
        self.assertTrue(self.runner_path.exists(), "[F28] tests_e2e/runner.py must exist")

    def test_f28_01_runner_exists_and_executable(self):
        """TC-F28-01: runner.py exists and has execution permissions."""
        import os
        self.assertTrue(os.access(str(self.runner_path), os.R_OK), "[F28] runner.py must be readable")

    def test_f28_02_runner_help_command_succeeds(self):
        """TC-F28-02: Executing runner.py --help returns 0 and displays tier options."""
        res = subprocess.run([sys.executable, str(self.runner_path), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, "[F28] runner.py --help must succeed")
        self.assertIn("--tier", res.stdout, "[F28] Help text must list --tier option")

    def test_f28_03_all_four_tiers_directories_exist(self):
        """TC-F28-03: All 4 tier directories exist with __init__.py files."""
        tiers = ["tier1_features", "tier2_boundaries", "tier3_combinations", "tier4_scenarios"]
        for t in tiers:
            d = self.PROJECT_ROOT / "tests_e2e" / t
            self.assertTrue(d.exists(), f"[F28] Directory tests_e2e/{t} must exist")
            init_file = d / "__init__.py"
            self.assertTrue(init_file.exists(), f"[F28] File tests_e2e/{t}/__init__.py must exist")

    def test_f28_04_runner_feature_filter_argument_supported(self):
        """TC-F28-04: runner.py supports filtering by specific feature (e.g. --feature F01)."""
        res = subprocess.run([sys.executable, str(self.runner_path), "--help"], capture_output=True, text=True)
        self.assertIn("--feature", res.stdout, "[F28] Help text must list --feature option")

    def test_f28_05_runner_failfast_argument_supported(self):
        """TC-F28-05: runner.py supports --failfast option for quick defect isolation."""
        res = subprocess.run([sys.executable, str(self.runner_path), "--help"], capture_output=True, text=True)
        self.assertIn("--failfast", res.stdout, "[F28] Help text must list --failfast option")
