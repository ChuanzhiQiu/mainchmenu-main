"""
Tier 1 — Feature F10: Atomic Stock Deduction Service.
Verifies Menu.services.inventory_service.descontar_stock_orden for dishes, combos, kardex, and idempotency.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestF10StockDeductionService(E2ETestCase):
    """Test suite for Feature F10: Atomic Stock Deduction Service."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F10")
        self.Menu = self.require_model("Menu", "Menu", feature_id="F10")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F10")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F10")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F10")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F10")
        self.MovimientoStock = self.require_model("Menu", "MovimientoStock", feature_id="F10")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="F10")

    def test_f10_01_service_callable(self):
        """TC-F10-01: descontar_stock_orden function is imported and callable."""
        self.assertTrue(callable(self.descontar_stock_orden), "[F10] descontar_stock_orden must be callable")

    def test_f10_02_single_dish_order_stock_deduction(self):
        """TC-F10-02: Completing an order with 2 units of a dish deducts exactly 2x recipe amount."""
        insumo = self.Insumo.objects.create(
            codigo="INS-CHURR", nombre="Churrasco Posta", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("7000.000")
        )
        plato = self.Plato.objects.create(nombre="Sandwich Churrasco", valor=6000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.200"))

        orden = self.Orden.objects.create(cliente="Mesa 1", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=2)

        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"), "[F10] Deduction must succeed")

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("9.600"), "[F10] Stock should be 10.000 - (2 * 0.200) = 9.600")

        # Verify Kardex log
        mov = self.MovimientoStock.objects.filter(orden=orden, insumo=insumo).first()
        self.assertIsNotNone(mov, "[F10] Kardex movement must be created")
        self.assertEqual(mov.cantidad, Decimal("0.400"))
        self.assertEqual(mov.tipo, "CONSUMO_ORDEN")

    def test_f10_03_combo_explosion_deduction(self):
        """TC-F10-03: An order item containing a combo (Menu) explodes into its constituent dishes."""
        insumo_pan = self.Insumo.objects.create(
            codigo="INS-PAN2", nombre="Pan Frica", unidad_medida="un",
            stock_actual=Decimal("20.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("300.000")
        )
        insumo_papa = self.Insumo.objects.create(
            codigo="INS-PAPA2", nombre="Papas", unidad_medida="kg",
            stock_actual=Decimal("15.000"), stock_minimo=Decimal("3.000"), costo_unitario=Decimal("1000.000")
        )

        plato1 = self.Plato.objects.create(nombre="Burger", valor=5000)
        plato2 = self.Plato.objects.create(nombre="Papas", valor=2000)
        self.RecetaItem.objects.create(plato=plato1, insumo=insumo_pan, cantidad=Decimal("1.000"))
        self.RecetaItem.objects.create(plato=plato2, insumo=insumo_papa, cantidad=Decimal("0.250"))

        combo = self.Menu.objects.create(nombre="Combo Burger + Papas", precio_menus=6500)
        combo.platos.add(plato1, plato2)

        orden = self.Orden.objects.create(cliente="Mesa Combo", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, menu=combo, cantidad=2)

        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"), "[F10] Combo deduction must succeed")

        insumo_pan.refresh_from_db()
        insumo_papa.refresh_from_db()
        self.assertEqual(insumo_pan.stock_actual, Decimal("18.000"), "[F10] 20 - (2 * 1) = 18")
        self.assertEqual(insumo_papa.stock_actual, Decimal("14.500"), "[F10] 15 - (2 * 0.250) = 14.500")

    def test_f10_04_idempotent_deduction_prevents_duplicate_run(self):
        """TC-F10-04: Calling descontar_stock_orden twice does not deduct stock a second time."""
        insumo = self.Insumo.objects.create(
            codigo="INS-QUESO2", nombre="Queso Mantecoso", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("6000.000")
        )
        plato = self.Plato.objects.create(nombre="Empanada Queso", valor=2500)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.100"))

        orden = self.Orden.objects.create(cliente="Mesa Repeat", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=1)

        # First run
        res1 = self.descontar_stock_orden(orden.id)
        self.assertTrue(res1.get("success"))

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("9.900"))

        # Second run
        res2 = self.descontar_stock_orden(orden.id)
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("9.900"), "[F10] Stock must not decrease on re-run")

    def test_f10_05_stock_alert_returned_when_below_minimum(self):
        """TC-F10-05: When an insumo drops below stock_minimo, an alert entry is returned in response."""
        insumo = self.Insumo.objects.create(
            codigo="INS-CRIT", nombre="Insumo Crítico", unidad_medida="kg",
            stock_actual=Decimal("2.050"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        plato = self.Plato.objects.create(nombre="Plato Crítico", valor=3000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.100"))

        orden = self.Orden.objects.create(cliente="Mesa Alerta", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=1)

        res = self.descontar_stock_orden(orden.id)
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("1.950"))
        alertas = res.get("alertas_stock_minimo", [])
        self.assertTrue(len(alertas) > 0, "[F10] Should return stock alert for items below minimum")
