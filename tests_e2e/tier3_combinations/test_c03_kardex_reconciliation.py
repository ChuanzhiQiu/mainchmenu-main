"""
Tier 3 — Cross-Feature Combination C03: F08 (Kardex) + F10 (Deduction) + F11 (Confirm Hook).
Verifies that confirming an order writes an immutable Kardex audit trace exactly matching stock changes.
"""

import json
from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestC03KardexReconciliation(E2ETestCase):
    """Pairwise combination: Kardex Logging + Stock Deduction + Order Confirmation Hook."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C03")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C03")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C03")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="C03")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="C03")
        self.MovimientoStock = self.require_model("Menu", "MovimientoStock", feature_id="C03")

    def test_c03_01_confirm_hook_creates_matching_kardex_movement(self):
        """TC-C03-01: Confirming order generates Kardex movements where delta exactly equals deducted stock."""
        insumo = self.Insumo.objects.create(
            codigo="INS-KARDEX", nombre="Tomates Granel", unidad_medida="kg",
            stock_actual=Decimal("20.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1500.000")
        )
        plato = self.Plato.objects.create(nombre="Ensalada Tomate", valor=3000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.350"))

        orden = self.Orden.objects.create(cliente="Mesa Audit", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=4)

        # Confirm via endpoint
        client = self.get_client()
        resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({"tipo_pago": "Efectivo"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        # Reconcile Kardex
        mov = self.MovimientoStock.objects.filter(orden=orden, insumo=insumo).first()
        self.assertIsNotNone(mov, "[C03] Kardex movement must exist for confirmed order")
        self.assertEqual(mov.tipo, "CONSUMO_ORDEN")
        self.assertEqual(mov.stock_anterior, Decimal("20.000"))
        self.assertEqual(mov.cantidad, Decimal("1.400"))  # 4 * 0.350
        self.assertEqual(mov.stock_nuevo, Decimal("18.600"))

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, mov.stock_nuevo, "[C03] Insumo current stock must equal latest Kardex stock_nuevo")

    def test_c03_02_kardex_sum_matches_stock_depletion(self):
        """TC-C03-02: Total sum of Kardex consumed quantities equals initial stock minus current stock."""
        ins = self.Insumo.objects.create(
            codigo="INS-KSUM", nombre="Harina", unidad_medida="kg",
            stock_actual=Decimal("50.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("800.000")
        )
        p = self.Plato.objects.create(nombre="Pizza", valor=7000)
        self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.250"))

        client = self.get_client()
        for i in range(3):
            o = self.Orden.objects.create(cliente=f"Cliente Pizza {i}", canal_venta="Local", tipo_pago="Efectivo")
            self.OrdenItem.objects.create(orden=o, plato=p, cantidad=2)
            client.post(f"/pedidos/{o.id}/confirmar/", data=json.dumps({}), content_type="application/json")

        movements = self.MovimientoStock.objects.filter(insumo=ins, tipo="CONSUMO_ORDEN")
        total_kardex_consumed = sum(m.cantidad for m in movements)
        self.assertEqual(total_kardex_consumed, Decimal("1.500"))  # 3 orders * 2 pizzas * 0.250

        ins.refresh_from_db()
        self.assertEqual(Decimal("50.000") - total_kardex_consumed, ins.stock_actual)
