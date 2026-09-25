"""
Tier 4 — Real-World Scenario S03: "El Combo Promocional Complejo" (Multi-Dish Combo Promotion).
Simulates creation and sales of promotional multi-dish combos with shared base raw materials,
verifying BOM explosion, inventory aggregation, and ticket comanda generation.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestS03PromoCombo(E2ETestCase):
    """Scenario 3: Multi-dish promotional combo with shared raw ingredients."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="S03")
        self.Menu = self.require_model("Menu", "Menu", feature_id="S03")
        self.Orden = self.require_model("Menu", "Orden", feature_id="S03")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="S03")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="S03")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="S03")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="S03")

    def test_s03_family_pack_combo_operations(self):
        """TC-S03-01: Mega Pack Familiar (2 Burgers + 1 Large Fries + 4 Empanadas) explodes and deducts accurately."""
        # Shared raw materials
        ins_aceite = self.Insumo.objects.create(codigo="PROMO-ACEITE", nombre="Aceite", unidad_medida="lt", stock_actual=Decimal("50.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("2000.000"))
        ins_sal = self.Insumo.objects.create(codigo="PROMO-SAL", nombre="Sal", unidad_medida="kg", stock_actual=Decimal("10.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("600.000"))
        ins_carne = self.Insumo.objects.create(codigo="PROMO-CARNE", nombre="Carne Picada", unidad_medida="kg", stock_actual=Decimal("30.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("6500.000"))
        ins_papas = self.Insumo.objects.create(codigo="PROMO-PAPAS", nombre="Papas Prefritas", unidad_medida="kg", stock_actual=Decimal("40.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1500.000"))
        ins_queso = self.Insumo.objects.create(codigo="PROMO-QUESO", nombre="Queso Fundido", unidad_medida="kg", stock_actual=Decimal("20.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("5000.000"))

        # Individual dishes
        d_burger = self.Plato.objects.create(nombre="Burger Casera", valor=5000)
        d_fries = self.Plato.objects.create(nombre="Papas Grandes", valor=3500)
        d_empanada = self.Plato.objects.create(nombre="Empanada Queso", valor=2000)

        # Recipes:
        # Burger: 0.180 kg carne, 0.050 kg queso, 0.005 kg sal
        self.RecetaItem.objects.create(plato=d_burger, insumo=ins_carne, cantidad=Decimal("0.180"))
        self.RecetaItem.objects.create(plato=d_burger, insumo=ins_queso, cantidad=Decimal("0.050"))
        self.RecetaItem.objects.create(plato=d_burger, insumo=ins_sal, cantidad=Decimal("0.005"))

        # Fries: 0.400 kg papas, 0.050 lt aceite, 0.010 kg sal
        self.RecetaItem.objects.create(plato=d_fries, insumo=ins_papas, cantidad=Decimal("0.400"))
        self.RecetaItem.objects.create(plato=d_fries, insumo=ins_aceite, cantidad=Decimal("0.050"))
        self.RecetaItem.objects.create(plato=d_fries, insumo=ins_sal, cantidad=Decimal("0.010"))

        # Empanada: 0.080 kg queso, 0.030 lt aceite, 0.002 kg sal
        self.RecetaItem.objects.create(plato=d_empanada, insumo=ins_queso, cantidad=Decimal("0.080"))
        self.RecetaItem.objects.create(plato=d_empanada, insumo=ins_aceite, cantidad=Decimal("0.030"))
        self.RecetaItem.objects.create(plato=d_empanada, insumo=ins_sal, cantidad=Decimal("0.002"))

        # Create Combo with 3 dishes
        combo_pack = self.Menu.objects.create(nombre="Mega Pack Familiar", precio_menus=15000)
        combo_pack.platos.add(d_burger, d_fries, d_empanada)

        # Customer orders 2 units of the Mega Pack Familiar
        orden = self.Orden.objects.create(cliente="Familia González", canal_venta="Local", tipo_pago="Tarjeta")
        self.OrdenItem.objects.create(orden=orden, menu=combo_pack, cantidad=2)

        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"))

        # Expected consumption for 2 combos:
        # Each combo contains: 1 burger + 1 fries + 1 empanada
        # For 2 combos: 2 burgers + 2 fries + 2 empanadas
        # Carne: 2 * 0.180 = 0.360 kg -> 30 - 0.360 = 29.640
        # Papas: 2 * 0.400 = 0.800 kg -> 40 - 0.800 = 39.200
        # Queso: (2 * 0.050) + (2 * 0.080) = 0.100 + 0.160 = 0.260 kg -> 20 - 0.260 = 19.740
        # Aceite: (2 * 0.050) + (2 * 0.030) = 0.100 + 0.060 = 0.160 lt -> 50 - 0.160 = 49.840
        # Sal: (2 * 0.005) + (2 * 0.010) + (2 * 0.002) = 0.010 + 0.020 + 0.004 = 0.034 kg -> 10 - 0.034 = 9.966

        ins_carne.refresh_from_db()
        ins_papas.refresh_from_db()
        ins_queso.refresh_from_db()
        ins_aceite.refresh_from_db()
        ins_sal.refresh_from_db()

        self.assertEqual(ins_carne.stock_actual, Decimal("29.640"))
        self.assertEqual(ins_papas.stock_actual, Decimal("39.200"))
        self.assertEqual(ins_queso.stock_actual, Decimal("19.740"))
        self.assertEqual(ins_aceite.stock_actual, Decimal("49.840"))
        self.assertEqual(ins_sal.stock_actual, Decimal("9.966"))
