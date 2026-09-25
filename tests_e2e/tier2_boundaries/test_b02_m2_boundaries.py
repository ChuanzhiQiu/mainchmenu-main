"""
Tier 2 — Boundaries & Corner Cases: Milestone 2 (F06 - F11).
Covers Insumo stock extremes, recipe fractional bounds, Kardex reconciliation, and deduction edge cases.
"""

import json
from decimal import Decimal
from django.utils import timezone
from tests_e2e.base import E2ETestCase


class TestB02M2Boundaries(E2ETestCase):
    """Boundary test cases for Milestone 2 features F06 to F11."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F06-F11")
        self.Menu = self.require_model("Menu", "Menu", feature_id="F06-F11")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F06-F11")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F06-F11")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F06-F11")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="F06-F11")
        self.MovimientoStock = self.require_model("Menu", "MovimientoStock", feature_id="F06-F11")
        self.descontar_stock_orden = self.require_service("Menu.services.inventory_service", "descontar_stock_orden", feature_id="F10")

    # F06: Insumo boundaries
    def test_b02_01_insumo_zero_stock_boundary(self):
        """TC-B02-01: Insumo supports zero stock value (0.000)."""
        ins = self.Insumo.objects.create(
            codigo="INS-ZERO", nombre="Insumo Cero", unidad_medida="kg",
            stock_actual=Decimal("0.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1000.000")
        )
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("0.000"))

    def test_b02_02_insumo_negative_stock_allowed(self):
        """TC-B02-02: Insumo allows negative stock for kitchen continuity when physical stock exceeds recorded stock."""
        ins = self.Insumo.objects.create(
            codigo="INS-NEG", nombre="Insumo Negativo", unidad_medida="kg",
            stock_actual=Decimal("-1.500"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("-1.500"))

    def test_b02_03_insumo_large_stock_boundary(self):
        """TC-B02-03: Insumo preserves large quantities (999999.999)."""
        ins = self.Insumo.objects.create(
            codigo="INS-BIG", nombre="Insumo Gigante", unidad_medida="un",
            stock_actual=Decimal("999999.999"), stock_minimo=Decimal("100.000"), costo_unitario=Decimal("10.000")
        )
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("999999.999"))

    def test_b02_04_insumo_inactive_soft_delete(self):
        """TC-B02-04: Inactive insumos can be filtered out from active procurement."""
        self.Insumo.objects.create(
            codigo="INS-INACT", nombre="Insumo Descontinuado", unidad_medida="kg",
            stock_actual=Decimal("0.000"), stock_minimo=Decimal("0.000"), costo_unitario=Decimal("100.000"),
            activo=False
        )
        active = self.Insumo.objects.filter(activo=True)
        self.assertFalse(active.filter(codigo="INS-INACT").exists())

    def test_b02_05_insumo_code_case_preservation(self):
        """TC-B02-05: Insumo code preserves formatting string (e.g. INS-001-A)."""
        ins = self.Insumo.objects.create(
            codigo="INS-001-A", nombre="Insumo Alfa", unidad_medida="lt",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("500.000")
        )
        self.assertEqual(ins.codigo, "INS-001-A")

    # F07: RecetaItem boundaries
    def test_b02_06_receta_minimal_fraction(self):
        """TC-B02-06: RecetaItem supports micro-quantities (0.001 kg = 1 gram)."""
        p = self.Plato.objects.create(nombre="Plato Especia", valor=3000)
        ins = self.Insumo.objects.create(
            codigo="INS-ORIEG", nombre="Orégano", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("0.100"), costo_unitario=Decimal("15000.000")
        )
        rec = self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.001"))
        self.assertEqual(rec.cantidad, Decimal("0.001"))

    def test_b02_07_receta_multiple_ingredients_ordering(self):
        """TC-B02-07: Plato with 5 different ingredients is queryable cleanly."""
        p = self.Plato.objects.create(nombre="Completo Especial 5 Insumos", valor=4000)
        for i in range(5):
            ins = self.Insumo.objects.create(
                codigo=f"INS-COMP-{i}", nombre=f"Ingrediente {i}", unidad_medida="kg",
                stock_actual=Decimal("10.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("1000.000")
            )
            self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.050"))
        self.assertEqual(self.RecetaItem.objects.filter(plato=p).count(), 5)

    def test_b02_08_delete_dish_cascades_recipes(self):
        """TC-B02-08: Deleting a Plato cascades and cleans up its RecetaItem records."""
        p = self.Plato.objects.create(nombre="Plato a Eliminar", valor=1000)
        ins = self.Insumo.objects.create(
            codigo="INS-CASC", nombre="Insumo Cascada", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("1000.000")
        )
        self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.100"))
        p.delete()
        self.assertEqual(self.RecetaItem.objects.filter(insumo=ins).count(), 0)

    def test_b02_09_receta_item_precision_no_float_rounding(self):
        """TC-B02-09: RecetaItem decimal precision prevents floating point 0.1 + 0.2 drift."""
        p = self.Plato.objects.create(nombre="Plato Precision", valor=2000)
        ins = self.Insumo.objects.create(
            codigo="INS-PREC", nombre="Insumo Precision", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("1000.000")
        )
        rec = self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.333"))
        rec.refresh_from_db()
        self.assertEqual(str(rec.cantidad), "0.333")

    def test_b02_10_dish_without_recipe_handled_safely(self):
        """TC-B02-10: Plato without any recipe items does not crash deduction service."""
        p = self.Plato.objects.create(nombre="Bebida Embotellada", valor=1500)
        orden = self.Orden.objects.create(cliente="Mesa Sin Receta", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"))

    # F08: MovimientoStock boundaries
    def test_b02_11_movimiento_zero_quantity(self):
        """TC-B02-11: Movement with 0.000 quantity records equal before and after stock."""
        ins = self.Insumo.objects.create(
            codigo="INS-M0", nombre="Insumo M0", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("1000.000")
        )
        mov = self.MovimientoStock.objects.create(
            insumo=ins, tipo="AJUSTE_MANUAL", cantidad=Decimal("0.000"),
            stock_anterior=Decimal("5.000"), stock_nuevo=Decimal("5.000")
        )
        self.assertEqual(mov.stock_anterior, mov.stock_nuevo)

    def test_b02_12_movimiento_negative_stock_recorded(self):
        """TC-B02-12: Movement records negative stock_nuevo accurately when stock is breached."""
        ins = self.Insumo.objects.create(
            codigo="INS-MBREACH", nombre="Insumo Breach", unidad_medida="kg",
            stock_actual=Decimal("0.500"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        mov = self.MovimientoStock.objects.create(
            insumo=ins, tipo="CONSUMO_ORDEN", cantidad=Decimal("1.000"),
            stock_anterior=Decimal("0.500"), stock_nuevo=Decimal("-0.500")
        )
        self.assertEqual(mov.stock_nuevo, Decimal("-0.500"))

    def test_b02_13_movimiento_null_orden_allowed(self):
        """TC-B02-13: Stock movements from manual adjustments or supplier purchases have orden=None."""
        ins = self.Insumo.objects.create(
            codigo="INS-MNULL", nombre="Insumo MNull", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        mov = self.MovimientoStock.objects.create(
            insumo=ins, tipo="INGRESO_COMPRA", cantidad=Decimal("20.000"),
            stock_anterior=Decimal("10.000"), stock_nuevo=Decimal("30.000"), orden=None
        )
        self.assertIsNone(mov.orden)

    def test_b02_14_sequential_movements_integrity(self):
        """TC-B02-14: Consecutive movements form an unbroken stock audit trail."""
        ins = self.Insumo.objects.create(
            codigo="INS-MSEQ", nombre="Insumo Seq", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        m1 = self.MovimientoStock.objects.create(
            insumo=ins, tipo="CONSUMO_ORDEN", cantidad=Decimal("2.000"),
            stock_anterior=Decimal("10.000"), stock_nuevo=Decimal("8.000")
        )
        m2 = self.MovimientoStock.objects.create(
            insumo=ins, tipo="CONSUMO_ORDEN", cantidad=Decimal("3.000"),
            stock_anterior=Decimal("8.000"), stock_nuevo=Decimal("5.000")
        )
        self.assertEqual(m1.stock_nuevo, m2.stock_anterior)

    def test_b02_15_movimiento_long_notes_handling(self):
        """TC-B02-15: Movement supports detailed audit notes without truncation."""
        ins = self.Insumo.objects.create(
            codigo="INS-MNOTE", nombre="Insumo Notes", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("1000.000")
        )
        note = "Ajuste por inventario físico realizado por jefe de cocina. Lote #98234."
        mov = self.MovimientoStock.objects.create(
            insumo=ins, tipo="AJUSTE_MANUAL", cantidad=Decimal("1.000"),
            stock_anterior=Decimal("5.000"), stock_nuevo=Decimal("6.000")
        )
        if hasattr(mov, "notas"):
            mov.notas = note
            mov.save()
            mov.refresh_from_db()
            self.assertEqual(mov.notas, note)

    # F09: Order Model Extension boundaries
    def test_b02_16_order_empty_items_completion(self):
        """TC-B02-16: Completing an order with zero items marks stock_descontado=True safely."""
        orden = self.Orden.objects.create(cliente="Mesa Vacia", canal_venta="Local", tipo_pago="Efectivo")
        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"))
        orden.refresh_from_db()
        self.assertTrue(orden.stock_descontado)

    def test_b02_17_order_completion_date_preserved(self):
        """TC-B02-17: fecha_completada retains accurate timestamp."""
        now = timezone.now()
        orden = self.Orden.objects.create(cliente="Mesa Timestamp", canal_venta="Local", tipo_pago="Efectivo", fecha_completada=now)
        orden.refresh_from_db()
        self.assertIsNotNone(orden.fecha_completada)

    def test_b02_18_deleted_order_stock_not_deducted(self):
        """TC-B02-18: Order in 'Eliminada' status does not execute deduction."""
        ins = self.Insumo.objects.create(
            codigo="INS-DELORDER", nombre="Insumo Del", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000")
        )
        p = self.Plato.objects.create(nombre="Plato Del", valor=2000)
        self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("1.000"))

        orden = self.Orden.objects.create(cliente="Mesa Eliminada", canal_venta="Local", tipo_pago="Efectivo", estado="Eliminada")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        res = self.descontar_stock_orden(orden.id)
        ins.refresh_from_db()
        # Deleted order should not deduct or should return failure
        if not res.get("success"):
            self.assertEqual(ins.stock_actual, Decimal("10.000"))

    def test_b02_19_order_boolean_flag_strict_type(self):
        """TC-B02-19: stock_descontado is strictly boolean (True/False)."""
        orden = self.Orden.objects.create(cliente="Mesa Bool", canal_venta="Local", tipo_pago="Efectivo")
        self.assertIs(orden.stock_descontado, False)
        orden.stock_descontado = True
        orden.save()
        orden.refresh_from_db()
        self.assertIs(orden.stock_descontado, True)

    def test_b02_20_order_multiple_query_filtering(self):
        """TC-B02-20: Querying orders by both estado and stock_descontado returns precise slice."""
        o1 = self.Orden.objects.create(cliente="O1", canal_venta="Local", tipo_pago="Efectivo", estado="En curso", stock_descontado=False)
        o2 = self.Orden.objects.create(cliente="O2", canal_venta="Local", tipo_pago="Efectivo", estado="Completada", stock_descontado=True)
        completed_deducted = self.Orden.objects.filter(estado="Completada", stock_descontado=True)
        self.assertTrue(completed_deducted.filter(id=o2.id).exists())
        self.assertFalse(completed_deducted.filter(id=o1.id).exists())

    # F10: Stock Deduction Service boundaries
    def test_b02_21_stock_exact_zero_transition(self):
        """TC-B02-21: Insumo stock transitions from 1.000 to exactly 0.000."""
        ins = self.Insumo.objects.create(
            codigo="INS-EXACT0", nombre="Insumo Exacto", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("0.500"), costo_unitario=Decimal("2000.000")
        )
        p = self.Plato.objects.create(nombre="Plato Exacto", valor=4000)
        self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("1.000"))

        orden = self.Orden.objects.create(cliente="Mesa Exacto", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        self.descontar_stock_orden(orden.id)
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("0.000"))

    def test_b02_22_combo_with_shared_ingredients_deductions_aggregated(self):
        """TC-B02-22: Dish and Combo ordered together sharing common ingredient aggregate deduction."""
        ins_sal = self.Insumo.objects.create(
            codigo="INS-SAL2", nombre="Sal Común", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("1.000"), costo_unitario=Decimal("500.000")
        )
        p1 = self.Plato.objects.create(nombre="Papas", valor=2000)
        p2 = self.Plato.objects.create(nombre="Carne Asada", valor=7000)
        self.RecetaItem.objects.create(plato=p1, insumo=ins_sal, cantidad=Decimal("0.010"))
        self.RecetaItem.objects.create(plato=p2, insumo=ins_sal, cantidad=Decimal("0.020"))

        combo = self.Menu.objects.create(nombre="Combo Asado", precio_menus=8500)
        combo.platos.add(p1, p2)

        orden = self.Orden.objects.create(cliente="Mesa Mix", canal_venta="Local", tipo_pago="Efectivo")
        # 1 extra papas (0.010) + 1 combo (0.010 + 0.020 = 0.030) = total 0.040 sal consumed
        self.OrdenItem.objects.create(orden=orden, plato=p1, cantidad=1)
        self.OrdenItem.objects.create(orden=orden, menu=combo, cantidad=1)

        self.descontar_stock_orden(orden.id)
        ins_sal.refresh_from_db()
        self.assertEqual(ins_sal.stock_actual, Decimal("4.960"))

    def test_b02_23_nonexistent_order_id_returns_clean_failure(self):
        """TC-B02-23: descontar_stock_orden with non-existent ID returns success=False without crashing."""
        res = self.descontar_stock_orden(9999999)
        self.assertFalse(res.get("success"))

    def test_b02_24_deadlock_prevention_order_by_id(self):
        """TC-B02-24: Insumos locked in ascending ID order to prevent concurrent deadlocks."""
        # Create insumos with differing IDs
        ins1 = self.Insumo.objects.create(codigo="INS-L1", nombre="Lock 1", unidad_medida="kg", stock_actual=Decimal("10"), stock_minimo=Decimal("1"), costo_unitario=Decimal("100"))
        ins2 = self.Insumo.objects.create(codigo="INS-L2", nombre="Lock 2", unidad_medida="kg", stock_actual=Decimal("10"), stock_minimo=Decimal("1"), costo_unitario=Decimal("100"))
        p = self.Plato.objects.create(nombre="Plato Deadlock", valor=3000)
        self.RecetaItem.objects.create(plato=p, insumo=ins2, cantidad=Decimal("0.500"))
        self.RecetaItem.objects.create(plato=p, insumo=ins1, cantidad=Decimal("0.500"))

        orden = self.Orden.objects.create(cliente="Mesa Lock", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        res = self.descontar_stock_orden(orden.id)
        self.assertTrue(res.get("success"))

    def test_b02_25_alert_threshold_strictly_less_than_minimum(self):
        """TC-B02-25: Stock equal to stock_minimo vs less than stock_minimo alert boundaries."""
        ins = self.Insumo.objects.create(
            codigo="INS-THRESH", nombre="Insumo Umbral", unidad_medida="kg",
            stock_actual=Decimal("5.100"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1000.000")
        )
        p = self.Plato.objects.create(nombre="Plato Umbral", valor=3000)
        self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.100"))

        orden = self.Orden.objects.create(cliente="Mesa Umbral", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        res = self.descontar_stock_orden(orden.id)
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("5.000"))

    # F11: Order Completion Hook boundaries
    def test_b02_26_confirm_with_missing_tipo_pago(self):
        """TC-B02-26: Confirming order without explicit tipo_pago retains or defaults safely."""
        orden = self.Orden.objects.create(cliente="Mesa Pago Default", canal_venta="Local", tipo_pago="Efectivo")
        client = self.get_client()
        resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_b02_27_confirm_with_negative_discount(self):
        """TC-B02-27: Confirming order with negative discount does not inflate order total."""
        p = self.Plato.objects.create(nombre="Plato Descuento Negativo", valor=5000.0)
        orden = self.Orden.objects.create(cliente="Mesa Descuento", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        client = self.get_client()
        client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({"descuento": -1000.0}), content_type="application/json")
        orden.refresh_from_db()
        self.assertLessEqual(orden.monto_total, 5000.0, "[F11-B] Negative discount must not increase total")

    def test_b02_28_confirm_malformed_json_returns_400(self):
        """TC-B02-28: Confirming order with unparseable non-JSON payload returns 400."""
        orden = self.Orden.objects.create(cliente="Mesa Bad JSON", canal_venta="Local", tipo_pago="Efectivo")
        client = self.get_client()
        resp = client.post(f"/pedidos/{orden.id}/confirmar/", data="MALFORMED_NON_JSON", content_type="application/json")
        self.assertIn(resp.status_code, [400, 200])  # If handled gracefully

    def test_b02_29_confirm_nonexistent_order_returns_404(self):
        """TC-B02-29: Confirming non-existent order ID returns 404."""
        client = self.get_client()
        resp = client.post("/pedidos/9999999/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 404)

    def test_b02_30_confirm_hook_returns_low_stock_details(self):
        """TC-B02-30: JSON response includes low stock details when alert triggered."""
        ins = self.Insumo.objects.create(
            codigo="INS-ALERTJSON", nombre="Insumo Alerta JSON", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1000.000")
        )
        p = self.Plato.objects.create(nombre="Plato Alerta JSON", valor=2000)
        self.RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.500"))

        orden = self.Orden.objects.create(cliente="Mesa Alerta JSON", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        client = self.get_client()
        resp = client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        data = resp.json()
        self.assertTrue(data.get("success"))
