"""
Tier 3 — Cross-Feature Combination C02: F07 (RecetaItem) + F10 (Stock Deduction) + F13 (POS Combos).
Verifies multi-dish combo explosion into recipe ingredients and inventory consumption.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestC02ComboExplosionDeduction(E2ETestCase):
    """Pairwise combination: Recipe BOM + Atomic Deduction + Combo Catalog."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C02")
        self.Menu = self.require_model("Menu", "Menu", feature_id="C02")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C02")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C02")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="C02")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="C02")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="C02")

    def test_c02_01_triple_dish_combo_deduction(self):
        """TC-C02-01: A combo containing 3 dishes consumes raw ingredients across all 3 recipes."""
        ins_pan = self.Insumo.objects.create(codigo="INS-PAN-C2", nombre="Pan", unidad_medida="un", stock_actual=Decimal("30"), stock_minimo=Decimal("5"), costo_unitario=Decimal("300"))
        ins_carne = self.Insumo.objects.create(codigo="INS-CARNE-C2", nombre="Carne", unidad_medida="kg", stock_actual=Decimal("20"), stock_minimo=Decimal("5"), costo_unitario=Decimal("6000"))
        ins_papas = self.Insumo.objects.create(codigo="INS-PAPAS-C2", nombre="Papas", unidad_medida="kg", stock_actual=Decimal("25"), stock_minimo=Decimal("5"), costo_unitario=Decimal("1200"))
        ins_bebida = self.Insumo.objects.create(codigo="INS-BEB-C2", nombre="Jarabe Bebida", unidad_medida="lt", stock_actual=Decimal("15"), stock_minimo=Decimal("2"), costo_unitario=Decimal("2500"))

        p1 = self.Plato.objects.create(nombre="Hamburguesa", valor=5000)
        p2 = self.Plato.objects.create(nombre="Papas Fritas", valor=2500)
        p3 = self.Plato.objects.create(nombre="Bebida Dispensada", valor=1500)

        self.RecetaItem.objects.create(plato=p1, insumo=ins_pan, cantidad=Decimal("1.000"))
        self.RecetaItem.objects.create(plato=p1, insumo=ins_carne, cantidad=Decimal("0.200"))
        self.RecetaItem.objects.create(plato=p2, insumo=ins_papas, cantidad=Decimal("0.300"))
        self.RecetaItem.objects.create(plato=p3, insumo=ins_bebida, cantidad=Decimal("0.400"))

        combo = self.Menu.objects.create(nombre="Trilogía Burger", precio_menus=8000)
        combo.platos.add(p1, p2, p3)

        # Order 2 units of the combo
        orden = self.Orden.objects.create(cliente="Familia Combo", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, menu=combo, cantidad=2)

        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"))

        ins_pan.refresh_from_db()
        ins_carne.refresh_from_db()
        ins_papas.refresh_from_db()
        ins_bebida.refresh_from_db()

        self.assertEqual(ins_pan.stock_actual, Decimal("28.000"))     # 30 - 2
        self.assertEqual(ins_carne.stock_actual, Decimal("19.600"))   # 20 - 0.400
        self.assertEqual(ins_papas.stock_actual, Decimal("24.400"))   # 25 - 0.600
        self.assertEqual(ins_bebida.stock_actual, Decimal("14.200"))  # 15 - 0.800

    def test_c02_02_combo_and_individual_dish_overlap(self):
        """TC-C02-02: Ordering combo + extra dish correctly adds both ingredient consumptions."""
        ins_queso = self.Insumo.objects.create(codigo="INS-Q-C2", nombre="Queso", unidad_medida="kg", stock_actual=Decimal("10"), stock_minimo=Decimal("2"), costo_unitario=Decimal("5000"))
        p1 = self.Plato.objects.create(nombre="Empanada Queso", valor=2000)
        p2 = self.Plato.objects.create(nombre="Porcion Queso Extra", valor=1000)
        self.RecetaItem.objects.create(plato=p1, insumo=ins_queso, cantidad=Decimal("0.100"))
        self.RecetaItem.objects.create(plato=p2, insumo=ins_queso, cantidad=Decimal("0.050"))

        combo = self.Menu.objects.create(nombre="Duo Queso", precio_menus=2800)
        combo.platos.add(p1)

        orden = self.Orden.objects.create(cliente="Cliente Fan Queso", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, menu=combo, cantidad=1)  # 0.100
        self.OrdenItem.objects.create(orden=orden, plato=p2, cantidad=2)    # 2 * 0.050 = 0.100

        self.descontar_stock_orden(orden.id)
        ins_queso.refresh_from_db()
        self.assertEqual(ins_queso.stock_actual, Decimal("9.800"))
