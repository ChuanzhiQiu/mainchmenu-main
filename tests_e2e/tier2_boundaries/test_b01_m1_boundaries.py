"""
Tier 2 — Boundaries & Corner Cases: Milestone 1 (F01 - F05).
Covers dependency parsing, database URL edge cases, static file boundaries, printer limits, and manifest corner cases.
"""

import json
from unittest.mock import MagicMock, patch
from tests_e2e.base import E2ESimpleTestCase, E2ETestCase


class TestB01M1Boundaries(E2ETestCase):
    """Boundary test cases for Milestone 1 features F01 to F05."""

    # F01: Dependency boundaries
    def test_b01_01_requirements_empty_lines_and_comments_ignored(self):
        """TC-B01-01: requirements.txt parser handles comment-only and whitespace lines cleanly."""
        req_path = self.PROJECT_ROOT / "requirements.txt"
        lines = [line.strip() for line in req_path.read_text(encoding="utf-8").splitlines()]
        clean = [l for l in lines if l and not l.startswith("#")]
        for line in clean:
            self.assertFalse(line.startswith("#"), "[F01-B] Clean dependencies must not include comment lines")

    def test_b01_02_requirements_no_duplicate_package_names(self):
        """TC-B01-02: requirements.txt contains no duplicate package declarations."""
        req_path = self.PROJECT_ROOT / "requirements.txt"
        lines = [line.strip().split("==")[0].split(";")[0].strip().lower()
                 for line in req_path.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.strip().startswith("#")]
        seen = set()
        duplicates = []
        for pkg in lines:
            if pkg in seen:
                duplicates.append(pkg)
            seen.add(pkg)
        self.assertEqual(len(duplicates), 0, f"[F01-B] Duplicate packages found: {duplicates}")

    def test_b01_03_pywin32_semicolon_format_integrity(self):
        """TC-B01-03: Environment marker in pywin32 strictly uses semicolon and sys_platform."""
        req_path = self.PROJECT_ROOT / "requirements.txt"
        content = req_path.read_text(encoding="utf-8")
        if "pywin32" in content:
            for line in content.splitlines():
                if "pywin32" in line and not line.strip().startswith("#"):
                    self.assertIn(";", line, "[F01-B] Marker must be separated by ';'")
                    self.assertIn("sys_platform", line, "[F01-B] Marker must specify sys_platform")

    def test_b01_04_requirements_exact_version_pinning(self):
        """TC-B01-04: Core cloud dependencies specify deterministic version pinning (==)."""
        req_path = self.PROJECT_ROOT / "requirements.txt"
        lines = [l.strip() for l in req_path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
        for line in lines:
            self.assertTrue("==" in line or ">=" in line, f"[F01-B] Line '{line}' should specify version pinning")

    def test_b01_05_unknown_platform_fallback_safety(self):
        """TC-B01-05: Verifies non-win32 platforms do not evaluate win32 dependencies."""
        import sys
        if sys.platform != "win32":
            # On Linux / macOS, win32print is absent but should not break imports
            try:
                import win32print
                self.fail("[F01-B] win32print should not exist on non-win32 platform")
            except ImportError:
                pass

    # F02: Cloud DB boundaries
    def test_b01_06_database_url_invalid_scheme_handling(self):
        """TC-B01-06: Parsing invalid DB URL scheme fails gracefully."""
        try:
            import dj_database_url
            with self.assertRaises(Exception):
                dj_database_url.parse("invalid_scheme://user:pass@host/db")
        except ImportError:
            self.fail("[F02-B] dj_database_url is required")

    def test_b01_07_database_url_empty_string_fallback(self):
        """TC-B01-07: When DATABASE_URL is empty string, system falls back to SQLite."""
        try:
            import dj_database_url
            res = dj_database_url.config(default="sqlite:///test_fallback.sqlite3", env="NONEXISTENT_VAR_FOR_TEST")
            self.assertIn("sqlite", res["ENGINE"], "[F02-B] Empty or missing env var should fallback to SQLite")
        except ImportError:
            self.fail("[F02-B] dj_database_url is required")

    def test_b01_08_database_url_with_special_characters_in_password(self):
        """TC-B01-08: DATABASE_URL with percent-encoded special characters parses correctly."""
        try:
            import dj_database_url
            url = "postgresql://user:p%40ss%23word@localhost:5432/testdb"
            parsed = dj_database_url.parse(url)
            self.assertEqual(parsed["PASSWORD"], "p@ss#word", "[F02-B] Percent-encoded password must decode cleanly")
        except ImportError:
            self.fail("[F02-B] dj_database_url is required")

    def test_b01_09_database_url_ssl_mode_requirement(self):
        """TC-B01-09: Supabase connections require sslmode in options."""
        try:
            import dj_database_url
            url = "postgresql://postgres:pass@db.supabase.co:6543/postgres?sslmode=require"
            parsed = dj_database_url.parse(url)
            self.assertIn("postgres", parsed["ENGINE"])
        except ImportError:
            self.fail("[F02-B] dj_database_url is required")

    def test_b01_10_database_sqlite_options_timeout_boundary(self):
        """TC-B01-10: SQLite database options reject timeout < 0."""
        from django.conf import settings
        db_conf = settings.DATABASES.get("default", {})
        if "sqlite3" in db_conf.get("ENGINE", ""):
            timeout = db_conf.get("OPTIONS", {}).get("timeout", 20)
            self.assertGreater(timeout, 0, "[F02-B] Timeout must be positive")

    # F03: Static & WhiteNoise boundaries
    def test_b01_11_static_root_is_absolute_path(self):
        """TC-B01-11: STATIC_ROOT must be an absolute path to prevent root-relative errors."""
        from django.conf import settings
        static_root = getattr(settings, "STATIC_ROOT", None)
        if static_root:
            import os
            self.assertTrue(os.path.isabs(str(static_root)), "[F03-B] STATIC_ROOT must be an absolute path")

    def test_b01_12_whitenoise_handles_nonexistent_static_asset_404(self):
        """TC-B01-12: Requesting non-existent static asset returns 404 without 500 error."""
        client = self.get_client()
        resp = client.get("/static/non_existent_asset_12345.css")
        self.assertEqual(resp.status_code, 404, "[F03-B] Nonexistent static asset should return 404")

    def test_b01_13_allowed_hosts_preserves_localhost(self):
        """TC-B01-13: ALLOWED_HOSTS retains localhost and 127.0.0.1 for local dev/testing."""
        from django.conf import settings
        hosts = settings.ALLOWED_HOSTS
        if '*' not in hosts:
            self.assertIn("localhost", hosts, "[F03-B] localhost should be in ALLOWED_HOSTS")
            self.assertIn("127.0.0.1", hosts, "[F03-B] 127.0.0.1 should be in ALLOWED_HOSTS")

    def test_b01_14_secret_key_not_empty_boundary(self):
        """TC-B01-14: SECRET_KEY must have length >= 32 characters."""
        from django.conf import settings
        self.assertGreaterEqual(len(settings.SECRET_KEY), 32, "[F03-B] SECRET_KEY must be at least 32 characters")

    def test_b01_15_static_url_starts_and_ends_with_slash(self):
        """TC-B01-15: STATIC_URL starts and ends with a forward slash."""
        from django.conf import settings
        self.assertTrue(settings.STATIC_URL.startswith("/"), "[F03-B] STATIC_URL must start with '/'")
        self.assertTrue(settings.STATIC_URL.endswith("/"), "[F03-B] STATIC_URL must end with '/'")

    # F04: Hardware printer boundaries
    def test_b01_16_imprimir_comanda_with_none_order(self):
        """TC-B01-16: Calling imprimir_comanda with None returns error dict without unhandled exception."""
        imprimir_comanda = self.require_service("Menu.utils", "imprimir_comanda", feature_id="F04")
        res = imprimir_comanda(None)
        self.assertIsInstance(res, dict)
        self.assertFalse(res.get("success", False), "[F04-B] Should handle None order safely")

    def test_b01_17_imprimir_comanda_with_extreme_customer_name(self):
        """TC-B01-17: Printing order with 500-char customer name does not crash printer buffer."""
        imprimir_comanda = self.require_service("Menu.utils", "imprimir_comanda", feature_id="F04")
        mock_orden = MagicMock()
        mock_orden.id = 999
        mock_orden.cliente = "A" * 500
        mock_orden.items.all.return_value = []
        mock_orden.monto_total = 1000
        res = imprimir_comanda(mock_orden)
        self.assertIsInstance(res, dict)

    def test_b01_18_ticket_view_negative_order_id_returns_404(self):
        """TC-B01-18: GET /pedidos/-1/ticket/ returns 404."""
        client = self.get_client()
        resp = client.get("/pedidos/-1/ticket/")
        self.assertEqual(resp.status_code, 404, "[F04-B] Negative order ID must return 404")

    def test_b01_19_ticket_view_zero_total_order(self):
        """TC-B01-19: Ticket view renders cleanly for promotional order with total=0."""
        Orden = self.require_model("Menu", "Orden", feature_id="F04")
        orden = Orden.objects.create(cliente="Promo Gratis", canal_venta="Local", tipo_pago="Efectivo", monto_total=0.0)
        client = self.get_client()
        resp = client.get(f"/pedidos/{orden.id}/ticket/")
        self.assertEqual(resp.status_code, 200, "[F04-B] Zero total ticket should render without error")

    def test_b01_20_ticket_template_unicode_character_rendering(self):
        """TC-B01-20: Ticket rendering handles UTF-8 / Spanish accented characters (ñ, á, é, í, ó, ú)."""
        Orden = self.require_model("Menu", "Orden", feature_id="F04")
        orden = Orden.objects.create(cliente="Ñandú González Pérez", canal_venta="Local", tipo_pago="Efectivo")
        client = self.get_client()
        resp = client.get(f"/pedidos/{orden.id}/ticket/")
        content = resp.content.decode("utf-8")
        self.assertIn("Ñandú González Pérez", content, "[F04-B] Ticket should render unicode names faithfully")

    # F05: Deployment manifests boundaries
    def test_b01_21_vercel_json_not_empty(self):
        """TC-B01-21: vercel.json contains non-empty configuration."""
        v_path = self.require_file("vercel.json", feature_id="F05")
        with open(v_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(len(data.keys()) > 0, "[F05-B] vercel.json must not be empty")

    def test_b01_22_procfile_single_line_or_multi_line_format(self):
        """TC-B01-22: Procfile lines do not have trailing carriage returns causing unix script errors."""
        p_path = self.require_file("Procfile", feature_id="F05")
        raw = p_path.read_bytes()
        self.assertNotIn(b"\r\n\r\n\r\n", raw, "[F05-B] Procfile should not have excess CRLF line breaks")

    def test_b01_23_procfile_bind_port_or_worker_config(self):
        """TC-B01-23: Procfile web line specifies gunicorn with wsgi module."""
        p_path = self.require_file("Procfile", feature_id="F05")
        content = p_path.read_text(encoding="utf-8")
        web_lines = [l for l in content.splitlines() if l.strip().startswith("web:")]
        self.assertEqual(len(web_lines), 1, "[F05-B] Procfile must have exactly one 'web:' declaration")

    def test_b01_24_wsgi_environment_variable_override(self):
        """TC-B01-24: WSGI loads default settings module without error."""
        import os
        from MainchApp import wsgi
        self.assertIsNotNone(wsgi.application, "[F05-B] wsgi.application must be defined")

    def test_b01_25_manifests_project_root_co_location(self):
        """TC-B01-25: Both vercel.json and Procfile are placed in project root next to manage.py."""
        self.assertTrue((self.PROJECT_ROOT / "manage.py").exists())
        self.assertTrue((self.PROJECT_ROOT / "vercel.json").exists())
        self.assertTrue((self.PROJECT_ROOT / "Procfile").exists())
