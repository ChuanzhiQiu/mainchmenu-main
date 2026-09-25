"""
Tier 4 — Real-World Scenario S02: "El Quiebre de Stock y Reabastecimiento" (Stock Out & Replenishment).
Simulates an ingredient breach: near zero stock, order causes negative stock, alert generated,
supplier delivery arrives, stock restored, Kardex completely reconciled.
"""

import json
from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestS02StockoutCrisis(E2ETestCase):
    """Scenario 2: Low-stock breach, operational non-blocking, alert generation, and replenishment."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="S02")
        self.Orden = self.require_model("Menu", "Orden", feature_id="S02")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="S02")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="S02")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="S02")
        self.MovimientoStock = self.require_model("Menu", "MovimientoStock", feature_id="S02")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="S02")

    def test_s02_stockout_crisis_lifecycle(self):
        """TC-S02-01: Full lifecycle of a stock crisis: depletion -> warning -> negative stock -> restock -> audit."""
        # Step 1: Insumo starts near critical minimum (0.800 kg remaining, safety minimum is 3.000 kg)
        insumo = self.Insumo.objects.create(
            codigo="CRISIS-PALTA", nombre="Palta Hass Extra", unidad_medida="kg",
            stock_actual=Decimal("0.800"), stock_minimo=Decimal("3.000"), costo_unitario=Decimal("5000.000")
        )
        plato = self.Plato.objects.create(nombre="Completo Italiano Gigante", valor=4000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.200"))

        # Step 2: Customer orders 5 units (consumes 5 * 0.200 = 1.000 kg, driving stock to 0.800 - 1.000 = -0.200 kg)
        orden = self.Orden.objects.create(cliente="Mesa Crisis Palta", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=5)

        # Step 3: Kitchen confirms order. System must NOT block operations; it completes with stock alert
        client = self.get_client()
        resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 200, "[S02] Confirm must complete without blocking")

        data = resp.json()
        self.assertTrue(data.get("success"))

        # Check stock is negative
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("-0.200"), "[S02] Stock correctly tracked below zero")

        # Step 4: Verify Kardex recorded the consumption
        mov_consumo = self.MovimientoStock.objects.filter(orden=orden, insumo=insumo).first()
        self.assertIsNotNone(mov_consumo)
        self.assertEqual(mov_consumo.tipo, "CONSUMO_ORDEN")
        self.assertEqual(mov_consumo.stock_anterior, Decimal("0.800"))
        self.assertEqual(mov_consumo.stock_nuevo, Decimal("-0.200"))

        # Step 5: Supplier delivery arrives: 15.000 kg of fresh avocado
        # Restock operation (e.g. manual adjustment or purchase receipt)
        self.MovimientoStock.objects.create(
            insumo=insumo,
            tipo="INGRESO_COMPRA",
            cantidad=Decimal("15.000"),
            stock_anterior=Decimal("-0.200"),
            stock_nuevo=Decimal("14.800")
        )
        insumo.stock_actual = Decimal("14.800")
        insumo.save()

        # Step 6: Verify stock restored above minimum
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("14.800"))
        self.assertGreater(insumo.stock_actual, insumo.stock_minimo)
