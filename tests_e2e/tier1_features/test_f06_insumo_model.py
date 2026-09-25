"""
Tier 1 — Feature F06: Raw Material Model (Insumo).
Verifies Insumo model fields, decimal precision, units, and active status.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestF06InsumoModel(E2ETestCase):
    """Test suite for Feature F06: Raw Material Model (Insumo)."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F06")

    def test_f06_01_insumo_fields_exist(self):
        """TC-F06-01: Insumo model must define code, name, unit, stock, min stock, unit cost, active."""
        self.assert_model_has_field(self.Insumo, "codigo")
        self.assert_model_has_field(self.Insumo, "nombre")
        self.assert_model_has_field(self.Insumo, "unidad_medida")
        self.assert_model_has_field(self.Insumo, "stock_actual")
        self.assert_model_has_field(self.Insumo, "stock_minimo")
        self.assert_model_has_field(self.Insumo, "costo_unitario")
        self.assert_model_has_field(self.Insumo, "activo")

    def test_f06_02_insumo_creation_and_str(self):
        """TC-F06-02: Creating an Insumo record and checking string representation."""
        insumo = self.Insumo.objects.create(
            codigo="INS-001",
            nombre="Carne Molida",
            unidad_medida="kg",
            stock_actual=Decimal("15.500"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("6500.000"),
            activo=True
        )
        self.assertIn("Carne Molida", str(insumo), "[F06] str(insumo) should include insumo name")

    def test_f06_03_decimal_precision_preservation(self):
        """TC-F06-03: Insumo stock fields must preserve 3 decimal places without binary float drift."""
        insumo = self.Insumo.objects.create(
            codigo="INS-002",
            nombre="Sal Fina",
            unidad_medida="kg",
            stock_actual=Decimal("0.125"),
            stock_minimo=Decimal("0.050"),
            costo_unitario=Decimal("800.000")
        )
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("0.125"), "[F06] Stock value must preserve 3 decimal places")

    def test_f06_04_default_active_is_true(self):
        """TC-F06-04: Insumo activo field should default to True."""
        insumo = self.Insumo.objects.create(
            codigo="INS-003",
            nombre="Aceite Maravilla",
            unidad_medida="lt",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("2.000"),
            costo_unitario=Decimal("2100.000")
        )
        self.assertTrue(insumo.activo, "[F06] Insumo should be active by default")

    def test_f06_05_insumo_query_filtering(self):
        """TC-F06-05: Filtering active insumos and low-stock detection."""
        self.Insumo.objects.create(
            codigo="INS-004",
            nombre="Papas Congeladas",
            unidad_medida="kg",
            stock_actual=Decimal("2.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("1800.000"),
            activo=True
        )
        low_stock = self.Insumo.objects.filter(stock_actual__lt=Decimal("5.000"), activo=True)
        self.assertTrue(low_stock.filter(codigo="INS-004").exists(), "[F06] Low-stock insumo should be queried correctly")
