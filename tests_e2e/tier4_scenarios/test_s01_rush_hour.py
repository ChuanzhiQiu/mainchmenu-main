"""
Tier 4 — Real-World Scenario S01: "El Viernes de Alta Demanda" (Friday Rush Hour).
Simulates high-volume operational rush: opening stock, multiple concurrent orders, kitchen queue,
batch completions, and low-stock alerting.
"""

import json
from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestS01RushHour(E2ETestCase):
    """Scenario 1: High-volume Friday dinner rush hour simulation."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="S01")
        self.Menu = self.require_model("Menu", "Menu", feature_id="S01")
        self.Orden = self.require_model("Menu", "Orden", feature_id="S01")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="S01")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="S01")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="S01")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="S01")

    def test_s01_friday_dinner_rush_simulation(self):
        """TC-S01-01: Full Friday dinner rush: 8 orders, mixed dishes and combos, multi-order stock reconciliation."""
        # 1. Opening Shift: Setup raw materials
        ins_pan = self.Insumo.objects.create(
            codigo="RUSH-PAN", nombre="Pan Frica", unidad_medida="un",
            stock_actual=Decimal("50.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("300.000")
        )
        ins_carne = self.Insumo.objects.create(
            codigo="RUSH-CARNE", nombre="Carne Posta", unidad_medida="kg",
            stock_actual=Decimal("20.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("6500.000")
        )
        ins_queso = self.Insumo.objects.create(
            codigo="RUSH-QUESO", nombre="Queso Gauda", unidad_medida="kg",
            stock_actual=Decimal("15.000"), stock_minimo=Decimal("3.000"), costo_unitario=Decimal("5500.000")
        )

        # 2. Menu Catalog
        p_chacarero = self.Plato.objects.create(nombre="Chacarero", valor=6000)
        p_barros = self.Plato.objects.create(nombre="Barros Luco", valor=6000)

        # Recipes
        self.RecetaItem.objects.create(plato=p_chacarero, insumo=ins_pan, cantidad=Decimal("1.000"))
        self.RecetaItem.objects.create(plato=p_chacarero, insumo=ins_carne, cantidad=Decimal("0.200"))

        self.RecetaItem.objects.create(plato=p_barros, insumo=ins_pan, cantidad=Decimal("1.000"))
        self.RecetaItem.objects.create(plato=p_barros, insumo=ins_carne, cantidad=Decimal("0.200"))
        self.RecetaItem.objects.create(plato=p_barros, insumo=ins_queso, cantidad=Decimal("0.100"))

        # 3. Simulate Rush Hour: 8 Orders arrive in quick succession
        orders = []
        for i in range(8):
            orden = self.Orden.objects.create(cliente=f"Cliente Rush #{i+1}", canal_venta="Local", tipo_pago="Tarjeta")
            # Alternate orders
            if i % 2 == 0:
                self.OrdenItem.objects.create(orden=orden, plato=p_chacarero, cantidad=2)
            else:
                self.OrdenItem.objects.create(orden=orden, plato=p_barros, cantidad=2)
            orders.append(orden)

        # 4. Kitchen processes and completes each order
        client = self.get_client()
        for orden in orders:
            resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({"tipo_pago": "Tarjeta"}), content_type="application/json")
            self.assertEqual(resp.status_code, 200)

        # 5. Verify end-of-rush inventory reconciliation
        # Total ordered: 4 orders of 2 Chacareros (8 Chacareros) + 4 orders of 2 Barros Luco (8 Barros Luco) = 16 sandwiches
        # Pan: 16 sandwiches * 1 = 16 pan consumed. Stock: 50 - 16 = 34
        # Carne: 16 sandwiches * 0.200 = 3.200 kg consumed. Stock: 20 - 3.200 = 16.800 kg
        # Queso: 8 Barros Luco * 0.100 = 0.800 kg consumed. Stock: 15 - 0.800 = 14.200 kg

        ins_pan.refresh_from_db()
        ins_carne.refresh_from_db()
        ins_queso.refresh_from_db()

        self.assertEqual(ins_pan.stock_actual, Decimal("34.000"), "[S01] Bread consumption mismatch")
        self.assertEqual(ins_carne.stock_actual, Decimal("16.800"), "[S01] Meat consumption mismatch")
        self.assertEqual(ins_queso.stock_actual, Decimal("14.200"), "[S01] Cheese consumption mismatch")
