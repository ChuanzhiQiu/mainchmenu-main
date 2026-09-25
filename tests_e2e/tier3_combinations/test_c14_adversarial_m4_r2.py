"""
Adversarial Stress Test Suite for Milestone 4 (challenger_m4_r2_2).
Targets:
1. Combo recursion depth and edge-case combos (combos containing combos, empty dishes, dishes without recipes).
2. Negative kitchen stock and lead time zero edge cases (extreme negative stock, micro-precision, boundary conditions).
3. API error responses (extreme malformed querystrings, invalid integer parameters, non-existent endpoints, PATCH/HEAD).
4. CLI command execution (generar_orden_compra with --dias 0, --format json, --no-llm, --json, --output file).
"""

from decimal import Decimal
import io
import json
import os
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from pydantic import ValidationError

from Menu.models import Insumo, Plato, Menu, Orden, OrdenItem, RecetaItem
from src.ai_forecast.fallback import calcular_reorden_heuristico
from src.ai_forecast.forecaster import (
    calcular_consumo_diario_insumos,
    generar_sugerencias_compra,
)
from src.ai_forecast.schemas import InsumoSugerido
from tests_e2e.base import E2ETestCase


class TestAdversarialComboEdgeCases(E2ETestCase):
    """Challenge Dimension 1: Combo recursion, empty combos, dishes without recipes, and item anomalies."""

    def test_adv_combo_empty_dishes_returns_empty_consumption(self):
        """A completed order with a combo menu containing zero dishes produces empty consumption without crashing."""
        empty_menu = Menu.objects.create(nombre="Combo Sin Platos", precio_menus=5000.0)
        orden = Orden.objects.create(
            cliente="Mesa Vacia",
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=10000.0,
            fecha_completada=timezone.now(),
        )
        OrdenItem.objects.create(orden=orden, menu=empty_menu, cantidad=2)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        self.assertEqual(consumo, {}, "Empty combo menu should produce empty consumption dict")

    def test_adv_combo_mixed_with_dishes_without_recipes(self):
        """A combo containing dishes with recipes and dishes without recipes aggregates only valid recipes without phantom insumos."""
        ins_pan = Insumo.objects.create(
            codigo="ADV2-PAN", nombre="Pan Artesanal", unidad_medida="un",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("30.000"), costo_unitario=Decimal("300.000")
        )
        ins_pate = Insumo.objects.create(
            codigo="ADV2-PATE", nombre="Pate Casero", unidad_medida="kg",
            stock_actual=Decimal("2.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("4500.000")
        )

        # Plato 1: with recipe (uses 1 pan + 0.1 kg pate)
        plato_tostada = Plato.objects.create(nombre="Tostada Con Pate", valor=3500.0)
        RecetaItem.objects.create(plato=plato_tostada, insumo=ins_pan, cantidad=Decimal("1.000"))
        RecetaItem.objects.create(plato=plato_tostada, insumo=ins_pate, cantidad=Decimal("0.100"))

        # Plato 2: without recipe (packaged bottled tea)
        plato_te = Plato.objects.create(nombre="Te Verde Botella", valor=1800.0)

        # Plato 3: with recipe (uses 1 pan only)
        plato_pan_solo = Plato.objects.create(nombre="Pan Extra", valor=800.0)
        RecetaItem.objects.create(plato=plato_pan_solo, insumo=ins_pan, cantidad=Decimal("1.000"))

        # Combo containing all 3 dishes
        combo_desayuno = Menu.objects.create(nombre="Desayuno Completo", precio_menus=5500.0)
        combo_desayuno.platos.add(plato_tostada, plato_te, plato_pan_solo)

        orden = Orden.objects.create(
            cliente="Mesa Desayuno",
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=16500.0,
            fecha_completada=timezone.now(),
        )
        # 3 combos ordered -> 3 tostadas + 3 tes + 3 panes extra
        # Pan total: 3 * 1.0 (from tostada) + 3 * 1.0 (from pan extra) = 6.0 un
        # Pate total: 3 * 0.1 = 0.3 kg
        OrdenItem.objects.create(orden=orden, menu=combo_desayuno, cantidad=3)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        self.assertEqual(consumo.get(ins_pan.id), 6.0)
        self.assertEqual(consumo.get(ins_pate.id), 0.3)
        self.assertEqual(len(consumo), 2, "Dishes without recipes must not introduce unexpected insumo IDs")

    def test_adv_combo_multiple_shared_recipes_and_direct_sales(self):
        """Overlapping ingredients across multiple combos and direct dishes on separate dates calculate correct averages."""
        ins_queso = Insumo.objects.create(
            codigo="ADV2-QUESO", nombre="Queso Gauda", unidad_medida="kg",
            stock_actual=Decimal("4.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("6000.000")
        )
        plato_pizza = Plato.objects.create(nombre="Pizza Individual", valor=6000.0)
        RecetaItem.objects.create(plato=plato_pizza, insumo=ins_queso, cantidad=Decimal("0.200"))

        plato_empanada = Plato.objects.create(nombre="Empanada Queso", valor=2000.0)
        RecetaItem.objects.create(plato=plato_empanada, insumo=ins_queso, cantidad=Decimal("0.080"))

        combo_italiano = Menu.objects.create(nombre="Combo Italiano", precio_menus=7500.0)
        combo_italiano.platos.add(plato_pizza)

        combo_chileno = Menu.objects.create(nombre="Combo Chileno", precio_menus=5000.0)
        combo_chileno.platos.add(plato_empanada)

        # Day 1: Order with 5 pizza combos (5 * 0.200 = 1.000 kg)
        ord1 = Orden.objects.create(cliente="C1", estado=Orden.ESTADO_COMPLETADA, fecha_completada=timezone.now())
        OrdenItem.objects.create(orden=ord1, menu=combo_italiano, cantidad=5)

        # Day 1: Direct order of 10 empanadas (10 * 0.080 = 0.800 kg)
        ord2 = Orden.objects.create(cliente="C2", estado=Orden.ESTADO_COMPLETADA, fecha_completada=timezone.now())
        OrdenItem.objects.create(orden=ord2, plato=plato_empanada, cantidad=10)

        # Total Queso = 1.000 + 0.800 = 1.800 kg. Num dates = 1 -> daily consumption = 1.800 kg
        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        self.assertEqual(consumo.get(ins_queso.id), 1.8)

    def test_adv_orden_item_both_plato_and_menu_none(self):
        """Corrupted OrdenItem with plato=None and menu=None is ignored gracefully without throwing exceptions."""
        orden = Orden.objects.create(
            cliente="Corrupted Item Test",
            estado=Orden.ESTADO_COMPLETADA,
            fecha_completada=timezone.now()
        )
        OrdenItem.objects.create(orden=orden, plato=None, menu=None, cantidad=5)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        self.assertEqual(consumo, {}, "Order with null plato and null menu items should not crash")

    def test_adv_orden_item_cantidad_zero_truthiness_documentation(self):
        """Empirical verification: `cant = item.cantidad or 1` in forecaster.py evaluates `0 or 1 -> 1`."""
        ins = Insumo.objects.create(
            codigo="ADV2-TRUTH", nombre="Truthiness Test Insumo", unidad_medida="kg",
            stock_actual=Decimal("10.0"), stock_minimo=Decimal("5.0"), costo_unitario=Decimal("1000.0")
        )
        plato = Plato.objects.create(nombre="Plato Zero", valor=1000.0)
        RecetaItem.objects.create(plato=plato, insumo=ins, cantidad=Decimal("0.500"))

        orden = Orden.objects.create(
            cliente="Zero Qty Client",
            estado=Orden.ESTADO_COMPLETADA,
            fecha_completada=timezone.now()
        )
        # Create an item with cantidad=0
        OrdenItem.objects.create(orden=orden, plato=plato, cantidad=0)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        # In Python: 0 or 1 -> 1. 1 * 0.500 = 0.500 kg
        self.assertEqual(consumo.get(ins.id), 0.5)

    def test_adv_combo_with_inactive_insumo_in_recipe(self):
        """A combo whose recipe contains an inactive insumo aggregates consumption, but ROP excludes it from purchase pool."""
        ins_inactivo = Insumo.objects.create(
            codigo="ADV2-DISC", nombre="Insumo Descontinuado", unidad_medida="kg",
            stock_actual=Decimal("0.0"), stock_minimo=Decimal("10.0"), costo_unitario=Decimal("500.0"),
            activo=False
        )
        plato = Plato.objects.create(nombre="Plato Antiguo", valor=2000.0)
        RecetaItem.objects.create(plato=plato, insumo=ins_inactivo, cantidad=Decimal("1.000"))

        combo = Menu.objects.create(nombre="Combo Antiguo", precio_menus=1800.0)
        combo.platos.add(plato)

        orden = Orden.objects.create(cliente="Old", estado=Orden.ESTADO_COMPLETADA, fecha_completada=timezone.now())
        OrdenItem.objects.create(orden=orden, menu=combo, cantidad=4)

        # Historical consumption tracks the physical event
        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        self.assertEqual(consumo.get(ins_inactivo.id), 4.0)

        # But heuristic ROP without explicit insumo list excludes inactive insumos
        res_rop = calcular_reorden_heuristico(insumos=None, dias_lead_time=2)
        codigos_sugeridos = [it.codigo for it in res_rop.items_sugeridos]
        self.assertNotIn("ADV2-DISC", codigos_sugeridos)


class TestAdversarialNegativeStockAndLeadTimeZero(E2ETestCase):
    """Challenge Dimension 2: Extreme negative kitchen stock, lead time zero, and micro-precision."""

    def test_adv_rop_extreme_negative_kitchen_stock_lead_time_zero(self):
        """Extreme negative stock (-1,000,000) with lead time 0 suggests exactly 1,000,000 to recover deficit."""
        insumo = Insumo.objects.create(
            codigo="ADV2-EXTREME-NEG",
            nombre="Harina Mega Negativa",
            unidad_medida="kg",
            stock_actual=Decimal("-1000000.000"),
            stock_minimo=Decimal("0.000"),
            costo_unitario=Decimal("50.000"),
        )
        consumos = {insumo.id: 100.0}
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=0,
            consumos_diarios=consumos,
        )
        self.assertEqual(len(res.items_sugeridos), 1)
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 1000000.0)
        self.assertEqual(item.costo_subtotal, 50000000.0)
        self.assertEqual(res.presupuesto_estimado_total, 50000000.0)

    def test_adv_rop_negative_stock_with_lead_time_zero_and_positive_safety_stock(self):
        """Negative kitchen stock (-35.25) with lead time 0 and safety stock 15 covers both negative debt and buffer."""
        insumo = Insumo.objects.create(
            codigo="ADV2-NEG-WITH-SM",
            nombre="Aceite Fraccionado Negativo",
            unidad_medida="lt",
            stock_actual=Decimal("-35.250"),
            stock_minimo=Decimal("15.000"),
            costo_unitario=Decimal("2000.000"),
        )
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=0,
            consumos_diarios={insumo.id: 10.0},
        )
        item = res.items_sugeridos[0]
        # Deficit = (0 * 10 + 15) - (-35.25) = 15 + 35.25 = 50.25
        self.assertEqual(item.cantidad_sugerida, 50.25)
        self.assertEqual(item.costo_subtotal, 100500.0)

    def test_adv_rop_zero_stock_zero_lead_time_zero_min_stock_zero_demand(self):
        """All zero operational inputs produce 0.0 suggested quantity and 0.0 budget cleanly."""
        insumo = Insumo.objects.create(
            codigo="ADV2-ALL-ZERO",
            nombre="Zero Operational",
            unidad_medida="un",
            stock_actual=Decimal("0.000"),
            stock_minimo=Decimal("0.000"),
            costo_unitario=Decimal("500.000"),
        )
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=0,
            consumos_diarios={insumo.id: 0.0},
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 0.0)
        self.assertEqual(item.costo_subtotal, 0.0)
        self.assertEqual(res.presupuesto_estimado_total, 0.0)

    def test_adv_rop_micro_negative_stock_precision(self):
        """Micro negative stock (-0.001) preserves milligram accuracy in purchase recommendation."""
        insumo = Insumo.objects.create(
            codigo="ADV2-MICRO-NEG",
            nombre="Sal Fina Micro",
            unidad_medida="kg",
            stock_actual=Decimal("-0.001"),
            stock_minimo=Decimal("0.000"),
            costo_unitario=Decimal("600.000"),
        )
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=0,
            consumos_diarios={insumo.id: 0.0},
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 0.001)

    def test_adv_rop_negative_stock_minimo_validation_boundary(self):
        """Pydantic schema InsumoSugerido enforces stock_minimo >= 0.0 via ValidationError."""
        insumo = Insumo(
            id=8888,
            codigo="ADV2-NEG-SM",
            nombre="Bad Safety Stock",
            unidad_medida="un",
            stock_actual=Decimal("10.0"),
            stock_minimo=Decimal("-5.0"),
            costo_unitario=Decimal("100.0"),
        )
        with self.assertRaises(ValidationError):
            calcular_reorden_heuristico(insumos=[insumo], dias_lead_time=2)


class TestAdversarialAPIErrorResponses(E2ETestCase):
    """Challenge Dimension 3: Malformed querystrings, invalid integer parameters, non-existent endpoints, and HTTP methods."""

    def setUp(self):
        self.client = Client()
        self.endpoint = "/api/sugerencias-compra/"
        Insumo.objects.create(
            codigo="ADV2-API-INS",
            nombre="Insumo Para API Test",
            unidad_medida="un",
            stock_actual=Decimal("1.0"),
            stock_minimo=Decimal("5.0"),
            costo_unitario=Decimal("1000.0"),
        )

    def test_adv_api_extreme_malformed_dias_querystrings(self):
        """Hostile and malformed querystrings must return HTTP 200 with sanitized default periodo_dias=7."""
        hostile_queries = [
            "?dias=NaN",
            "?dias=Infinity",
            "?dias=-Infinity",
            "?dias=1e10",
            "?dias=3.14159",
            "?dias=-0",
            "?dias=%00",
            "?dias=%20%20",
            "?dias=undefined",
            "?dias=null",
            "?dias=[1,2]",
            "?dias={\"a\":1}",
            "?dias=--1",
            "?dias=0x10",
        ]
        for query in hostile_queries:
            resp = self.client.get(f"{self.endpoint}{query}")
            self.assertEqual(resp.status_code, 200, f"Query '{query}' failed with status {resp.status_code}")
            data = resp.json()
            self.assertEqual(data.get("periodo_dias"), 7, f"Query '{query}' did not sanitize to 7 days")
            self.assertIn("items_sugeridos", data)
            self.assertIn("presupuesto_estimado_total", data)

    def test_adv_api_usar_llm_parameter_variations(self):
        """Query parameter 'usar_llm' accepts various truthy/falsy conventions without failure."""
        falsy_values = ["0", "false", "False", "no", "off"]
        for fv in falsy_values:
            resp = self.client.get(f"{self.endpoint}?usar_llm={fv}")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("metodo"), "HEURISTIC_FALLBACK")

        truthy_values = ["1", "true", "True", "yes", "on", "random"]
        for tv in truthy_values:
            resp = self.client.get(f"{self.endpoint}?usar_llm={tv}")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            # In sandbox without API key, should gracefully degrade to fallback
            self.assertEqual(data.get("metodo"), "HEURISTIC_FALLBACK")

    def test_adv_api_unsupported_http_methods(self):
        """PATCH and HEAD methods must be rejected with HTTP 405 Method Not Allowed."""
        for method in ["patch", "head"]:
            caller = getattr(self.client, method)
            resp = caller(self.endpoint)
            self.assertEqual(resp.status_code, 405, f"{method.upper()} must return HTTP 405")

    def test_adv_api_nonexistent_endpoints(self):
        """Non-existent sub-endpoints under /api/ must return standard HTTP 404."""
        nonexistent = [
            "/api/sugerencias-compra/extra/",
            "/api/sugerencias/",
            "/api/does-not-exist/",
            "/inventario/sugerencias-ia/subpath/",
        ]
        for url in nonexistent:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 404, f"Endpoint {url} must return HTTP 404")

    def test_adv_api_unexpected_query_parameters_ignored(self):
        """Unknown or malicious query parameters are safely ignored and do not trigger HTTP 500."""
        resp = self.client.get(f"{self.endpoint}?sql=SELECT%20*%20FROM%20Menu_insumo&xss=<script>alert(1)</script>")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items_sugeridos", data)


class TestAdversarialCLICommandExecution(E2ETestCase):
    """Challenge Dimension 4: CLI command generar_orden_compra execution under various argument combinations."""

    def setUp(self):
        Insumo.objects.all().delete()
        self.ins_carne = Insumo.objects.create(
            codigo="CLI-CARNE", nombre="Carne Vacuno", unidad_medida="kg",
            stock_actual=Decimal("2.0"), stock_minimo=Decimal("10.0"), costo_unitario=Decimal("8000.0")
        )
        self.ins_papas = Insumo.objects.create(
            codigo="CLI-PAPAS", nombre="Papas Granel", unidad_medida="kg",
            stock_actual=Decimal("5.0"), stock_minimo=Decimal("20.0"), costo_unitario=Decimal("1000.0")
        )

    def test_adv_cli_generar_orden_compra_dias_zero_no_llm_json(self):
        """Command executed with --dias 0 --no-llm --format json sanitizes to 7 days and outputs valid JSON."""
        out = io.StringIO()
        err = io.StringIO()
        call_command("generar_orden_compra", dias=0, no_llm=True, format="json", stdout=out, stderr=err)

        output_str = out.getvalue()
        data = json.loads(output_str)
        self.assertEqual(data["periodo_dias"], 7, "Dias 0 should be normalized to 7 days")
        self.assertEqual(data["metodo"], "HEURISTIC_FALLBACK")
        self.assertGreater(len(data["items_sugeridos"]), 0)
        self.assertGreater(data["presupuesto_estimado_total"], 0.0)

    def test_adv_cli_generar_orden_compra_table_format_output(self):
        """Command executed with --format table outputs ANSI formatted text with column headers."""
        out = io.StringIO()
        err = io.StringIO()
        call_command("generar_orden_compra", no_llm=True, format="table", stdout=out, stderr=err)

        output_str = out.getvalue()
        self.assertIn("Sugerencia de Orden de Compra", output_str)
        self.assertIn("SKU", output_str)
        self.assertIn("Insumo", output_str)
        self.assertIn("Sugerido", output_str)
        self.assertIn("Costo Subtotal", output_str)
        self.assertIn("CLI-CARNE", output_str)
        self.assertIn("CLI-PAPAS", output_str)

    def test_adv_cli_generar_orden_compra_json_boolean_flag(self):
        """Command executed with --json shorthand flag outputs raw JSON directly to stdout."""
        out = io.StringIO()
        err = io.StringIO()
        call_command("generar_orden_compra", no_llm=True, json=True, stdout=out, stderr=err)

        output_str = out.getvalue()
        data = json.loads(output_str)
        self.assertIsInstance(data, dict)
        self.assertIn("items_sugeridos", data)
        self.assertIn("presupuesto_estimado_total", data)

    def test_adv_cli_generar_orden_compra_file_export(self):
        """Command executed with --output <filepath> exports valid JSON report to filesystem."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_path = tf.name

        try:
            out = io.StringIO()
            err = io.StringIO()
            call_command("generar_orden_compra", no_llm=True, output=temp_path, stdout=out, stderr=err)

            self.assertTrue(os.path.exists(temp_path))
            with open(temp_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertIn("items_sugeridos", data)
            self.assertEqual(data["metodo"], "HEURISTIC_FALLBACK")
            self.assertIn("Reporte exportado exitosamente", err.getvalue())
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_adv_cli_generar_orden_compra_negative_dias(self):
        """Command executed with --dias -10 clamps/normalizes to 7 days."""
        out = io.StringIO()
        err = io.StringIO()
        call_command("generar_orden_compra", dias=-10, no_llm=True, format="json", stdout=out, stderr=err)

        data = json.loads(out.getvalue())
        self.assertEqual(data["periodo_dias"], 7)

    def test_adv_cli_generar_orden_compra_no_items_needed(self):
        """When no active insumos require purchase (or database has no active insumos), table format handles clean exit."""
        Insumo.objects.all().delete()

        out = io.StringIO()
        err = io.StringIO()
        call_command("generar_orden_compra", no_llm=True, format="table", stdout=out, stderr=err)

        output_str = out.getvalue()
        self.assertIn("No se requieren compras urgentes para el periodo.", output_str)

    def test_adv_cli_generar_orden_compra_llm_graceful_fallback(self):
        """Command executed without --no-llm gracefully catches missing API key and falls back cleanly."""
        out = io.StringIO()
        err = io.StringIO()
        call_command("generar_orden_compra", format="json", stdout=out, stderr=err)

        data = json.loads(out.getvalue())
        self.assertEqual(data["metodo"], "HEURISTIC_FALLBACK")
        self.assertIn("items_sugeridos", data)
