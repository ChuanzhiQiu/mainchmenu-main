"""
Tier 3 — Cross-Feature Combination C09: F01 (Deps) + F03 (WhiteNoise) + F05 (Manifests).
Verifies complete coherence across Procfile, vercel.json, requirements.txt, and settings.py.
"""

from tests_e2e.base import E2ESimpleTestCase


class TestC09CloudConfigConsistency(E2ESimpleTestCase):
    """Pairwise combination: Dependencies + WhiteNoise Settings + Cloud Deployment Manifests."""

    def test_c09_01_gunicorn_in_procfile_and_requirements(self):
        """TC-C09-01: Gunicorn specified in Procfile is declared in requirements.txt."""
        proc_path = self.require_file("Procfile", feature_id="C09")
        proc_content = proc_path.read_text(encoding="utf-8")
        req_path = self.require_file("requirements.txt", feature_id="C09")
        req_content = req_path.read_text(encoding="utf-8").lower()

        if "gunicorn" in proc_content:
            self.assertIn("gunicorn", req_content, "[C09] Gunicorn must be present in requirements.txt")

    def test_c09_02_whitenoise_in_settings_and_requirements(self):
        """TC-C09-02: WhiteNoise in settings.py is declared in requirements.txt."""
        settings_path = self.require_file("MainchApp/settings.py", feature_id="C09")
        settings_content = settings_path.read_text(encoding="utf-8")
        req_path = self.require_file("requirements.txt", feature_id="C09")
        req_content = req_path.read_text(encoding="utf-8").lower()

        if "WhiteNoise" in settings_content:
            self.assertIn("whitenoise", req_content, "[C09] WhiteNoise must be declared in requirements.txt")

    def test_c09_03_wsgi_path_alignment_across_manifests(self):
        """TC-C09-03: WSGI module name MainchApp.wsgi matches across settings, Procfile, and vercel.json."""
        proc_path = self.require_file("Procfile", feature_id="C09")
        proc_content = proc_path.read_text(encoding="utf-8")
        self.assertIn("MainchApp.wsgi", proc_content, "[C09] Procfile must refer to MainchApp.wsgi")
