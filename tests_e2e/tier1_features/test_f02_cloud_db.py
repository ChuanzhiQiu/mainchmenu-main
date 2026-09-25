"""
Tier 1 — Feature F02: Cloud Database Configuration.
Verifies Supabase PostgreSQL integration via dj-database-url with transparent SQLite fallback.
"""

import os
from unittest.mock import patch
from django.conf import settings
from tests_e2e.base import E2ESimpleTestCase


class TestF02CloudDB(E2ESimpleTestCase):
    """Test suite for Feature F02: Cloud Database Configuration."""

    def setUp(self):
        self.settings_path = self.PROJECT_ROOT / "MainchApp" / "settings.py"
        self.settings_content = self.settings_path.read_text(encoding="utf-8")

    def test_f02_01_sqlite_fallback_active_by_default(self):
        """TC-F02-01: In local development without DATABASE_URL, SQLite is the default database engine."""
        db_config = settings.DATABASES.get('default', {})
        self.assertIn('sqlite3', db_config.get('ENGINE', ''), "[F02] Default fallback engine must be sqlite3")

    def test_f02_02_database_url_env_checked_in_settings(self):
        """TC-F02-02: settings.py must reference DATABASE_URL from environment."""
        self.assertIn("DATABASE_URL", self.settings_content, "[F02] settings.py must reference 'DATABASE_URL'")

    def test_f02_03_dj_database_url_imported_or_utilized(self):
        """TC-F02-03: settings.py must utilize dj_database_url for dynamic connection parsing."""
        self.assertIn("dj_database_url", self.settings_content, "[F02] settings.py must import/utilize 'dj_database_url'")

    def test_f02_04_sqlite_connection_timeout_configured(self):
        """TC-F02-04: SQLite database options must configure timeout >= 20s to prevent lock errors."""
        db_config = settings.DATABASES.get('default', {})
        if 'sqlite3' in db_config.get('ENGINE', ''):
            options = db_config.get('OPTIONS', {})
            timeout = options.get('timeout', 0)
            self.assertGreaterEqual(timeout, 20, "[F02] SQLite connection timeout must be at least 20 seconds")

    def test_f02_05_dynamic_database_url_parsing(self):
        """TC-F02-05: When DATABASE_URL is provided, dj_database_url parses postgres configuration."""
        try:
            import dj_database_url
        except ImportError:
            self.fail("[F02] dj_database_url module is not installed in the python environment.")

        sample_url = "postgresql://testuser:testpass@supabase.example.com:5432/testdb"
        parsed = dj_database_url.parse(sample_url)
        self.assertEqual(parsed['ENGINE'], 'django.db.backends.postgresql', "[F02] Parsed engine should be postgresql")
        self.assertEqual(parsed['NAME'], 'testdb', "[F02] Parsed db name mismatch")
        self.assertEqual(parsed['USER'], 'testuser', "[F02] Parsed db user mismatch")
        self.assertEqual(parsed['HOST'], 'supabase.example.com', "[F02] Parsed db host mismatch")
        self.assertEqual(parsed['PORT'], 5432, "[F02] Parsed db port mismatch")
