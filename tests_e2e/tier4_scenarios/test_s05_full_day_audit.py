"""
Tier 4 — Real-World Scenario S05: "Ciclo Completo de Auditoría Operativa y Cierre de Turno" (End-of-Day Audit & Turnover).
Simulates an entire restaurant operating day: opening stock, multiple orders, kitchen dispatch,
ticket generation, spoilage/waste (MERMA), night shift audit reconciliation, and next-day AI replenishment order.
"""

import json
from decimal import Decimal
from django.utils import timezone
from tests_e2e.base import E2ETestCase


class TestS05FullDayAudit(E2ETestCase):
    """Scenario 5: Complete operational day cycle, waste, Kardex audit, and automated night replenishment."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="S05")
        self.Orden = self.require_model("Menu", "Orden", feature_id="S05")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="S05")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="S05")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="S05")
        self.MovimientoStock = self.require_model("Menu", "MovimientoStock", feature_id="S05")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="S05")
        self.generar_sugerencias = self.require_service("src.ai_forecast.forecaster", "generar_sugerencias_compra", feature_id="S05")

    def test_s05_full_operating_day_cycle(self):
        """TC-S05-01: Morning opening -> POS orders -> Kitchen KDS -> Spoilage loss -> Night Kardex reconciliation -> AI PO."""
        # 1. MORNING (09:00): Opening physical inventory count
        ins_leche = self.Insumo.objects.create(
            codigo="DAY-LECHE", nombre="Leche Entera", unidad_medida="lt",
            stock_actual=Decimal("20.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("950.000")
        )
        p_cappuccino = self.Plato.objects.create(nombre="Cappuccino Grande", valor=2800)
        self.RecetaItem.objects.create(plato=p_cappuccino, insumo=ins_leche, cantidad=Decimal("0.250"))

        # 2. DAY SHIFT (10:00 - 18:00): 4 Orders served throughout the day
        client = self.get_client()
        for i in range(4):
            orden = self.Orden.objects.create(cliente=f"Cliente Cafe #{i+1}", canal_venta="Local", tipo_pago="Efectivo")
            self.OrdenItem.objects.create(orden=orden, plato=p_cappuccino, cantidad=2)  # 2 coffees each = 0.500 lt per order (total 2.000 lt)
            # Confirm order in kitchen
            resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
            self.assertEqual(resp.status_code, 200)

        # 3. AFTERNOON (18:30): Spoilage / Merma occurs (e.g. 1 liter spilled / spoiled)
        ins_leche.refresh_from_db()
        stock_before_waste = ins_leche.stock_actual  # 20.000 - 2.000 = 18.000 lt
        waste_qty = Decimal("1.000")
        stock_after_waste = stock_before_waste - waste_qty

        self.MovimientoStock.objects.create(
            insumo=ins_leche,
            tipo="MERMA",
            cantidad=waste_qty,
            stock_anterior=stock_before_waste,
            stock_nuevo=stock_after_waste
        )
        ins_leche.stock_actual = stock_after_waste
        ins_leche.save()

        # 4. NIGHT CLOSING (22:00): Inventory audit reconciliation
        ins_leche.refresh_from_db()
        # Initial (20.000) - Orders (2.000) - Merma (1.000) = 17.000 lt
        self.assertEqual(ins_leche.stock_actual, Decimal("17.000"), "[S05] Night audit stock mismatch")

        # Reconcile all Kardex movements for the day
        movements = self.MovimientoStock.objects.filter(insumo=ins_leche)
        total_consumed = sum(m.cantidad for m in movements if m.tipo == "CONSUMO_ORDEN")
        total_waste = sum(m.cantidad for m in movements if m.tipo == "MERMA")

        self.assertEqual(total_consumed, Decimal("2.000"))
        self.assertEqual(total_waste, Decimal("1.000"))
        self.assertEqual(Decimal("20.000") - (total_consumed + total_waste), ins_leche.stock_actual)

        # 5. NIGHT CLOSING: Run AI Demand Forecaster to prepare tomorrow's supplier purchase order
        po_result = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(po_result, "[S05] Forecaster must generate end-of-day purchase order")
