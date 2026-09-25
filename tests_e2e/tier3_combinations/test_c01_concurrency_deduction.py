"""
Tier 3 — Cross-Feature Combination C01: F02 (Cloud DB) + F06 (Insumo) + F10 (Stock Deduction).
Verifies multi-transaction order completions under database transaction isolation.
"""

from decimal import Decimal
from tests_e2e.base import E2ETestCase


class TestC01ConcurrencyDeduction(E2ETestCase):
    """Pairwise combination: Cloud DB transactions + Insumo Model + Atomic Deduction Service."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C01")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C01")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C01")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="C01")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="C01")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="C01")

    def test_c01_01_sequential_multi_order_deduction_reconciliation(self):
        """TC-C01-01: 5 distinct orders completed consecutively deduct exact cumulative inventory."""
        insumo = self.Insumo.objects.create(
            codigo="INS-BURGER", nombre="Carne Hamburguesa", unidad_medida="kg",
            stock_actual=Decimal("50.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("6000.000")
        )
        plato = self.Plato.objects.create(nombre="Burger Simple", valor=5000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.200"))

        # Create 5 orders of 2 burgers each (5 * 2 * 0.200 = 2.000 kg consumed)
        for i in range(5):
            orden = self.Orden.objects.create(cliente=f"Cliente Concurrency {i}", canal_venta="Local", tipo_pago="Efectivo")
            self.OrdenItem.objects.create(orden=orden, plato=plato, cantidad=2)
            res = self.descontar_stock_orden(orden.id)
            self.assertTrue(res.get("success"), f"Order {i} deduction failed")

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("48.000"), "[C01] 50.000 - 2.000 = 48.000 kg")

    def test_c01_02_transaction_rollback_preserves_initial_stock(self):
        """TC-C01-02: Failure inside transaction leaves stock completely unchanged."""
        insumo = self.Insumo.objects.create(
            codigo="INS-SAFE", nombre="Insumo Seguro", unidad_medida="kg",
            stock_actual=Decimal("20.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        initial_stock = insumo.stock_actual
        # Attempt deduction on non-existent order
        res = self.descontar_stock_orden(-999)
        self.assertFalse(res.get("success"))
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, initial_stock, "[C01] Failed deduction should not alter stock")
