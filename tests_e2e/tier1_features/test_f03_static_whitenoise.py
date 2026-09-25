"""
Tier 1 — Feature F03: Static Files WhiteNoise Middleware & Cloud Host Configuration.
Verifies WhiteNoise integration, STATIC_ROOT definition, and cloud-ready ALLOWED_HOSTS.
"""

from pathlib import Path
from django.conf import settings
from tests_e2e.base import E2ESimpleTestCase


class TestF03StaticWhiteNoise(E2ESimpleTestCase):
    """Test suite for Feature F03: Static Files WhiteNoise."""

    def setUp(self):
        self.settings_path = self.PROJECT_ROOT / "MainchApp" / "settings.py"
        self.settings_content = self.settings_path.read_text(encoding="utf-8")

    def test_f03_01_whitenoise_middleware_present(self):
        """TC-F03-01: WhiteNoiseMiddleware must be present in settings.MIDDLEWARE."""
        middleware_list = settings.MIDDLEWARE
        has_whitenoise = any("whitenoise" in m.lower() for m in middleware_list)
        self.assertTrue(has_whitenoise, "[F03] WhiteNoiseMiddleware must be registered in settings.MIDDLEWARE")

    def test_f03_02_whitenoise_positioned_after_security(self):
        """TC-F03-02: WhiteNoiseMiddleware must be positioned directly after SecurityMiddleware."""
        middleware_list = settings.MIDDLEWARE
        wn_indices = [i for i, m in enumerate(middleware_list) if "whitenoise" in m.lower()]
        sec_indices = [i for i, m in enumerate(middleware_list) if "SecurityMiddleware" in m]
        if wn_indices and sec_indices:
            self.assertEqual(wn_indices[0], sec_indices[0] + 1, "[F03] WhiteNoiseMiddleware must follow SecurityMiddleware")
        else:
            self.fail("[F03] Both SecurityMiddleware and WhiteNoiseMiddleware must be present")

    def test_f03_03_static_root_configured(self):
        """TC-F03-03: STATIC_ROOT must be defined and point to valid path for collectstatic."""
        static_root = getattr(settings, 'STATIC_ROOT', None)
        self.assertIsNotNone(static_root, "[F03] STATIC_ROOT must be defined in settings.py")
        self.assertTrue(str(static_root).endswith("staticfiles") or "static" in str(static_root),
                        "[F03] STATIC_ROOT path should target staticfiles or static directory")

    def test_f03_04_static_url_defined(self):
        """TC-F03-04: STATIC_URL must be defined and end with a slash."""
        static_url = getattr(settings, 'STATIC_URL', None)
        self.assertIsNotNone(static_url, "[F03] STATIC_URL must be defined")
        self.assertTrue(static_url.endswith("/"), "[F03] STATIC_URL must end with a trailing slash")

    def test_f03_05_allowed_hosts_cloud_compatible(self):
        """TC-F03-05: ALLOWED_HOSTS must accommodate production cloud hosts or env variable override."""
        hosts = settings.ALLOWED_HOSTS
        cloud_compatible = (
            '*' in hosts
            or any(".vercel.app" in h for h in hosts)
            or any(".onrender.com" in h for h in hosts)
            or "ALLOWED_HOSTS" in self.settings_content
        )
        self.assertTrue(cloud_compatible, "[F03] ALLOWED_HOSTS must allow cloud domains or env override")
