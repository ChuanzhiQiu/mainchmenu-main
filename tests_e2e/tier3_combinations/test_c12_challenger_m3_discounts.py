"""
Tier 3 — Cross-Feature Combination C12: Empirical Stress Test Suite for CLP Currency Discounts
and Kitchen Order Confirmation Consistency (Milestone 3 Remediation Round 2).

Empirically challenges:
1. Preservation of fixed CLP currency discounts ($1,000 CLP, $5,000 CLP, $0 CLP) during POS creation
   and subsequent kitchen order confirmation via /pedidos/<id>/confirmar/.
2. Negative discount clamping (e.g. -1,000 CLP, -5,000 CLP) to prevent total inflation.
3. Excessive discount clamping (discount > subtotal) ensuring monto_total never drops below 0.0.
4. Kitchen confirmation updating or overriding discounts with valid CLP amounts.
5. Non-finite (inf, nan) and string discount coercion handling without 500 crashes.
6. Multi-dish and combo order discount recalculation consistency in database.
7. Idempotent multi-confirmation preserving discount and total without duplicate stock deduction.
"""

import json
import math
from decimal import Decimal
from django.test import TestCase, Client
from django.utils import timezone

from Menu.models import Insumo, Menu, MovimientoStock, Orden, OrdenItem, Plato, RecetaItem
from tests_e2e.base import E2EBaseMixin


class TestC12EmpiricalDiscountsAndConfirmationStress(TestCase, E2EBaseMixin):
    """Rigorous empirical stress test harness for CLP currency discounts and kitchen confirmations."""

    def setUp(self):
        MovimientoStock.objects.all().delete()
        OrdenItem.objects.all().delete()
        Orden.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Menu.objects.all().delete()
        Insumo.objects.all().delete()
        self.client = Client()

        # Seed catalog
        self.insumo_carne = Insumo.objects.create(
            codigo="INS-CARNE",
            nombre="Carne Molida",
            unidad_medida="kg",
            stock_actual=Decimal("100.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("5000.000")
        )
        self.plato = Plato.objects.create(nombre="Hamburguesa Especial", valor=6000.0)
        RecetaItem.objects.create(plato=self.plato, insumo=self.insumo_carne, cantidad=Decimal("0.250"))

        self.plato2 = Plato.objects.create(nombre="Papas Rusticas", valor=3000.0)
        self.combo = Menu.objects.create(nombre="Combo Dupla", precio_menus=8000.0)
        self.combo.platos.add(self.plato, self.plato2)

    # =========================================================================
    # 1. $1,000 CLP Discount: POS Creation -> Kitchen Confirmation
    # =========================================================================
    def test_stress_confirmar_orden_preserves_1000_clp_discount(self):
        """
        Verify: Subtotal $6,000 CLP with $1,000 CLP discount.
        POS creates order -> total $5,000 CLP.
        Kitchen confirms with empty JSON -> discount must remain 1000.0, total must remain 5000.0.
        """
        payload = {
            "cliente": "Cliente 1000 CLP",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "descuento": 1000.0,
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        orden_id = resp.json()["orden_id"]

        # Verify DB before confirmation
        orden_pre = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_pre.descuento, 1000.0)
        self.assertEqual(orden_pre.monto_total, 5000.0)
        self.assertEqual(orden_pre.estado, Orden.ESTADO_EN_CURSO)
        self.assertFalse(orden_pre.stock_descontado)

        # Kitchen confirmation
        resp_confirm = self.client.post(
            f"/pedidos/{orden_id}/confirmar/",
            data=json.dumps({}),
            content_type="application/json"
        )
        self.assertEqual(resp_confirm.status_code, 200)

        # Verify DB after confirmation
        orden_post = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_post.descuento, 1000.0, f"Discount corrupted! Expected 1000.0, got {orden_post.descuento}")
        self.assertEqual(orden_post.monto_total, 5000.0, f"Total corrupted! Expected 5000.0, got {orden_post.monto_total}")
        self.assertEqual(orden_post.estado, Orden.ESTADO_COMPLETADA)
        self.assertTrue(orden_post.stock_descontado)
        self.assertIsNotNone(orden_post.fecha_completada)

    # =========================================================================
    # 2. $5,000 CLP Discount: POS Creation -> Kitchen Confirmation
    # =========================================================================
    def test_stress_confirmar_orden_preserves_5000_clp_discount(self):
        """
        Verify: Subtotal $6,000 CLP with large $5,000 CLP discount.
        POS creates order -> total $1,000 CLP.
        Kitchen confirms with empty JSON -> discount must remain 5000.0, total must remain 1000.0.
        """
        payload = {
            "cliente": "Cliente 5000 CLP",
            "canal_venta": "Delivery",
            "tipo_pago": "Tarj. Débito",
            "descuento": 5000.0,
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        orden_id = resp.json()["orden_id"]

        orden_pre = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_pre.descuento, 5000.0)
        self.assertEqual(orden_pre.monto_total, 1000.0)

        # Kitchen confirmation via form-data (standard POST)
        resp_confirm = self.client.post(f"/pedidos/{orden_id}/confirmar/", data={})
        self.assertEqual(resp_confirm.status_code, 200)

        orden_post = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_post.descuento, 5000.0, f"Discount corrupted! Expected 5000.0, got {orden_post.descuento}")
        self.assertEqual(orden_post.monto_total, 1000.0, f"Total corrupted! Expected 1000.0, got {orden_post.monto_total}")
        self.assertEqual(orden_post.estado, Orden.ESTADO_COMPLETADA)
        self.assertTrue(orden_post.stock_descontado)

    # =========================================================================
    # 3. $0 CLP Discount (Zero Discount): POS Creation -> Kitchen Confirmation
    # =========================================================================
    def test_stress_confirmar_orden_preserves_0_clp_discount(self):
        """
        Verify: Subtotal $6,000 CLP with $0 CLP discount.
        POS creates order -> total $6,000 CLP.
        Kitchen confirms -> discount must remain 0.0, total must remain 6000.0.
        """
        payload = {
            "cliente": "Cliente Cero CLP",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "descuento": 0.0,
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp = self.client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        orden_id = resp.json()["orden_id"]

        orden_pre = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_pre.descuento, 0.0)
        self.assertEqual(orden_pre.monto_total, 6000.0)

        resp_confirm = self.client.post(f"/pedidos/{orden_id}/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp_confirm.status_code, 200)

        orden_post = Orden.objects.get(id=orden_id)
        self.assertEqual(orden_post.descuento, 0.0)
        self.assertEqual(orden_post.monto_total, 6000.0)
        self.assertEqual(orden_post.estado, Orden.ESTADO_COMPLETADA)

    # =========================================================================
    # 4. Negative Discount Clamped to Zero: Model, POS, and Kitchen Confirm
    # =========================================================================
    def test_stress_negative_discount_clamped_to_zero_at_all_levels(self):
        """
        Verify:
        a) POS submission with negative discount is rejected with HTTP 400.
        b) Direct model instantiation with negative discount (-3000.0) clamps to 0.0 on save()
           and does not inflate monto_total above subtotal.
        c) Kitchen confirmation passing negative discount {"descuento": -2000.0} clamps to 0.0
           and computes total = subtotal (6000.0), never 8000.0.
        """
        # a) POS rejection
        payload_neg = {
            "cliente": "Cliente Negativo POS",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "descuento": -1500.0,
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}]
        }
        resp_pos = self.client.post("/pedidos/crear/", data=json.dumps(payload_neg), content_type="application/json")
        self.assertEqual(resp_pos.status_code, 400, "POS must reject negative discount with HTTP 400")

        # b) Model level clamping
        orden = Orden.objects.create(cliente="Cliente Negativo DB", descuento=-3000.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        orden.save()
        self.assertEqual(orden.descuento, 0.0, f"Model save() must clamp negative discount to 0.0, got {orden.descuento}")
        self.assertEqual(orden.calcular_total(), 6000.0, "Total must not be inflated by negative discount")

        # c) Kitchen confirmation passing negative discount
        resp_confirm = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"descuento": -2000.0}),
            content_type="application/json"
        )
        self.assertEqual(resp_confirm.status_code, 200)
        orden.refresh_from_db()
        self.assertEqual(orden.descuento, 0.0, f"Kitchen confirm must clamp negative discount to 0.0, got {orden.descuento}")
        self.assertEqual(orden.monto_total, 6000.0, f"Total must equal subtotal (6000.0), got {orden.monto_total}")

    # =========================================================================
    # 5. Discount Exceeding Subtotal: Total Clamped to 0.0
    # =========================================================================
    def test_stress_discount_exceeding_subtotal_clamped_to_zero(self):
        """
        Verify: Subtotal $6,000 CLP with discount $12,000 CLP.
        Total must clamp to exactly $0.0 (no negative balances in restaurant POS).
        """
        orden = Orden.objects.create(cliente="Cliente Super Descuento", descuento=0.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        orden.save()
        self.assertEqual(orden.monto_total, 6000.0)

        # Confirm with discount exceeding subtotal
        resp_confirm = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"descuento": 12000.0}),
            content_type="application/json"
        )
        self.assertEqual(resp_confirm.status_code, 200)

        orden.refresh_from_db()
        self.assertEqual(orden.descuento, 12000.0)
        self.assertEqual(orden.monto_total, 0.0, f"Total must be 0.0 when discount exceeds subtotal, got {orden.monto_total}")

    # =========================================================================
    # 6. Kitchen Confirmation Explicit Discount Updates
    # =========================================================================
    def test_stress_kitchen_confirm_can_update_or_apply_clp_discount(self):
        """
        Verify: Order placed with 0 discount ($6,000 total).
        Kitchen cashier applies a promotional $1,500 CLP discount at confirmation time.
        Database must reflect descuento = 1500.0 and monto_total = 4500.0.
        """
        orden = Orden.objects.create(cliente="Cliente Promo Caja", descuento=0.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)
        orden.save()

        resp_confirm = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"descuento": 1500.0, "tipo_pago": "Tarj. Débito"}),
            content_type="application/json"
        )
        self.assertEqual(resp_confirm.status_code, 200)

        orden.refresh_from_db()
        self.assertEqual(orden.descuento, 1500.0)
        self.assertEqual(orden.monto_total, 4500.0)
        self.assertEqual(orden.tipo_pago, "Tarj. Débito")
        self.assertEqual(orden.estado, Orden.ESTADO_COMPLETADA)

    # =========================================================================
    # 7. Non-Finite (inf, nan) and String Coercion Stress
    # =========================================================================
    def test_stress_kitchen_confirm_non_finite_and_string_discounts(self):
        """
        Verify:
        a) String representation "2500" converts to 2500.0 correctly.
        b) "inf" string does not crash and falls back safely to 0.0 or original discount.
        c) "nan" string does not crash and falls back safely.
        """
        # a) String numeric
        o1 = Orden.objects.create(cliente="Cliente Str Num", descuento=0.0)
        OrdenItem.objects.create(orden=o1, plato=self.plato, cantidad=1)
        o1.save()

        r1 = self.client.post(f"/pedidos/{o1.id}/confirmar/", data=json.dumps({"descuento": "2500"}), content_type="application/json")
        self.assertEqual(r1.status_code, 200)
        o1.refresh_from_db()
        self.assertEqual(o1.descuento, 2500.0)
        self.assertEqual(o1.monto_total, 3500.0)

        # b) Non-finite 'inf'
        o2 = Orden.objects.create(cliente="Cliente Inf", descuento=500.0)
        OrdenItem.objects.create(orden=o2, plato=self.plato, cantidad=1)
        o2.save()

        r2 = self.client.post(f"/pedidos/{o2.id}/confirmar/", data=json.dumps({"descuento": "inf"}), content_type="application/json")
        self.assertEqual(r2.status_code, 200)
        o2.refresh_from_db()
        self.assertFalse(math.isinf(o2.descuento))
        self.assertGreaterEqual(o2.monto_total, 0.0)
        self.assertLessEqual(o2.monto_total, 6000.0)

        # c) Non-finite 'nan'
        o3 = Orden.objects.create(cliente="Cliente NaN", descuento=800.0)
        OrdenItem.objects.create(orden=o3, plato=self.plato, cantidad=1)
        o3.save()

        r3 = self.client.post(f"/pedidos/{o3.id}/confirmar/", data=json.dumps({"descuento": "nan"}), content_type="application/json")
        self.assertEqual(r3.status_code, 200)
        o3.refresh_from_db()
        self.assertFalse(math.isnan(o3.descuento))
        self.assertGreaterEqual(o3.monto_total, 0.0)
        self.assertLessEqual(o3.monto_total, 6000.0)

    # =========================================================================
    # 8. Complex Combos + Individual Dishes with CLP Discount
    # =========================================================================
    def test_stress_combo_and_dishes_with_clp_discount_reconciliation(self):
        """
        Verify: Order with 1 Combo ($8,000) + 2 Platos ($6,000 each = $12,000)
        Subtotal = $20,000 CLP.
        Descuento = $3,500 CLP.
        Expected Total = $16,500 CLP.
        Kitchen confirmation preserves exact values and deducts stock accurately.
        """
        orden = Orden.objects.create(cliente="Cliente Banquete Combo", descuento=3500.0)
        OrdenItem.objects.create(orden=orden, menu=self.combo, cantidad=1)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=2)
        orden.save()

        self.assertEqual(orden.calcular_total(), 16500.0)

        resp_confirm = self.client.post(f"/pedidos/{orden.id}/confirmar/", data={})
        self.assertEqual(resp_confirm.status_code, 200)

        orden.refresh_from_db()
        self.assertEqual(orden.descuento, 3500.0)
        self.assertEqual(orden.monto_total, 16500.0)
        self.assertTrue(orden.stock_descontado)

        # Insumo deduction: Combo has 1 plato (0.250 kg) + 2 plato (2 * 0.250 = 0.500 kg) = 0.750 kg
        self.insumo_carne.refresh_from_db()
        self.assertEqual(self.insumo_carne.stock_actual, Decimal("99.250"))

    # =========================================================================
    # 9. Idempotent Confirmation with Discount
    # =========================================================================
    def test_stress_idempotent_multi_confirmation_preserves_discount_no_double_deduction(self):
        """
        Verify: Repeated confirmation calls on the same order preserve the CLP discount,
        keep monto_total identical, and DO NOT double-deduct inventory.
        """
        orden = Orden.objects.create(cliente="Cliente Idempotente", descuento=1200.0)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=2)  # subtotal 12,000 -> total 10,800
        orden.save()

        # Confirmation 1
        r1 = self.client.post(f"/pedidos/{orden.id}/confirmar/", data={})
        self.assertEqual(r1.status_code, 200)
        orden.refresh_from_db()
        first_completed = orden.fecha_completada
        self.assertEqual(orden.descuento, 1200.0)
        self.assertEqual(orden.monto_total, 10800.0)

        self.insumo_carne.refresh_from_db()
        stock_after_first = self.insumo_carne.stock_actual  # 100 - (2 * 0.250) = 99.500
        self.assertEqual(stock_after_first, Decimal("99.500"))

        # Confirmation 2 (re-submission or double-click)
        r2 = self.client.post(f"/pedidos/{orden.id}/confirmar/", data={})
        self.assertEqual(r2.status_code, 200)
        orden.refresh_from_db()

        self.assertEqual(orden.descuento, 1200.0, "Discount must remain unchanged on repeat confirm")
        self.assertEqual(orden.monto_total, 10800.0, "Total must remain unchanged on repeat confirm")
        self.assertEqual(orden.fecha_completada, first_completed, "Completion timestamp must be idempotent")

        self.insumo_carne.refresh_from_db()
        self.assertEqual(self.insumo_carne.stock_actual, stock_after_first, "Stock must NOT be double-deducted")


class TestC12EmpiricalKDSPollingAndConcurrencyStress(TestCase, E2EBaseMixin):
    """Stress testing KDS query efficiency under 100 orders and rapid inventory adjustments."""

    def setUp(self):
        MovimientoStock.objects.all().delete()
        OrdenItem.objects.all().delete()
        Orden.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Menu.objects.all().delete()
        Insumo.objects.all().delete()
        self.client = Client()

        self.p_burger = Plato.objects.create(nombre="Hamburguesa Deluxe", valor=6500)
        self.p_bebida = Plato.objects.create(nombre="Bebida Cola", valor=1800)
        self.combo = Menu.objects.create(nombre="Combo Mega", precio_menus=7500)
        self.combo.platos.add(self.p_burger, self.p_bebida)

    def test_stress_kds_polling_query_boundedness_up_to_100_orders(self):
        """
        Adversarial Scale Test:
        Inject 100 active orders with dishes and combos.
        Verify query count is strictly <= 5 queries, demonstrating true O(1) query complexity.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        for i in range(100):
            o = Orden.objects.create(
                cliente=f"Cliente Scale-{i}",
                canal_venta="Local",
                tipo_pago="Efectivo",
                estado=Orden.ESTADO_EN_CURSO
            )
            OrdenItem.objects.create(orden=o, plato=self.p_burger, cantidad=1)
            OrdenItem.objects.create(orden=o, menu=self.combo, cantidad=1)

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/", HTTP_HX_REQUEST="true")

        self.assertEqual(resp.status_code, 200)
        query_count = len(ctx.captured_queries)
        self.assertLess(
            query_count, 30,
            f"KDS query count exceeded boundary under 100 orders: {query_count}"
        )
        self.assertEqual(
            query_count, 5,
            f"Expected exactly 5 prefetched queries under 100 orders, got {query_count}"
        )

    def test_stress_inventory_fractional_precision_kardex(self):
        """
        Verify fractional inventory adjustments down to 0.001 units:
        Initial: 10.000 kg
        + 0.125 kg (Ingreso)
        - 0.050 kg (Merma)
        + 1.333 kg (Ingreso)
        - 0.408 kg (Merma)
        Net: 10 + 0.125 - 0.050 + 1.333 - 0.408 = 11.000 kg exactly.
        """
        insumo = Insumo.objects.create(
            codigo="INS-FRAC",
            nombre="Levadura Seca",
            unidad_medida="kg",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("1.000")
        )

        steps = [
            (Decimal("0.125"), MovimientoStock.TIPO_INGRESO_COMPRA),
            (Decimal("0.050"), MovimientoStock.TIPO_MERMA),
            (Decimal("1.333"), MovimientoStock.TIPO_INGRESO_COMPRA),
            (Decimal("0.408"), MovimientoStock.TIPO_MERMA),
        ]

        for qty, tipo in steps:
            resp = self.client.post("/inventario/ajuste/", {
                "insumo_id": insumo.id,
                "cantidad": str(qty),
                "tipo": tipo
            }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(resp.status_code, 200)

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("11.000"))

        movs = list(MovimientoStock.objects.filter(insumo=insumo).order_by("id"))
        self.assertEqual(len(movs), 4)
        for i in range(1, len(movs)):
            self.assertEqual(movs[i].stock_anterior, movs[i - 1].stock_nuevo)
