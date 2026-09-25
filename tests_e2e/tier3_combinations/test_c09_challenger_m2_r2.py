"""
Tier 3 — Cross-Feature Combination C09: Empirical Challenger Suite for Milestone 2 Iteration 2.
Adversarially stress-tests:
1. Model __str__ robustness under complete absence of persistence (unsaved instances, None attributes, unassigned FKs, corrupted/partial FK objects, edge datetimes, extreme decimals).
2. confirmar_orden view guardrail against 'Eliminada' orders under various HTTP content types, payloads, concurrency, and lifecycle transitions.
3. Verification of stock immutability when confirmation of eliminated orders is attempted.
"""

import concurrent.futures
import datetime
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.utils import timezone

from Menu.models import Insumo, Menu, MovimientoStock, Orden, OrdenItem, Plato, RecetaItem
from Menu.services.inventory_service import descontar_stock_orden
from tests_e2e.base import E2EBaseMixin


class TestC09EmpiricalChallengerM2R2(TestCase, E2EBaseMixin):
    """Empirical challenge tests targeting M2.R2 guards and robustness."""

    def setUp(self):
        MovimientoStock.objects.all().delete()
        OrdenItem.objects.all().delete()
        Orden.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Menu.objects.all().delete()
        Insumo.objects.all().delete()
        self.client = Client()

    # =========================================================================
    # 1. EMPIRICAL STRESS TESTS: MovimientoStock.__str__ Robustness
    # =========================================================================

    def test_str_movimiento_stock_empty_and_none_attributes(self):
        """Verify MovimientoStock.__str__ never throws when instantiated with no or None attributes."""
        # Scenario 1: Default empty instance
        m1 = MovimientoStock()
        s1 = str(m1)
        self.assertIsInstance(s1, str)
        self.assertIn("Sin fecha", s1)
        self.assertIn("#Nuevo", s1)
        self.assertIn("Sin insumo", s1)

        # Scenario 2: Explicitly None for all fields
        m2 = MovimientoStock(
            id=None,
            insumo=None,
            tipo=None,
            cantidad=None,
            stock_anterior=None,
            stock_nuevo=None,
            orden=None,
            fecha_hora=None,
            notas=None,
        )
        s2 = str(m2)
        self.assertIsInstance(s2, str)
        self.assertIn("Sin fecha", s2)
        self.assertIn("#Nuevo", s2)
        self.assertIn("Sin insumo", s2)

    def test_str_movimiento_stock_partial_and_unpersisted_fk(self):
        """Verify MovimientoStock.__str__ with unsaved Insumo instances containing missing/empty attributes."""
        # Unsaved empty Insumo
        ins_empty = Insumo()
        m1 = MovimientoStock(insumo=ins_empty, cantidad=Decimal("3.500"))
        s1 = str(m1)
        self.assertIsInstance(s1, str)
        self.assertIn("Sin fecha", s1)
        self.assertIn("#Nuevo", s1)

        # Insumo with None nombre and None unidad_medida
        ins_none = Insumo(nombre=None, unidad_medida=None)
        m2 = MovimientoStock(insumo=ins_none, tipo="AJUSTE_MANUAL", cantidad=Decimal("0.000"))
        s2 = str(m2)
        self.assertIsInstance(s2, str)
        self.assertIn("AJUSTE_MANUAL", s2)

        # Insumo with extreme name and unicode
        ins_unicode = Insumo(nombre="Ñandú y Ají @#! 🌶️", unidad_medida="kg")
        m3 = MovimientoStock(insumo=ins_unicode, tipo="CONSUMO_ORDEN", cantidad=Decimal("12.345"))
        s3 = str(m3)
        self.assertIn("Ñandú y Ají @#! 🌶️", s3)
        self.assertIn("12.345 kg", s3)

    def test_str_movimiento_stock_dates_and_quantities_matrix(self):
        """Verify MovimientoStock.__str__ across diverse datetime formats and decimal quantities."""
        ins = Insumo(codigo="INS-MATRIX", nombre="Matrix Insumo", unidad_medida="lt")

        test_cases = [
            (None, None, "Sin fecha", "0"),
            (timezone.now(), Decimal("-5.123"), timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M'), "-5.123 lt"),
            (datetime.datetime(2020, 1, 1, 12, 0, 0), Decimal("999999.999"), "2020-01-01 12:00", "999999.999 lt"),
            (datetime.datetime(2099, 12, 31, 23, 59, 0, tzinfo=datetime.timezone.utc), Decimal("0"), "2099-12-31", "0 lt"),
        ]

        for dt, qty, expected_date_substr, expected_qty_substr in test_cases:
            m = MovimientoStock(insumo=ins, fecha_hora=dt, cantidad=qty, tipo="INGRESO_COMPRA")
            s = str(m)
            self.assertIn(expected_date_substr, s)
            self.assertIn(expected_qty_substr, s)

    def test_str_movimiento_stock_high_throughput_loop(self):
        """Stress-test: 1000 unsaved MovimientoStock str conversions in rapid succession."""
        for i in range(1000):
            m = MovimientoStock(
                tipo="CONSUMO_ORDEN" if i % 2 == 0 else None,
                cantidad=Decimal(str(i)),
                fecha_hora=timezone.now() if i % 3 == 0 else None
            )
            s = str(m)
            self.assertTrue(len(s) > 0)

    # =========================================================================
    # 2. EMPIRICAL STRESS TESTS: RecetaItem.__str__ Robustness
    # =========================================================================

    def test_str_receta_item_empty_and_none_attributes(self):
        """Verify RecetaItem.__str__ never throws on empty or None attributes."""
        # Scenario 1: Empty instance
        r1 = RecetaItem()
        s1 = str(r1)
        self.assertIsInstance(s1, str)
        self.assertIn("Plato no especificado", s1)
        self.assertIn("Insumo no especificado", s1)
        self.assertEqual(s1, "Plato no especificado -> 0 de Insumo no especificado")

        # Scenario 2: Explicit None for all fields
        r2 = RecetaItem(id=None, plato=None, insumo=None, cantidad=None)
        s2 = str(r2)
        self.assertIsInstance(s2, str)
        self.assertEqual(s2, "Plato no especificado -> 0 de Insumo no especificado")

    def test_str_receta_item_partial_and_unpersisted_fk(self):
        """Verify RecetaItem.__str__ with partial, unsaved, or broken related models."""
        p_empty = Plato()
        ins_empty = Insumo()
        r1 = RecetaItem(plato=p_empty, insumo=ins_empty, cantidad=Decimal("0.500"))
        s1 = str(r1)
        self.assertIsInstance(s1, str)
        self.assertIn("0.500", s1)

        p = Plato(nombre="Completo Especial")
        r2 = RecetaItem(plato=p, cantidad=Decimal("1.250"))
        s2 = str(r2)
        self.assertIn("Completo Especial", s2)
        self.assertIn("1.250 de Insumo no especificado", s2)

        ins = Insumo(nombre="Tomate Picado", unidad_medida="kg")
        r3 = RecetaItem(insumo=ins, cantidad=Decimal("0.300"))
        s3 = str(r3)
        self.assertIn("Plato no especificado", s3)
        self.assertIn("0.300 kg de Tomate Picado", s3)

    def test_str_receta_item_high_throughput_loop(self):
        """Stress-test: 1000 unsaved RecetaItem str conversions in rapid succession."""
        for i in range(1000):
            r = RecetaItem(
                plato=Plato(nombre=f"Plato {i}") if i % 2 == 0 else None,
                insumo=Insumo(nombre=f"Insumo {i}", unidad_medida="g") if i % 3 == 0 else None,
                cantidad=Decimal(str(i * 0.1)) if i % 5 != 0 else None
            )
            s = str(r)
            self.assertTrue(len(s) > 0)

    # =========================================================================
    # 3. EMPIRICAL STRESS TESTS: confirmar_orden 'Eliminada' Guardrail
    # =========================================================================

    def test_confirm_eliminada_order_json_payload_returns_400(self):
        """POST with JSON body to confirmed eliminated order must return HTTP 400 and reject."""
        orden = Orden.objects.create(
            cliente="Cliente Test Eliminada",
            canal_venta="Local",
            estado=Orden.ESTADO_ELIMINADA
        )
        resp = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"tipo_pago": "Efectivo", "descuento": 10}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Eliminada", data["error"])
        self.assertIn("Eliminada", data["message"])

        # DB State must be pristine and unchanged
        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_ELIMINADA)
        self.assertFalse(orden.stock_descontado)
        self.assertIsNone(orden.fecha_completada)

    def test_confirm_eliminada_order_form_data_payload_returns_400(self):
        """POST with standard form data (application/x-www-form-urlencoded) must return HTTP 400 and reject."""
        orden = Orden.objects.create(
            cliente="Cliente Form Post",
            canal_venta="Local",
            estado=Orden.ESTADO_ELIMINADA,
            descuento=0.0,
            tipo_pago="No especificado"
        )
        resp = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data={"tipo_pago": "Tarjeta", "descuento": "50"}
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Eliminada", data["error"])

        # Ensure parameters in POST body were NOT applied to DB
        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_ELIMINADA)
        self.assertEqual(orden.descuento, 0.0)
        self.assertEqual(orden.tipo_pago, "No especificado")
        self.assertFalse(orden.stock_descontado)

    def test_confirm_eliminada_order_empty_body_returns_400(self):
        """POST with empty body must return HTTP 400 and reject."""
        orden = Orden.objects.create(
            cliente="Cliente Empty Post",
            canal_venta="Local",
            estado=Orden.ESTADO_ELIMINADA
        )
        resp = self.client.post(f"/pedidos/{orden.id}/confirmar/", data="", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Eliminada", data["error"])

        # Also test empty form POST (data={})
        resp_form = self.client.post(f"/pedidos/{orden.id}/confirmar/", data={})
        self.assertEqual(resp_form.status_code, 400)
        data_f = resp_form.json()
        self.assertFalse(data_f["success"])
        self.assertIn("Eliminada", data_f["error"])

    def test_confirm_eliminada_order_malformed_json_body_returns_400(self):
        """POST with malformed JSON body must still return HTTP 400 with the Eliminada guard message."""
        orden = Orden.objects.create(
            cliente="Cliente Malformed JSON",
            canal_venta="Local",
            estado=Orden.ESTADO_ELIMINADA
        )
        resp = self.client.post(
            f"/pedidos/{orden.id}/confirmar/",
            data="{bad_json: true,",
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Eliminada", data["error"])

    def test_confirm_eliminada_order_stock_never_deducted(self):
        """Adversarial check: Stock must NEVER be decremented if confirmation is attempted on an eliminated order."""
        ins1 = Insumo.objects.create(
            codigo="INS-SEC-01",
            nombre="Carne Mechada",
            unidad_medida="kg",
            stock_actual=Decimal("50.000"),
            stock_minimo=Decimal("5.000")
        )
        ins2 = Insumo.objects.create(
            codigo="INS-SEC-02",
            nombre="Pan Frica",
            unidad_medida="un",
            stock_actual=Decimal("100.000"),
            stock_minimo=Decimal("20.000")
        )
        plato = Plato.objects.create(nombre="Sandwich Mechada", valor=5500)
        RecetaItem.objects.create(plato=plato, insumo=ins1, cantidad=Decimal("0.250"))
        RecetaItem.objects.create(plato=plato, insumo=ins2, cantidad=Decimal("1.000"))

        orden = Orden.objects.create(
            cliente="Cliente Mechada",
            canal_venta="Local",
            estado=Orden.ESTADO_ELIMINADA
        )
        OrdenItem.objects.create(orden=orden, plato=plato, cantidad=10)

        # Attempt confirmation
        resp = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 400)

        # Audit stock: must remain exactly identical
        ins1.refresh_from_db()
        ins2.refresh_from_db()
        self.assertEqual(ins1.stock_actual, Decimal("50.000"))
        self.assertEqual(ins2.stock_actual, Decimal("100.000"))

        # Kardex: 0 movements should exist for this order
        self.assertEqual(MovimientoStock.objects.filter(orden=orden).count(), 0)

    def test_lifecycle_eliminar_then_attempt_confirmar(self):
        """Operational lifecycle: Create order -> Delete via API -> Attempt to Confirm via API."""
        p = Plato.objects.create(nombre="Chacarero", valor=6500)
        orden = Orden.objects.create(cliente="Mesa 5", canal_venta="Local", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        # 1. Eliminar orden via POST /pedidos/<id>/eliminar/
        resp_del = self.client.post(f"/pedidos/{orden.id}/eliminar/")
        self.assertEqual(resp_del.status_code, 200)
        data_del = resp_del.json()
        self.assertTrue(data_del["success"])

        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_ELIMINADA)

        # 2. Intentar confirmar orden via POST /pedidos/<id>/confirmar/
        resp_conf = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp_conf.status_code, 400)
        data_conf = resp_conf.json()
        self.assertFalse(data_conf["success"])
        self.assertIn("Eliminada", data_conf["error"])

        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_ELIMINADA)
        self.assertFalse(orden.stock_descontado)

    def test_confirm_eliminada_order_rapid_fire_attempts(self):
        """Rapid-fire assault: 10 rapid requests attempting to confirm an eliminated order."""
        ins = Insumo.objects.create(codigo="INS-RAPID", nombre="Insumo Rapid", stock_actual=Decimal("100.000"))
        p = Plato.objects.create(nombre="Plato Rapid", valor=4000)
        RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("1.000"))

        orden = Orden.objects.create(cliente="Cliente Rapid", canal_venta="Local", estado=Orden.ESTADO_ELIMINADA)
        OrdenItem.objects.create(orden=orden, plato=p, cantidad=5)

        for _ in range(10):
            resp = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
            self.assertEqual(resp.status_code, 400)
            data = resp.json()
            self.assertFalse(data["success"])
            self.assertIn("Eliminada", data["error"])

        # DB State invariant: strictly untouched
        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_ELIMINADA)
        self.assertFalse(orden.stock_descontado)
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("100.000"))
        self.assertEqual(MovimientoStock.objects.filter(orden=orden).count(), 0)

    def test_confirm_completed_order_idempotency_prevents_double_deduction(self):
        """Idempotency check: Confirming an already completed order does not double-deduct stock."""
        ins = Insumo.objects.create(codigo="INS-IDEMP", nombre="Insumo Idempotente", stock_actual=Decimal("20.000"))
        p = Plato.objects.create(nombre="Plato Idemp", valor=3000)
        RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("2.000"))

        orden = Orden.objects.create(cliente="Cliente Idemp", canal_venta="Local", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)

        # 1st confirmation
        r1 = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({"tipo_pago": "Efectivo"}), content_type="application/json")
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        self.assertTrue(d1["success"])
        self.assertTrue(d1["stock_descontado"])
        self.assertEqual(d1["movimientos"], 1)

        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("18.000"))
        self.assertEqual(MovimientoStock.objects.filter(orden=orden).count(), 1)

        # 2nd confirmation (should be idempotent regarding stock)
        r2 = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({"tipo_pago": "Tarjeta"}), content_type="application/json")
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        self.assertTrue(d2["success"])
        self.assertTrue(d2["stock_descontado"])
        self.assertEqual(d2["movimientos"], 0)

        # Stock remains at 18.000, not 16.000
        ins.refresh_from_db()
        self.assertEqual(ins.stock_actual, Decimal("18.000"))
        self.assertEqual(MovimientoStock.objects.filter(orden=orden).count(), 1)

    def test_confirm_nonexistent_and_method_not_allowed(self):
        """Verify proper HTTP status codes for 404 and 405 on confirmar_orden."""
        # GET is 405
        r_get = self.client.get("/pedidos/1/confirmar/")
        self.assertEqual(r_get.status_code, 405)

        # Nonexistent is 404
        r_404 = self.client.post("/pedidos/999999/confirmar/")
        self.assertEqual(r_404.status_code, 404)

