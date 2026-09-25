"""
Tier 1 — Feature F26: Demo Data Seeding Management Command.
Verifies generar_datos_demo command for synthetic dishes, insumos, recipes, and sales history.
"""

from io import StringIO
from django.core.management import call_command
from tests_e2e.base import E2ETestCase


class TestF26DataSeeding(E2ETestCase):
    """Test suite for Feature F26: Demo Data Seeding Script."""

    def setUp(self):
        self.cmd_file = self.PROJECT_ROOT / "Menu" / "management" / "commands" / "generar_datos_demo.py"

    def test_f26_01_command_file_exists(self):
        """TC-F26-01: Management command file Menu/management/commands/generar_datos_demo.py exists."""
        self.assertTrue(self.cmd_file.exists(), "[F26] generar_datos_demo.py must exist")

    def test_f26_02_command_callable_via_django(self):
        """TC-F26-02: Command can be invoked via django.core.management.call_command."""
        out = StringIO()
        try:
            call_command("generar_datos_demo", stdout=out)
        except Exception as e:
            self.fail(f"[F26] call_command('generar_datos_demo') failed: {e}")

    def test_f26_03_seeding_creates_dishes_and_insumos(self):
        """TC-F26-03: Running seed command populates Plato and Insumo models."""
        Plato = self.require_model("Menu", "Plato", feature_id="F26")
        Insumo = self.require_model("Menu", "Insumo", feature_id="F26")

        call_command("generar_datos_demo")
        self.assertGreater(Plato.objects.count(), 0, "[F26] Seeding must create at least one Plato")
        self.assertGreater(Insumo.objects.count(), 0, "[F26] Seeding must create at least one Insumo")

    def test_f26_04_seeding_creates_recipe_links(self):
        """TC-F26-04: Running seed command links dishes to insumos via RecetaItem."""
        RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F26")
        call_command("generar_datos_demo")
        self.assertGreater(RecetaItem.objects.count(), 0, "[F26] Seeding must create recipe items linking dishes to ingredients")

    def test_f26_05_seeding_generates_sales_history(self):
        """TC-F26-05: Running seed command creates historical completed orders for AI forecaster."""
        Orden = self.require_model("Menu", "Orden", feature_id="F26")
        call_command("generar_datos_demo")
        self.assertGreater(Orden.objects.filter(estado="Completada").count(), 0, "[F26] Seeding must create completed orders")
