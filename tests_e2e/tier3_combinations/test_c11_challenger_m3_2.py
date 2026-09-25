"""
Tier 3 — Cross-Feature Combination C11: Adversarial Challenge Suite for Milestone 3 (F16, F17, F18).
Empirically challenges:
1. KDS Polling Query Efficiency: Exact CaptureQueriesContext measurement under 0, 10, and 50 active
   orders with complex multi-dish combos, asserting O(1) query complexity bounded strictly (<30).
2. HTMX Partial Polling Isolation: Verification that completed/eliminated orders never leak into KDS
   partial responses, and verifying empty-state behavior ("Cocina al Día").
3. KDS FIFO Order Prioritization: Empirical verification of chronological sorting (fecha ASC, hora ASC, id ASC)
   even when orders are inserted out of order or share identical dates/hours.
4. Complex Combo & BOM Explosion in KDS: Verifies line cooks see individual exploded items and combo tags.
5. Concurrency & Lost Updates on Insumo Adjustments: Simulates rapid interleaved adjustments on the same Insumo,
   verifying no lost updates and mathematical correctness of stock.
6. Kardex Audit Chain Continuity: Strict verification that for every MovimientoStock in sequence,
   movement[i].stock_anterior == movement[i-1].stock_nuevo with zero gaps or data corruption.
7. Extreme Stock Values: Zero stock (0.000), negative stock (-25.750), giant stock (500,000,000.000),
   and graceful HTTP 400 rejection (no HTTP 500) for numeric overflow beyond Decimal(12,3).
8. Input Guardrails & HTTP Status Codes: Rejection of invalid insumo_id, malformed JSON, missing fields,
   and non-POST HTTP methods.
"""

import datetime
from decimal import Decimal
import json
import re

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, Client
from django.test.utils import CaptureQueriesContext

from Menu.models import Insumo, Menu, MovimientoStock, Orden, OrdenItem, Plato, RecetaItem
from tests_e2e.base import E2EBaseMixin


class TestC11ChallengerM3Adversarial(TestCase, E2EBaseMixin):
    """Adversarial challenge test harness for KDS polling, FIFO queuing, and inventory adjustments."""

    def setUp(self):
        self.client = Client()
        MovimientoStock.objects.all().delete()
        OrdenItem.objects.all().delete()
        Orden.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Menu.objects.all().delete()
        Insumo.objects.all().delete()

        # Seed catalog with dishes and combos
        self.p_burger = Plato.objects.create(nombre="Hamburguesa Clásica", valor=5500)
        self.p_papas = Plato.objects.create(nombre="Papas Fritas Medianas", valor=2200)
        self.p_bebida = Plato.objects.create(nombre="Bebida Lata 350cc", valor=1500)
        self.p_postre = Plato.objects.create(nombre="Helado Artesanal", valor=2800)

        # Combo Familiar (4 dishes)
        self.combo_familiar = Menu.objects.create(nombre="Combo Familiar Mega", precio_menus=9900)
        self.combo_familiar.platos.add(self.p_burger, self.p_papas, self.p_bebida, self.p_postre)

        # Combo Duo (2 dishes)
        self.combo_duo = Menu.objects.create(nombre="Combo Duo Express", precio_menus=6800)
        self.combo_duo.platos.add(self.p_burger, self.p_bebida)

    # =========================================================================
    # 1. KDS Polling Query Efficiency (0, 10, 50 orders)
    # =========================================================================
    def test_adv_kds_01_query_count_strictly_bounded_0_10_50_orders(self):
        """
        Adversarial Test 1: Measure exact query count with CaptureQueriesContext under 0, 10, and 50
        active orders with complex combos. Verify queries remain strictly bounded (<30).
        """
        # Baseline: 0 active orders
        with CaptureQueriesContext(connection) as ctx_0:
            resp_0 = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp_0.status_code, 200)
        count_0 = len(ctx_0.captured_queries)
        self.assertLess(count_0, 30, f"Query count under 0 orders ({count_0}) exceeds threshold 30")
        self.assertEqual(count_0, 1, f"Expected exactly 1 query for 0 orders, got {count_0}")

        # Inject 10 orders with complex combos and dishes
        for i in range(10):
            o = Orden.objects.create(
                cliente=f"Cliente Combo 10-{i}",
                canal_venta="Local",
                tipo_pago="Efectivo",
                estado=Orden.ESTADO_EN_CURSO
            )
            OrdenItem.objects.create(orden=o, plato=self.p_burger, cantidad=2)
            OrdenItem.objects.create(orden=o, menu=self.combo_familiar, cantidad=1)
            OrdenItem.objects.create(orden=o, menu=self.combo_duo, cantidad=1)

        with CaptureQueriesContext(connection) as ctx_10:
            resp_10 = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp_10.status_code, 200)
        count_10 = len(ctx_10.captured_queries)
        self.assertLess(count_10, 30, f"Query count under 10 orders ({count_10}) exceeds threshold 30")

        # Inject 40 more orders (total 50 active orders with complex combos)
        for i in range(40):
            o = Orden.objects.create(
                cliente=f"Cliente Combo 50-{i}",
                canal_venta="Delivery",
                tipo_pago="Debito",
                estado=Orden.ESTADO_EN_CURSO
            )
            OrdenItem.objects.create(orden=o, plato=self.p_papas, cantidad=3)
            OrdenItem.objects.create(orden=o, plato=self.p_postre, cantidad=1)
            OrdenItem.objects.create(orden=o, menu=self.combo_familiar, cantidad=2)

        with CaptureQueriesContext(connection) as ctx_50:
            resp_50 = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp_50.status_code, 200)
        count_50 = len(ctx_50.captured_queries)
        self.assertLess(count_50, 30, f"Query count under 50 orders ({count_50}) exceeds threshold 30")

        # Assert O(1) query complexity: count for 50 orders must equal count for 10 orders
        self.assertEqual(
            count_50, count_10,
            f"Query count scaled with orders: {count_10} queries for 10 orders vs {count_50} queries for 50 orders (N+1 leak!)"
        )
        self.assertEqual(count_50, 5, f"Expected exactly 5 eager prefetched queries, got {count_50}")

    # =========================================================================
    # 2. HTMX Partial Polling Isolation & Leakage Prevention
    # =========================================================================
    def test_adv_kds_02_htmx_partial_no_leakage_completed_and_eliminated(self):
        """
        Adversarial Test 2: Ensure completed and eliminated orders never leak through HTMX partial polling responses.
        Also verifies clean empty-state rendering when all orders are completed.
        """
        # Create 3 active, 3 completed, and 3 eliminated orders
        active_orders = []
        for i in range(3):
            o = Orden.objects.create(
                cliente=f"Activo En Preparación {i}",
                canal_venta="Local",
                tipo_pago="Efectivo",
                estado=Orden.ESTADO_EN_CURSO
            )
            OrdenItem.objects.create(orden=o, plato=self.p_burger, cantidad=1)
            active_orders.append(o)

        completed_orders = []
        for i in range(3):
            o = Orden.objects.create(
                cliente=f"Completado Despachado {i}",
                canal_venta="Delivery",
                tipo_pago="Debito",
                estado=Orden.ESTADO_COMPLETADA,
                stock_descontado=True
            )
            OrdenItem.objects.create(orden=o, plato=self.p_papas, cantidad=1)
            completed_orders.append(o)

        eliminated_orders = []
        for i in range(3):
            o = Orden.objects.create(
                cliente=f"Eliminado Anulado {i}",
                canal_venta="UberEats",
                tipo_pago="Efectivo",
                estado=Orden.ESTADO_ELIMINADA
            )
            OrdenItem.objects.create(orden=o, plato=self.p_bebida, cantidad=1)
            eliminated_orders.append(o)

        # 1. Poll via HTMX
        resp = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Must NOT include full HTML doctype or base layout
        self.assertNotIn("<!DOCTYPE html>", html)
        self.assertNotIn("<html", html)
        self.assertNotIn("Historial", html)

        # Must include all active orders
        for o in active_orders:
            self.assertIn(f"#{o.id}", html)
            self.assertIn(o.cliente, html)

        # Must NOT include any completed or eliminated orders
        for o in completed_orders:
            self.assertNotIn(o.cliente, html, f"Completed order {o.cliente} leaked into HTMX KDS polling!")
        for o in eliminated_orders:
            self.assertNotIn(o.cliente, html, f"Eliminated order {o.cliente} leaked into HTMX KDS polling!")

        # 2. Transition one active order to Completada and verify dynamic disappearance
        target = active_orders[0]
        target.estado = Orden.ESTADO_COMPLETADA
        target.save(update_fields=["estado"])

        resp2 = self.client.get("/", HTTP_HX_REQUEST="true")
        html2 = resp2.content.decode("utf-8")
        self.assertNotIn(target.cliente, html2, "Newly completed order did not disappear from KDS polling!")
        self.assertIn(active_orders[1].cliente, html2)
        self.assertIn(active_orders[2].cliente, html2)

        # 3. Transition remaining active orders to complete -> verify empty state
        active_orders[1].estado = Orden.ESTADO_COMPLETADA
        active_orders[1].save(update_fields=["estado"])
        active_orders[2].estado = Orden.ESTADO_ELIMINADA
        active_orders[2].save(update_fields=["estado"])

        resp3 = self.client.get("/", HTTP_HX_REQUEST="true")
        html3 = resp3.content.decode("utf-8")
        self.assertIn("Cocina al Día", html3, "Empty state title missing when all orders are completed")
        self.assertIn("No hay pedidos pendientes en preparación", html3)

    # =========================================================================
    # 3. KDS FIFO Order Prioritization (Temporal Ordering)
    # =========================================================================
    def test_adv_kds_03_fifo_order_prioritization_strict_temporal_ordering(self):
        """
        Adversarial Test 3: Verify FIFO order prioritization (fecha ASC, hora ASC, id ASC)
        even when orders are inserted out of chronological order or share identical timestamps.
        """
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        tomorrow = today + datetime.timedelta(days=1)

        # Insert orders intentionally out of chronological order:
        # Order 1: Today 14:00 (ID 1)
        # Order 2: Yesterday 20:00 (ID 2 - should be FIRST!)
        # Order 3: Today 11:00 (ID 3 - should be SECOND!)
        # Order 4: Today 11:00 (ID 4 - identical time to 3, ordered by ID ASC -> THIRD!)
        # Order 5: Tomorrow 09:00 (ID 5 - should be LAST!)
        o1 = Orden.objects.create(cliente="Order Today 14:00", estado=Orden.ESTADO_EN_CURSO)
        Orden.objects.filter(id=o1.id).update(fecha=today, hora=datetime.time(14, 0, 0))

        o2 = Orden.objects.create(cliente="Order Yesterday 20:00", estado=Orden.ESTADO_EN_CURSO)
        Orden.objects.filter(id=o2.id).update(fecha=yesterday, hora=datetime.time(20, 0, 0))

        o3 = Orden.objects.create(cliente="Order Today 11:00 A", estado=Orden.ESTADO_EN_CURSO)
        Orden.objects.filter(id=o3.id).update(fecha=today, hora=datetime.time(11, 0, 0))

        o4 = Orden.objects.create(cliente="Order Today 11:00 B", estado=Orden.ESTADO_EN_CURSO)
        Orden.objects.filter(id=o4.id).update(fecha=today, hora=datetime.time(11, 0, 0))

        o5 = Orden.objects.create(cliente="Order Tomorrow 09:00", estado=Orden.ESTADO_EN_CURSO)
        Orden.objects.filter(id=o5.id).update(fecha=tomorrow, hora=datetime.time(9, 0, 0))

        expected_order = [o2.id, o3.id, o4.id, o1.id, o5.id]

        resp = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Extract order card IDs in appearance order
        rendered_order = [int(m) for m in re.findall(r'data-order-id="(\d+)"', html)]
        self.assertEqual(
            rendered_order, expected_order,
            f"KDS FIFO sequence violation! Expected: {expected_order}, Actual rendered: {rendered_order}"
        )

    # =========================================================================
    # 4. Complex Combo & BOM Explosion in KDS Cards
    # =========================================================================
    def test_adv_kds_04_complex_combo_explosion_in_kds_cards(self):
        """
        Adversarial Test 4: Verifies that kitchen comanda cards display dishes, combos,
        and full combo explosion with sub-dishes.
        """
        o = Orden.objects.create(cliente="Comanda Gourmet", canal_venta="PedidosYa", tipo_pago="Online", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=o, plato=self.p_burger, cantidad=3)
        OrdenItem.objects.create(orden=o, menu=self.combo_familiar, cantidad=2)

        resp = self.client.get("/", HTTP_HX_REQUEST="true")
        html = resp.content.decode("utf-8")

        # Check order card rendered
        self.assertIn(f"data-order-id=\"{o.id}\"", html)
        self.assertIn("Comanda Gourmet", html)
        self.assertIn("PedidosYa", html)

        # Check individual dish and quantity
        self.assertIn("3x", html)
        self.assertIn("Hamburguesa Clásica", html)

        # Check combo tag and quantity
        self.assertIn("2x", html)
        self.assertIn("[COMBO] Combo Familiar Mega", html)

        # Check exploded sub-platos bullets
        self.assertIn("• Hamburguesa Clásica", html)
        self.assertIn("• Papas Fritas Medianas", html)
        self.assertIn("• Bebida Lata 350cc", html)
        self.assertIn("• Helado Artesanal", html)

    # =========================================================================
    # 5. Inventory Adjustment: Sequential Rapid Adjustments & No Lost Updates
    # =========================================================================
    def test_adv_stock_01_concurrent_adjustments_no_lost_updates(self):
        """
        Adversarial Test 5: Rapid interleaved manual adjustments on the same Insumo record.
        Verifies mathematical correctness of stock and absence of lost updates.
        """
        insumo = Insumo.objects.create(
            codigo="INS-RAPID-ADJ",
            nombre="Carne Vacuno Molida",
            unidad_medida="kg",
            stock_actual=Decimal("100.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("5500.000")
        )

        deltas = [
            (Decimal("10.000"), MovimientoStock.TIPO_INGRESO_COMPRA, +10),
            (Decimal("4.500"), MovimientoStock.TIPO_MERMA, -4.5),
            (Decimal("15.250"), MovimientoStock.TIPO_INGRESO_COMPRA, +15.25),
            (Decimal("2.750"), MovimientoStock.TIPO_MERMA_DESPERDICIO, -2.75),
            (Decimal("8.000"), MovimientoStock.TIPO_INGRESO_COMPRA, +8),
            (Decimal("1.000"), MovimientoStock.TIPO_MERMA, -1),
            (Decimal("20.000"), MovimientoStock.TIPO_INGRESO_COMPRA, +20),
            (Decimal("5.000"), MovimientoStock.TIPO_MERMA, -5),
            (Decimal("0.500"), MovimientoStock.TIPO_MERMA, -0.5),
            (Decimal("12.500"), MovimientoStock.TIPO_INGRESO_COMPRA, +12.5),
        ]

        expected_stock = Decimal("100.000")
        for qty, tipo, net_effect in deltas:
            expected_stock += Decimal(str(net_effect))
            resp = self.client.post("/inventario/ajuste/", {
                "insumo_id": insumo.id,
                "cantidad": str(qty),
                "tipo": tipo,
                "notas": f"Ajuste rapid delta {net_effect}"
            }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(resp.status_code, 200, f"Adjustment failed: {resp.content}")
            data = resp.json()
            self.assertTrue(data.get("success"))

        insumo.refresh_from_db()
        self.assertEqual(
            insumo.stock_actual, expected_stock,
            f"Lost updates detected! Expected {expected_stock}, found {insumo.stock_actual}"
        )

    # =========================================================================
    # 6. Kardex Audit Chain Continuity
    # =========================================================================
    def test_adv_stock_02_kardex_chain_integrity_and_audit_reconciliation(self):
        """
        Adversarial Test 6: Verify that for every MovimientoStock in sequence,
        movement[i].stock_anterior == movement[i-1].stock_nuevo with zero gaps or data corruption.
        """
        insumo = Insumo.objects.create(
            codigo="INS-KARDEX-CHAIN",
            nombre="Aceite Maravilla",
            unidad_medida="lt",
            stock_actual=Decimal("50.000"),
            stock_minimo=Decimal("5.000")
        )

        # Execute 5 adjustments of varying types (new stock vs delta)
        steps = [
            {"nuevo_stock": "65.000", "tipo": MovimientoStock.TIPO_AJUSTE_MANUAL},
            {"cantidad": "10.000", "tipo": MovimientoStock.TIPO_INGRESO_COMPRA},
            {"cantidad": "3.500", "tipo": MovimientoStock.TIPO_MERMA},
            {"nuevo_stock": "80.000", "tipo": MovimientoStock.TIPO_AJUSTE_MANUAL},
            {"cantidad": "12.000", "tipo": MovimientoStock.TIPO_MERMA_DESPERDICIO},
        ]

        for s in steps:
            payload = {"insumo_id": insumo.id, **s}
            resp = self.client.post("/inventario/ajuste/", payload, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(resp.status_code, 200)

        movimientos = list(MovimientoStock.objects.filter(insumo=insumo).order_by("id"))
        self.assertEqual(len(movimientos), 5)

        # Verify continuity: initial before
        self.assertEqual(movimientos[0].stock_anterior, Decimal("50.000"))

        for i in range(len(movimientos)):
            mov = movimientos[i]
            self.assertIsNone(mov.orden, "Manual adjustments must have orden=None")
            self.assertIsNotNone(mov.fecha_hora)

            if i > 0:
                prev = movimientos[i - 1]
                self.assertEqual(
                    mov.stock_anterior, prev.stock_nuevo,
                    f"Kardex break at step {i}! Previous new: {prev.stock_nuevo}, current before: {mov.stock_anterior}"
                )

        # Final stock matches last movement's stock_nuevo
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, movimientos[-1].stock_nuevo)

    # =========================================================================
    # 7. Extreme Stock Values: Zero, Negative, Giant Stock
    # =========================================================================
    def test_adv_stock_03_extreme_stock_values_zero_negative_giant(self):
        """
        Adversarial Test 7: Verify handling of extreme stock values:
        - 0.000 (stock agotado)
        - Negative stock (-25.750, operational continuity in restaurant rush)
        - Giant stock within DecimalField(12,3) max range (500,000,000.000)
        - Decimal overflow (> max_digits=12) handled gracefully with HTTP 400 (no 500 crash).
        """
        insumo = Insumo.objects.create(
            codigo="INS-EXTREME-BOUNDS",
            nombre="Harina Pan",
            unidad_medida="kg",
            stock_actual=Decimal("100.000"),
            stock_minimo=Decimal("15.000")
        )

        # 1. Zero stock
        resp_zero = self.client.post("/inventario/ajuste/", {
            "insumo_id": insumo.id,
            "nuevo_stock": "0.000",
            "tipo": MovimientoStock.TIPO_AJUSTE_MANUAL,
            "notas": "Stock agotado total"
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp_zero.status_code, 200)
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("0.000"))
        self.assertTrue(insumo.esta_bajo_minimo)

        # 2. Negative stock
        resp_neg = self.client.post("/inventario/ajuste/", {
            "insumo_id": insumo.id,
            "nuevo_stock": "-25.750",
            "tipo": MovimientoStock.TIPO_AJUSTE_MANUAL,
            "notas": "Consumo en quiebre operativo"
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp_neg.status_code, 200)
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("-25.750"))
        self.assertTrue(insumo.esta_bajo_minimo)

        # 3. Giant stock within Decimal(12,3) limits
        resp_giant = self.client.post("/inventario/ajuste/", {
            "insumo_id": insumo.id,
            "nuevo_stock": "500000000.000",
            "tipo": MovimientoStock.TIPO_INGRESO_COMPRA,
            "notas": "Ingreso masivo bodega central"
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp_giant.status_code, 200)
        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("500000000.000"))
        self.assertFalse(insumo.esta_bajo_minimo)

        # 4. Overflow value exceeding DecimalField(12,3) precision
        resp_overflow = self.client.post("/inventario/ajuste/", {
            "insumo_id": insumo.id,
            "nuevo_stock": "9999999999999999999.999",
            "tipo": MovimientoStock.TIPO_AJUSTE_MANUAL,
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        # Must return HTTP 400 bad request without throwing unhandled 500 error
        self.assertEqual(resp_overflow.status_code, 400)
        data = resp_overflow.json()
        self.assertFalse(data.get("success"))

    # =========================================================================
    # 8. Guardrails & HTTP Boundary Conditions
    # =========================================================================
    def test_adv_stock_04_ajustar_stock_guardrails_and_validation(self):
        """
        Adversarial Test 8: Verify robust input rejection on ajustar_stock_view:
        - Non-existent insumo_id -> 404
        - Missing insumo_id -> 400
        - Missing both nuevo_stock and cantidad -> 400
        - Malformed JSON -> 400
        - GET request -> 405 Method Not Allowed
        - Non-numeric strings in numeric fields -> 400
        """
        insumo = Insumo.objects.create(codigo="INS-GUARD", nombre="Insumo Guard", stock_actual=Decimal("10.000"))

        # 1. Non-existent ID
        r1 = self.client.post("/inventario/ajuste/", {"insumo_id": 99999, "nuevo_stock": "5.0"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r1.status_code, 404)

        # 2. Missing ID
        r2 = self.client.post("/inventario/ajuste/", {"nuevo_stock": "5.0"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r2.status_code, 400)

        # 3. Missing both fields
        r3 = self.client.post("/inventario/ajuste/", {"insumo_id": insumo.id}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r3.status_code, 400)

        # 4. Malformed JSON
        r4 = self.client.post(
            "/inventario/ajuste/",
            data="{malformed: json...}",
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(r4.status_code, 400)

        # 5. GET request
        r5 = self.client.get("/inventario/ajuste/")
        self.assertEqual(r5.status_code, 405)

        # 6. Non-numeric value
        r6 = self.client.post("/inventario/ajuste/", {
            "insumo_id": insumo.id,
            "cantidad": "not-a-number"
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r6.status_code, 400)


class TestC11TransactionalConcurrency(TransactionTestCase):
    """
    Adversarial multi-threaded concurrency and dashboard integration test harness.
    Uses TransactionTestCase to allow real concurrent transactions across separate threads.
    """

    def setUp(self):
        MovimientoStock.objects.all().delete()
        Insumo.objects.all().delete()

    def test_adv_stock_05_multithreaded_concurrent_adjustments_kardex_reconciliation(self):
        """
        Adversarial Test 9: Real multi-threaded concurrent requests to /inventario/ajuste/
        verifying that even under thread contention and SQLite retry:
        1. No lost updates occur.
        2. Final stock exactly equals initial_stock + sum(deltas).
        3. Kardex chain is strictly unbroken (movement[i].stock_anterior == movement[i-1].stock_nuevo).
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        insumo = Insumo.objects.create(
            codigo="INS-THREAD-SAFE",
            nombre="Harina Industrial MultiThread",
            unidad_medida="kg",
            stock_actual=Decimal("100.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("1200.000")
        )

        def worker_adjustment(worker_id, delta, tipo):
            from django.db import connection
            connection.close()
            c = Client()
            for attempt in range(12):
                try:
                    resp = c.post("/inventario/ajuste/", {
                        "insumo_id": insumo.id,
                        "cantidad": str(delta),
                        "tipo": tipo,
                        "notas": f"Worker {worker_id} attempt {attempt}"
                    }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                    if resp.status_code == 200:
                        return worker_id, 200, resp.json()
                    elif resp.status_code == 500 and "locked" in resp.json().get("message", "").lower():
                        time.sleep(0.04 * (attempt + 1))
                        continue
                    return worker_id, resp.status_code, resp.json()
                except Exception:
                    time.sleep(0.04 * (attempt + 1))
                finally:
                    connection.close()
            return worker_id, 500, "Lock timeout"

        # 8 concurrent workers with alternating INGRESO (+3.0) and MERMA (-3.0)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(
                    worker_adjustment,
                    i,
                    Decimal("3.000"),
                    MovimientoStock.TIPO_INGRESO_COMPRA if i % 2 == 0 else MovimientoStock.TIPO_MERMA
                )
                for i in range(8)
            ]
            results = [f.result() for f in as_completed(futures)]

        success_results = [r for r in results if r[1] == 200]
        self.assertEqual(len(success_results), 8, f"Expected all 8 concurrent workers to succeed, got {len(success_results)}")

        insumo.refresh_from_db()
        # 4 ingresos (+12) and 4 mermas (-12) => Net 0 => final stock 100.000
        self.assertEqual(insumo.stock_actual, Decimal("100.000"), "Final stock after balanced concurrent adjustments must be 100.000")

        # Kardex reconciliation
        movs = list(MovimientoStock.objects.filter(insumo=insumo).order_by("id"))
        self.assertEqual(len(movs), 8, "Expected exactly 8 audit kardex movements")

        for idx in range(1, len(movs)):
            self.assertEqual(
                movs[idx].stock_anterior,
                movs[idx - 1].stock_nuevo,
                f"Kardex broken between movement {idx - 1} and {idx}!"
            )

    def test_adv_stock_06_dashboard_quiebre_filter_and_kpi_consistency(self):
        """
        Adversarial Test 10: Verify /inventario/ dashboard KPIs and filters under extreme stock conditions:
        - Insumo in Quiebre Crítico (<= 0)
        - Insumo Bajo Mínimo (0 < stock < stock_minimo)
        - Insumo Normal (stock >= stock_minimo)
        Verifies filter query params (?estado=quiebre, ?estado=bajo, ?estado=normal, ?q=...)
        and total inventory valuation calculation.
        """
        c = Client()
        i_quiebre = Insumo.objects.create(codigo="INS-Q1", nombre="Carne Congelada Quiebre", stock_actual=Decimal("-5.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("5000.000"))
        i_bajo = Insumo.objects.create(codigo="INS-B1", nombre="Queso Bajo Minimo", stock_actual=Decimal("3.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("4000.000"))
        i_normal = Insumo.objects.create(codigo="INS-N1", nombre="Pan Fono Normal", stock_actual=Decimal("50.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("200.000"))

        # 1. Full dashboard request
        resp = c.get("/inventario/")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # Verify KPI badges and counts in context and HTML
        self.assertIn("Total Insumos", html)
        self.assertIn("Quiebre Crítico", html)
        self.assertIn("Bajo Stock Mínimo", html)
        self.assertIn("Stock Normal", html)
        self.assertEqual(resp.context["total_insumos"], 3)
        self.assertEqual(resp.context["quiebre_count"], 1)
        self.assertEqual(resp.context["bajo_minimo_count"], 1)
        self.assertEqual(resp.context["normal_count"], 1)
        # Valuation: only positive stock items: 3 * 4000 + 50 * 200 = 12000 + 10000 = 22000
        self.assertEqual(resp.context["valor_total_inventario"], Decimal("22000.000"))

        # 2. Filter by ?estado=quiebre
        resp_q = c.get("/inventario/?estado=quiebre")
        self.assertEqual(resp_q.status_code, 200)
        filtered_ids_q = [i.id for i in resp_q.context["insumos"]]
        self.assertEqual(filtered_ids_q, [i_quiebre.id])

        # 3. Filter by ?estado=bajo
        resp_b = c.get("/inventario/?estado=bajo")
        self.assertEqual(resp_b.status_code, 200)
        filtered_ids_b = [i.id for i in resp_b.context["insumos"]]
        self.assertEqual(filtered_ids_b, [i_bajo.id])

        # 4. Filter by ?estado=normal
        resp_n = c.get("/inventario/?estado=normal")
        self.assertEqual(resp_n.status_code, 200)
        filtered_ids_n = [i.id for i in resp_n.context["insumos"]]
        self.assertEqual(filtered_ids_n, [i_normal.id])

        # 5. Search query ?q=Carne
        resp_search = c.get("/inventario/?q=Carne")
        self.assertEqual(resp_search.status_code, 200)
        filtered_ids_search = [i.id for i in resp_search.context["insumos"]]
        self.assertEqual(filtered_ids_search, [i_quiebre.id])

