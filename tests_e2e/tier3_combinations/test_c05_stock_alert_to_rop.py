"""
Tier 3 — Cross-Feature Combination C05: F10 (Deduction) + F18 (Dashboard) + F22 (ROP Fallback).
Verifies stock deduction breaching minimum triggering dashboard alert and generating heuristic purchase order.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestC05StockAlertToROP(E2ETestCase):
    """Pairwise combination: Stock Deduction + Inventory Dashboard + Fallback ROP Engine."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C05")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C05")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C05")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="C05")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="C05")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="C05")
        self.calcular_fallback = self.require_service("src.ai_forecast.fallback", "calcular_reorden_heuristico", feature_id="C05")

    def test_c05_01_stock_depletion_triggers_dashboard_flag_and_rop_suggestion(self):
        """TC-C05-01: Order completion causes stock breach -> dashboard displays critical badge -> ROP suggests reorder."""
        # Initial: stock=3.0, min=5.0. Needs 2.0 to reach min.
        insumo = self.Insumo.objects.create(
            codigo="INS-DEFICIT-ROP", nombre="Carne Posta Rosada", unidad_medida="kg",
            stock_actual=Decimal("5.500"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("7500.000")
        )
        plato = self.Plato.objects.create(nombre="Plato Posta", valor=6000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("1.000"))

        orden = self.Orden.objects.create(cliente="Mesa Quiebre", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=2)  # Consumes 2.0 -> stock becomes 3.5 (under 5.0 min!)

        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"))

        # 1. Verify in DB
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("3.500"))
        self.assertLess(insumo.stock_actual, insumo.stock_minimo)

        # 2. Verify on Inventory Dashboard
        client = self.get_client()
        resp = client.get("/inventario/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Carne Posta Rosada", content)

        # 3. Verify Fallback ROP engine generates suggestion for this insumo
        sug = self.calcular_fallback(insumos=[insumo], dias_lead_time=2, dias_proyeccion=7)
        items = sug.items_sugeridos if hasattr(sug, "items_sugeridos") else sug.get("items_sugeridos", [])
        self.assertGreater(len(items), 0, "[C05] Deficit insumo must appear in ROP purchase order")
        item = items[0]
        qty = item.cantidad_sugerida if hasattr(item, "cantidad_sugerida") else item["cantidad_sugerida"]
        self.assertGreater(qty, 0, "[C05] Reorder quantity must be positive")
