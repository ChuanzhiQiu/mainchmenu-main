"""
Tier 1 — Feature F01: Dependency Isolation & Cloud Compatibility.
Verifies requirements.txt platform environment markers, cloud dependencies, and syntax.
"""

from pathlib import Path
from tests_e2e.base import E2ESimpleTestCase


class TestF01Dependencies(E2ESimpleTestCase):
    """Test suite for Feature F01: Dependency Isolation."""

    def setUp(self):
        self.req_path = self.PROJECT_ROOT / "requirements.txt"
        self.assertTrue(self.req_path.exists(), "[F01] requirements.txt must exist in project root")
        self.lines = [line.strip() for line in self.req_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]

    def test_f01_01_requirements_file_exists_and_readable(self):
        """TC-F01-01: Verify requirements.txt is readable and non-empty."""
        self.assertGreater(len(self.lines), 0, "[F01] requirements.txt must contain dependency declarations")

    def test_f01_02_pywin32_has_platform_marker(self):
        """TC-F01-02: pywin32 must have sys_platform == 'win32' marker to prevent Linux build failure."""
        pywin_lines = [line for line in self.lines if "pywin32" in line.lower()]
        if pywin_lines:
            for line in pywin_lines:
                self.assertIn("sys_platform", line, f"[F01] Line '{line}' missing platform marker")
                self.assertIn("win32", line, f"[F01] Line '{line}' must specify win32 platform marker")
        else:
            # If pywin32 is completely removed, it also satisfies Linux/macOS isolation
            pass

    def test_f01_03_cloud_database_adapter_declared(self):
        """TC-F01-03: requirements.txt must declare dj-database-url for Supabase PostgreSQL config."""
        has_dj_db = any("dj-database-url" in line.lower() for line in self.lines)
        self.assertTrue(has_dj_db, "[F01] dj-database-url must be declared in requirements.txt")

    def test_f01_04_whitenoise_and_gunicorn_declared(self):
        """TC-F01-04: requirements.txt must declare whitenoise and gunicorn for PaaS deployment."""
        has_whitenoise = any("whitenoise" in line.lower() for line in self.lines)
        has_gunicorn = any("gunicorn" in line.lower() for line in self.lines)
        self.assertTrue(has_whitenoise, "[F01] whitenoise must be declared in requirements.txt for static serving")
        self.assertTrue(has_gunicorn, "[F01] gunicorn must be declared in requirements.txt for container runtime")

    def test_f01_05_pydantic_declared_for_guardrails(self):
        """TC-F01-05: requirements.txt must declare pydantic for structured AI schema guardrails."""
        has_pydantic = any("pydantic" in line.lower() for line in self.lines)
        self.assertTrue(has_pydantic, "[F01] pydantic must be declared in requirements.txt for R4 guardrails")
