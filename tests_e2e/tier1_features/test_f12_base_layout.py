"""
Tier 1 — Feature F12: Base Layout Modernization.
Verifies base.html template with Tailwind CSS CDN, Lucide Icons, viewport meta, and toast notifications.
"""

from tests_e2e.base import E2ESimpleTestCase


class TestF12BaseLayout(E2ESimpleTestCase):
    """Test suite for Feature F12: Base Layout Modernization."""

    def setUp(self):
        self.base_tpl = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "base.html"
        self.assertTrue(self.base_tpl.exists(), "[F12] Menu/templates/Menu/base.html must exist")
        self.content = self.base_tpl.read_text(encoding="utf-8")

    def test_f12_01_tailwind_cdn_included(self):
        """TC-F12-01: base.html includes Tailwind CSS script/link."""
        has_tailwind = "tailwindcss" in self.content.lower() or "tailwind" in self.content.lower()
        self.assertTrue(has_tailwind, "[F12] base.html must include Tailwind CSS CDN")

    def test_f12_02_lucide_or_modern_icons_included(self):
        """TC-F12-02: base.html includes Lucide icons or SVG icon library."""
        has_icons = "lucide" in self.content.lower() or "lucide.dev" in self.content.lower() or "unpkg.com/lucide" in self.content.lower()
        self.assertTrue(has_icons, "[F12] base.html must load modern Lucide icons")

    def test_f12_03_viewport_meta_tag_present(self):
        """TC-F12-03: base.html specifies responsive viewport meta tag."""
        self.assertIn("viewport", self.content.lower(), "[F12] base.html must specify viewport meta tag for mobile responsiveness")

    def test_f12_04_toast_or_messages_block_present(self):
        """TC-F12-04: base.html includes Django messages rendering block (toasts/alerts)."""
        has_messages = "messages" in self.content.lower()
        self.assertTrue(has_messages, "[F12] base.html must render Django messages to avoid silent notification loss")

    def test_f12_05_content_block_defined(self):
        """TC-F12-05: base.html defines standard block content for template inheritance."""
        self.assertIn("{% block content %}", self.content, "[F12] base.html must define '{% block content %}' block")
