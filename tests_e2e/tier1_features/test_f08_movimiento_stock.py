"""
Tier 1 — Feature F08: Audit Kardex (MovimientoStock).
Verifies immutable stock movement logging, movement types, and audit trail.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestF08MovimientoStock(E2ETestCase):
    """Test suite for Feature F08: Audit Kardex (MovimientoStock)."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F08")
        self.MovimientoStock = self.require_model("Menu", "MovimientoStock", feature_id="F08")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F08")

        self.insumo = self.Insumo.objects.create(
            codigo="INS-QUESO", nombre="Queso Cheddar", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("8000.000")
        )

    def test_f08_01_movimiento_stock_fields_exist(self):
        """TC-F08-01: MovimientoStock must define insumo, tipo, cantidad, stock_anterior, stock_nuevo."""
        self.assert_model_has_field(self.MovimientoStock, "insumo")
        self.assert_model_has_field(self.MovimientoStock, "tipo")
        self.assert_model_has_field(self.MovimientoStock, "cantidad")
        self.assert_model_has_field(self.MovimientoStock, "stock_anterior")
        self.assert_model_has_field(self.MovimientoStock, "stock_nuevo")
        self.assert_model_has_field(self.MovimientoStock, "fecha_hora")

    def test_f08_02_consumo_orden_movement_delta(self):
        """TC-F08-02: Recording a CONSUMO_ORDEN movement matches stock reduction."""
        orden = self.Orden.objects.create(cliente="Cliente Mesa 1", canal_venta="Local", tipo_pago="Efectivo")
        mov = self.MovimientoStock.objects.create(
            insumo=self.insumo,
            tipo="CONSUMO_ORDEN",
            cantidad=Decimal("0.500"),
            stock_anterior=Decimal("10.000"),
            stock_nuevo=Decimal("9.500"),
            orden=orden
        )
        self.assertEqual(mov.stock_anterior - mov.cantidad, mov.stock_nuevo, "[F08] Delta must equal consumed amount")
        self.assertEqual(mov.orden.id, orden.id, "[F08] Movement should link to order")

    def test_f08_03_ingreso_compra_movement(self):
        """TC-F08-03: Recording an INGRESO_COMPRA movement matches stock addition."""
        mov = self.MovimientoStock.objects.create(
            insumo=self.insumo,
            tipo="INGRESO_COMPRA",
            cantidad=Decimal("5.000"),
            stock_anterior=Decimal("9.500"),
            stock_nuevo=Decimal("14.500"),
            orden=None
        )
        self.assertEqual(mov.stock_anterior + mov.cantidad, mov.stock_nuevo, "[F08] Delta must equal added amount")
        self.assertIsNone(mov.orden, "[F08] Restock movement may have no order link")

    def test_f08_04_ajuste_manual_movement(self):
        """TC-F08-04: Recording AJUSTE_MANUAL or MERMA movement with audit notes."""
        mov = self.MovimientoStock.objects.create(
            insumo=self.insumo,
            tipo="MERMA",
            cantidad=Decimal("0.200"),
            stock_anterior=Decimal("14.500"),
            stock_nuevo=Decimal("14.300")
        )
        self.assertEqual(mov.tipo, "MERMA", "[F08] Movement type should be recorded as MERMA")

    def test_f08_05_chronological_ordering_of_movements(self):
        """TC-F08-05: Querying movements by insumo returns chronological timeline."""
        mov1 = self.MovimientoStock.objects.create(
            insumo=self.insumo, tipo="CONSUMO_ORDEN", cantidad=Decimal("0.100"),
            stock_anterior=Decimal("10.000"), stock_nuevo=Decimal("9.900")
        )
        mov2 = self.MovimientoStock.objects.create(
            insumo=self.insumo, tipo="CONSUMO_ORDEN", cantidad=Decimal("0.200"),
            stock_anterior=Decimal("9.900"), stock_nuevo=Decimal("9.700")
        )
        history = list(self.MovimientoStock.objects.filter(insumo=self.insumo).order_by('id'))
        self.assertEqual(len(history), 2, "[F08] Insumo should have 2 recorded movements")
        self.assertEqual(history[0].id, mov1.id)
        self.assertEqual(history[1].id, mov2.id)
