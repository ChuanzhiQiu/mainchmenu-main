"""
Tier 1 — Feature F05: Deployment Manifests for Cloud Hosting (Vercel & Render/Railway).
Verifies presence, validity, and configuration of vercel.json, Procfile, and wsgi.py.
"""

import json
from pathlib import Path
from tests_e2e.base import E2ESimpleTestCase


class TestF05DeploymentManifests(E2ESimpleTestCase):
    """Test suite for Feature F05: Deployment Manifests."""

    def test_f05_01_vercel_json_exists(self):
        """TC-F05-01: vercel.json must exist in project root."""
        self.require_file("vercel.json", feature_id="F05")

    def test_f05_02_vercel_json_is_valid_json_with_builds_or_routes(self):
        """TC-F05-02: vercel.json must parse as valid JSON and configure python WSGI entry point."""
        vercel_path = self.require_file("vercel.json", feature_id="F05")
        with open(vercel_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict, "[F05] vercel.json root must be a JSON object")
        has_wsgi = (
            any("wsgi.py" in str(v) for v in data.get("builds", []))
            or any("wsgi.py" in str(v) for v in data.get("routes", []))
            or "MainchApp" in str(data)
        )
        self.assertTrue(has_wsgi, "[F05] vercel.json must point to MainchApp WSGI application")

    def test_f05_03_procfile_exists(self):
        """TC-F05-03: Procfile must exist in project root for container PaaS deployments."""
        self.require_file("Procfile", feature_id="F05")

    def test_f05_04_procfile_configures_gunicorn_wsgi(self):
        """TC-F05-04: Procfile must configure gunicorn web process pointing to MainchApp.wsgi."""
        procfile_path = self.require_file("Procfile", feature_id="F05")
        content = procfile_path.read_text(encoding="utf-8")
        self.assertIn("web:", content, "[F05] Procfile must define a 'web:' process")
        self.assertIn("gunicorn", content, "[F05] Procfile web process must use gunicorn")
        self.assertIn("MainchApp.wsgi", content, "[F05] Procfile web process must invoke MainchApp.wsgi")

    def test_f05_05_wsgi_application_callable(self):
        """TC-F05-05: MainchApp.wsgi must export standard callable 'application'."""
        wsgi_path = self.require_file("MainchApp/wsgi.py", feature_id="F05")
        from MainchApp.wsgi import application
        self.assertTrue(callable(application), "[F05] MainchApp.wsgi.application must be a callable WSGI handler")
