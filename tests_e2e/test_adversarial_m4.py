"""
Adversarial Stress Test Suite for Milestone 4 (challenger_m4_2).
Targets:
1. Heuristic ROP under extreme parameters (zero lead time, zero safety stock, extreme lead time, negative stock, zero sales, inactive insumos, fractional precision).
2. Combo explosion into individual dishes and raw materials (shared ingredients across combos, dishes without recipes).
3. LLM failure recovery (mock ConnectionError, corrupted JSON, markdown fence wrapping, empty responses, non-dict arrays, subtotal reconciliation).
4. API endpoints (GET, OPTIONS, invalid query parameters, unsupported HTTP methods, total double-outage survival).
"""

from decimal import Decimal
import json
from unittest.mock import patch
from django.utils import timezone
from django.test import Client
from tests_e2e.base import E2ETestCase

from Menu.models import Insumo, Plato, Menu, Orden, OrdenItem, RecetaItem
from src.ai_forecast.fallback import (
    calcular_reorden_heuristico,
    calcular_consumo_diario_historico,
)
from src.ai_forecast.forecaster import (
    generar_sugerencias_compra,
    calcular_consumo_diario_insumos,
    _extraer_json_limpio,
)
from src.ai_forecast.schemas import (
    SugerenciaOrdenCompra,
    InsumoSugerido,
    parse_and_validate_forecast_json,
)


class TestAdversarialHeuristicROP(E2ETestCase):
    """Dimension 1: Extreme parameters and boundary conditions on Deterministic ROP."""

    def test_adv_rop_zero_lead_time(self):
        """Zero lead time: Demanda_Lead_Time = 0. ROP equals Stock_Minimo."""
        insumo = Insumo.objects.create(
            codigo="ADV-LT0",
            nombre="Insumo Zero LT",
            unidad_medida="kg",
            stock_actual=Decimal("2.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("1000.000"),
        )
        consumos = {insumo.id: 10.0}
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=0,
            consumos_diarios=consumos,
        )
        self.assertEqual(len(res.items_sugeridos), 1)
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 3.0)
        self.assertEqual(item.costo_subtotal, 3000.0)

    def test_adv_rop_zero_safety_stock(self):
        """Zero safety stock: stock_minimo = 0. ROP equals Demanda_Lead_Time."""
        insumo = Insumo.objects.create(
            codigo="ADV-SM0",
            nombre="Insumo Zero Safety",
            unidad_medida="lt",
            stock_actual=Decimal("4.000"),
            stock_minimo=Decimal("0.000"),
            costo_unitario=Decimal("500.000"),
        )
        consumos = {insumo.id: 3.0}
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=2,
            consumos_diarios=consumos,
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 2.0)
        self.assertEqual(item.costo_subtotal, 1000.0)

    def test_adv_rop_both_lead_time_and_safety_stock_zero(self):
        """Both lead_time=0 and stock_minimo=0: ROP = 0. Replenish only if negative stock."""
        insumo_pos = Insumo.objects.create(
            codigo="ADV-LT0SM0-POS",
            nombre="Zero Both Pos Stock",
            unidad_medida="un",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("0.000"),
            costo_unitario=Decimal("200.000"),
        )
        insumo_neg = Insumo.objects.create(
            codigo="ADV-LT0SM0-NEG",
            nombre="Zero Both Neg Stock",
            unidad_medida="un",
            stock_actual=Decimal("-5.000"),
            stock_minimo=Decimal("0.000"),
            costo_unitario=Decimal("200.000"),
        )
        consumos = {insumo_pos.id: 5.0, insumo_neg.id: 5.0}
        res = calcular_reorden_heuristico(
            insumos=[insumo_pos, insumo_neg],
            dias_lead_time=0,
            consumos_diarios=consumos,
        )
        items_by_code = {it.codigo: it for it in res.items_sugeridos}
        self.assertEqual(items_by_code["ADV-LT0SM0-POS"].cantidad_sugerida, 0.0)
        self.assertEqual(items_by_code["ADV-LT0SM0-NEG"].cantidad_sugerida, 5.0)

    def test_adv_rop_extreme_lead_time(self):
        """Extreme supplier lead time (60 days): ensures formula handles large horizons without overflow."""
        insumo = Insumo.objects.create(
            codigo="ADV-LT60",
            nombre="Imported Truffle",
            unidad_medida="kg",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("50000.000"),
        )
        consumos = {insumo.id: 2.0}
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=60,
            dias_proyeccion=60,
            consumos_diarios=consumos,
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 115.0)
        self.assertEqual(item.costo_subtotal, 115.0 * 50000.0)
        self.assertEqual(res.presupuesto_estimado_total, 5750000.0)

    def test_adv_rop_negative_kitchen_stock(self):
        """Negative kitchen stock (-15.5) covers physical deficit plus ROP buffer."""
        insumo = Insumo.objects.create(
            codigo="ADV-NEGSTOCK",
            nombre="Harina Desfasada",
            unidad_medida="kg",
            stock_actual=Decimal("-15.500"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("800.000"),
        )
        consumos = {insumo.id: 4.0}
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=3,
            consumos_diarios=consumos,
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 37.5)
        self.assertEqual(item.costo_subtotal, 37.5 * 800.0)

    def test_adv_rop_zero_historical_sales_no_zerodivision(self):
        """Zero historical sales in database: no orders exist, ensures zero division safety."""
        Orden.objects.all().delete()
        insumo = Insumo.objects.create(
            codigo="ADV-NOSALES",
            nombre="Insumo Sin Ventas",
            unidad_medida="un",
            stock_actual=Decimal("1.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("1500.000"),
        )
        consumos_forecaster = calcular_consumo_diario_insumos(dias_historia=30)
        consumos_fallback = calcular_consumo_diario_historico([insumo.id], dias_historial=30)
        self.assertEqual(consumos_forecaster.get(insumo.id, 0.0), 0.0)
        self.assertEqual(consumos_fallback.get(insumo.id, 0.0), 0.0)

        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=2,
            consumos_diarios=consumos_fallback,
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 9.0)

    def test_adv_rop_negative_and_zero_operational_inputs(self):
        """Defensive clamping for negative lead_time and negative projection days."""
        insumo = Insumo.objects.create(
            codigo="ADV-CLAMP",
            nombre="Clamping Test",
            unidad_medida="kg",
            stock_actual=Decimal("2.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("1000.000"),
        )
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=-5,
            dias_proyeccion=-10,
            consumos_diarios={insumo.id: 2.0},
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 3.0)
        self.assertGreaterEqual(res.periodo_dias, 1)

    def test_adv_rop_inactive_insumo_exclusion(self):
        """Inactive insumos (activo=False) must not be included when resolving default active pool."""
        Insumo.objects.all().delete()
        Insumo.objects.create(
            codigo="ADV-INACT", nombre="Insumo Inactivo", unidad_medida="kg",
            stock_actual=Decimal("0.0"), stock_minimo=Decimal("10.0"), costo_unitario=Decimal("100.0"),
            activo=False
        )
        active = Insumo.objects.create(
            codigo="ADV-ACT", nombre="Insumo Activo", unidad_medida="kg",
            stock_actual=Decimal("0.0"), stock_minimo=Decimal("10.0"), costo_unitario=Decimal("100.0"),
            activo=True
        )
        res = calcular_reorden_heuristico(insumos=None, dias_lead_time=1)
        codigos = [it.codigo for it in res.items_sugeridos]
        self.assertIn("ADV-ACT", codigos)
        self.assertNotIn("ADV-INACT", codigos)

    def test_adv_rop_fractional_precision_rounding(self):
        """Fractional quantities and unit costs with multiple decimal places round accurately."""
        insumo = Insumo.objects.create(
            codigo="ADV-FRACT", nombre="Azafran Granel", unidad_medida="g",
            stock_actual=Decimal("0.333"), stock_minimo=Decimal("1.250"), costo_unitario=Decimal("4567.890")
        )
        # Consumo = 0.555, lead_time = 2 -> Demanda = 1.110 -> ROP = 1.110 + 1.250 = 2.360
        # Deficit = 2.360 - 0.333 = 2.027
        res = calcular_reorden_heuristico(
            insumos=[insumo],
            dias_lead_time=2,
            consumos_diarios={insumo.id: 0.555}
        )
        item = res.items_sugeridos[0]
        self.assertEqual(item.cantidad_sugerida, 2.027)
        expected_cost = round(2.027 * 4567.89, 2)
        self.assertEqual(item.costo_subtotal, expected_cost)
        self.assertEqual(res.presupuesto_estimado_total, expected_cost)


class TestAdversarialComboExplosion(E2ETestCase):
    """Dimension 2: Combo Menu explosion into dishes and raw materials."""

    def setUp(self):
        self.ins_carne = Insumo.objects.create(
            codigo="ADV-CARNE", nombre="Carne Hamburguesa", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("20.000"), costo_unitario=Decimal("8000.000")
        )
        self.ins_pan = Insumo.objects.create(
            codigo="ADV-PAN", nombre="Pan Brioche", unidad_medida="un",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("50.000"), costo_unitario=Decimal("400.000")
        )
        self.ins_papa = Insumo.objects.create(
            codigo="ADV-PAPA", nombre="Papas Fritas", unidad_medida="kg",
            stock_actual=Decimal("8.000"), stock_minimo=Decimal("30.000"), costo_unitario=Decimal("1200.000")
        )
        self.ins_bebida = Insumo.objects.create(
            codigo="ADV-BEBIDA", nombre="Lata Refresco", unidad_medida="un",
            stock_actual=Decimal("15.000"), stock_minimo=Decimal("40.000"), costo_unitario=Decimal("600.000")
        )

        # Platos
        self.plato_burger = Plato.objects.create(nombre="Hamburguesa Doble", valor=6500.0)
        RecetaItem.objects.create(plato=self.plato_burger, insumo=self.ins_carne, cantidad=Decimal("0.250"))
        RecetaItem.objects.create(plato=self.plato_burger, insumo=self.ins_pan, cantidad=Decimal("1.000"))

        self.plato_papas = Plato.objects.create(nombre="Papas Rusticas", valor=2500.0)
        RecetaItem.objects.create(plato=self.plato_papas, insumo=self.ins_papa, cantidad=Decimal("0.300"))

        self.plato_bebida = Plato.objects.create(nombre="Bebida Individual", valor=1500.0)
        RecetaItem.objects.create(plato=self.plato_bebida, insumo=self.ins_bebida, cantidad=Decimal("1.000"))

        # Dish without recipe (e.g. coffee or packaged item)
        self.plato_no_receta = Plato.objects.create(nombre="Agua Mineral Envasada", valor=1000.0)

        # Combo Menu: Hamburguesa + Papas + Bebida
        self.combo = Menu.objects.create(nombre="Combo Hamburguesa Completa", precio_menus=8990.0)
        self.combo.platos.add(self.plato_burger, self.plato_papas, self.plato_bebida)

    def test_adv_combo_explosion_accuracy(self):
        """Combo accurately explodes into 3 constituent dishes and their 4 raw materials."""
        orden = Orden.objects.create(
            cliente="Mesa 1",
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=35960.0,
            fecha_completada=timezone.now(),
        )
        OrdenItem.objects.create(orden=orden, menu=self.combo, cantidad=4)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)

        self.assertEqual(consumo.get(self.ins_carne.id), 1.0)
        self.assertEqual(consumo.get(self.ins_pan.id), 4.0)
        self.assertEqual(consumo.get(self.ins_papa.id), 1.2)
        self.assertEqual(consumo.get(self.ins_bebida.id), 4.0)

    def test_adv_mixed_direct_and_combo_orders_aggregation(self):
        """Simultaneous direct dish and combo sales aggregate additively without cross-contamination."""
        orden1 = Orden.objects.create(
            cliente="Mesa 2",
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=17980.0,
            fecha_completada=timezone.now(),
        )
        OrdenItem.objects.create(orden=orden1, menu=self.combo, cantidad=2)

        orden2 = Orden.objects.create(
            cliente="Mesa 3",
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=13000.0,
            fecha_completada=timezone.now(),
        )
        OrdenItem.objects.create(orden=orden2, plato=self.plato_burger, cantidad=2)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)

        self.assertEqual(consumo.get(self.ins_carne.id), 1.0)
        self.assertEqual(consumo.get(self.ins_pan.id), 4.0)
        self.assertEqual(consumo.get(self.ins_papa.id), 0.6)
        self.assertEqual(consumo.get(self.ins_bebida.id), 2.0)

    def test_adv_non_completed_orders_excluded(self):
        """Orders with 'En curso' or 'Eliminada' status must NOT contribute to consumption."""
        orden_en_curso = Orden.objects.create(
            cliente="Mesa 4",
            estado=Orden.ESTADO_EN_CURSO,
            monto_total=50000.0,
        )
        OrdenItem.objects.create(orden=orden_en_curso, menu=self.combo, cantidad=10)

        orden_eliminada = Orden.objects.create(
            cliente="Mesa 5",
            estado=Orden.ESTADO_ELIMINADA,
            monto_total=50000.0,
        )
        OrdenItem.objects.create(orden=orden_eliminada, plato=self.plato_burger, cantidad=10)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        self.assertEqual(consumo, {}, "Uncompleted and deleted orders should produce 0 consumption")

    def test_adv_dish_without_recipe_does_not_crash(self):
        """Dish without any RecetaItem linked is handled gracefully during consumption aggregation."""
        orden = Orden.objects.create(
            cliente="Mesa 6",
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=2000.0,
            fecha_completada=timezone.now(),
        )
        OrdenItem.objects.create(orden=orden, plato=self.plato_no_receta, cantidad=2)
        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        # Should not crash, and should not add phantom insumos
        self.assertNotIn(self.ins_carne.id, consumo)

    def test_adv_multi_dish_shared_insumo_across_combos(self):
        """Two different combos sharing the same ingredient calculate combined demand correctly."""
        # Create second dish using carne
        plato_albondigas = Plato.objects.create(nombre="Albondigas Caseras", valor=5000.0)
        RecetaItem.objects.create(plato=plato_albondigas, insumo=self.ins_carne, cantidad=Decimal("0.150"))

        combo2 = Menu.objects.create(nombre="Combo Tapas", precio_menus=7000.0)
        combo2.platos.add(plato_albondigas, self.plato_papas)

        orden = Orden.objects.create(
            cliente="Mesa 7", estado=Orden.ESTADO_COMPLETADA,
            monto_total=25000.0, fecha_completada=timezone.now()
        )
        # 2 of combo 1 (2 * 0.250 = 0.500 kg carne)
        OrdenItem.objects.create(orden=orden, menu=self.combo, cantidad=2)
        # 4 of combo 2 (4 * 0.150 = 0.600 kg carne)
        OrdenItem.objects.create(orden=orden, menu=combo2, cantidad=4)

        consumo = calcular_consumo_diario_insumos(dias_historia=1)
        # Expected carne: 0.500 + 0.600 = 1.100 kg
        self.assertEqual(consumo.get(self.ins_carne.id), 1.1)


class TestAdversarialLLMRecovery(E2ETestCase):
    """Dimension 3: Robustness and graceful degradation under LLM network and format failure modes."""

    def setUp(self):
        self.insumo = Insumo.objects.create(
            codigo="ADV-LLM-INS",
            nombre="Queso Parmesano",
            unidad_medida="kg",
            stock_actual=Decimal("2.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("15000.000"),
        )

    def test_adv_llm_connection_error_fallback(self):
        """Simulate low-level network failure (ConnectionError): triggers HEURISTIC_FALLBACK."""
        with patch("src.ai_forecast.forecaster.llm_call", side_effect=ConnectionError("Failed to connect to API gateway")):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(res)
        self.assertIn("HEURISTIC", res.metodo.upper())
        self.assertGreater(len(res.items_sugeridos), 0)
        self.assertGreater(res.presupuesto_estimado_total, 0)

    def test_adv_llm_corrupted_json_syntax_error(self):
        """Corrupted/truncated JSON returned by LLM: triggers HEURISTIC_FALLBACK."""
        corrupted = '{"items_sugeridos": [{"insumo_id": 1, "codigo": "ADV-LLM-INS", "cantidad_sugerida": 8.0, '
        with patch("src.ai_forecast.forecaster.llm_call", return_value=corrupted):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(res)
        self.assertIn("HEURISTIC", res.metodo.upper())

    def test_adv_llm_markdown_fence_cleaning(self):
        """Markdown code block wrapping (```json ... ```) with conversational preamble is cleaned correctly."""
        valid_payload = json.dumps({
            "items_sugeridos": [
                {
                    "insumo_id": self.insumo.id,
                    "codigo": self.insumo.codigo,
                    "nombre": self.insumo.nombre,
                    "unidad_medida": self.insumo.unidad_medida,
                    "stock_actual": float(self.insumo.stock_actual),
                    "stock_minimo": float(self.insumo.stock_minimo),
                    "consumo_diario_estimado": 1.5,
                    "cantidad_sugerida": 12.0,
                    "costo_unitario": float(self.insumo.costo_unitario),
                    "costo_subtotal": 12.0 * float(self.insumo.costo_unitario),
                    "justificacion": "Demanda proyectada supera el stock de seguridad.",
                }
            ],
            "presupuesto_estimado_total": 180000.0,
            "periodo_dias": 7,
            "metodo": "LLM_GENERATED",
        })

        wrapped_text = f"Hola! Aquí tienes la recomendación calculada:\n\n```json\n{valid_payload}\n```\nSaludos!"
        with patch("src.ai_forecast.forecaster.llm_call", return_value=wrapped_text):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(res)
        self.assertEqual(res.metodo, "LLM_GENERATED")
        self.assertEqual(len(res.items_sugeridos), 1)
        self.assertEqual(res.items_sugeridos[0].cantidad_sugerida, 12.0)

    def test_adv_llm_empty_response(self):
        """Completely empty response or whitespace from LLM triggers HEURISTIC_FALLBACK."""
        with patch("src.ai_forecast.forecaster.llm_call", return_value="   "):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(res)
        self.assertIn("HEURISTIC", res.metodo.upper())

    def test_adv_llm_non_dict_json_array(self):
        """LLM returning a JSON list instead of an object triggers HEURISTIC_FALLBACK."""
        with patch("src.ai_forecast.forecaster.llm_call", return_value="[1, 2, 3]"):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(res)
        self.assertIn("HEURISTIC", res.metodo.upper())

    def test_adv_llm_schema_violation_negative_quantity(self):
        """LLM proposing a negative quantity triggers validation rejection and fallback."""
        bad_schema = json.dumps({
            "items_sugeridos": [
                {
                    "insumo_id": self.insumo.id,
                    "codigo": self.insumo.codigo,
                    "nombre": self.insumo.nombre,
                    "unidad_medida": self.insumo.unidad_medida,
                    "stock_actual": 2.0,
                    "stock_minimo": 10.0,
                    "consumo_diario_estimado": 1.0,
                    "cantidad_sugerida": -50.0,
                    "costo_unitario": 15000.0,
                    "costo_subtotal": -750000.0,
                    "justificacion": "Error de prueba",
                }
            ],
            "presupuesto_estimado_total": -750000.0,
            "periodo_dias": 7,
            "metodo": "LLM_GENERATED",
        })
        with patch("src.ai_forecast.forecaster.llm_call", return_value=bad_schema):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertIsNotNone(res)
        self.assertIn("HEURISTIC", res.metodo.upper())
        for it in res.items_sugeridos:
            self.assertGreaterEqual(it.cantidad_sugerida, 0.0)

    def test_adv_llm_subtotal_and_budget_reconciliation(self):
        """LLM returning inconsistent subtotal and zero total budget is reconciled by model validators."""
        payload = json.dumps({
            "items_sugeridos": [
                {
                    "insumo_id": self.insumo.id,
                    "codigo": self.insumo.codigo,
                    "nombre": self.insumo.nombre,
                    "unidad_medida": self.insumo.unidad_medida,
                    "stock_actual": 2.0,
                    "stock_minimo": 10.0,
                    "consumo_diario_estimado": 1.0,
                    "cantidad_sugerida": 5.0,
                    "costo_unitario": 1000.0,
                    "costo_subtotal": 99999.0,  # Deliberate mismatch
                    "justificacion": "Reabastecimiento regular",
                }
            ],
            "presupuesto_estimado_total": 0.0,  # Deliberate mismatch
            "periodo_dias": 7,
            "metodo": "LLM_GENERATED",
        })
        with patch("src.ai_forecast.forecaster.llm_call", return_value=payload):
            res = generar_sugerencias_compra(dias_proyeccion=7, usar_llm=True)

        self.assertEqual(res.metodo, "LLM_GENERATED")
        # Subtotal should be corrected to 5.0 * 1000.0 = 5000.0
        self.assertEqual(res.items_sugeridos[0].costo_subtotal, 5000.0)
        # Total budget reconciled to 5000.0
        self.assertEqual(res.presupuesto_estimado_total, 5000.0)


class TestAdversarialAPIEndpoints(E2ETestCase):
    """Dimension 4: AI Endpoint resilience against malformed inputs and protocol attacks."""

    def setUp(self):
        self.client = Client()
        self.api_url = "/api/sugerencias-compra/"
        self.inventario_url = "/inventario/sugerencias-ia/"
        self.insumo = Insumo.objects.create(
            codigo="ADV-API-INS",
            nombre="Aceite de Oliva Extra Virgen",
            unidad_medida="lt",
            stock_actual=Decimal("1.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("9000.000"),
        )

    def test_adv_api_options_preflight(self):
        """OPTIONS preflight request returns HTTP 200 with Allow header on both routes."""
        for url in [self.api_url, self.inventario_url]:
            resp = self.client.options(url)
            self.assertEqual(resp.status_code, 200, f"OPTIONS {url} must return 200")
            self.assertIn("Allow", resp.headers)
            self.assertIn("GET", resp.headers["Allow"])
            self.assertIn("OPTIONS", resp.headers["Allow"])

    def test_adv_api_unsupported_methods_return_405(self):
        """POST, PUT, and DELETE methods must be rejected with HTTP 405 Method Not Allowed."""
        for method in ["post", "put", "delete"]:
            caller = getattr(self.client, method)
            resp = caller(self.api_url)
            self.assertEqual(resp.status_code, 405, f"{method.upper()} must return 405")

    def test_adv_api_invalid_query_parameters(self):
        """Adversarial query parameters: ?dias=-5, ?dias=abc, ?dias=0, ?dias=huge integer."""
        adversarial_params = [
            "?dias=-5",
            "?dias=-1000",
            "?dias=0",
            "?dias=abc",
            "?dias=undefined",
            "?dias=null",
            "?dias=",
            "?dias=99999999999999999999999999999999",
        ]
        for param in adversarial_params:
            resp = self.client.get(f"{self.api_url}{param}")
            self.assertEqual(
                resp.status_code,
                200,
                f"Query param {param} caused failure with status {resp.status_code}"
            )
            data = resp.json()
            self.assertIn("items_sugeridos", data)
            self.assertIn("presupuesto_estimado_total", data)
            self.assertGreaterEqual(data["periodo_dias"], 1)

    def test_adv_api_defensive_field_aliasing(self):
        """API response must contain defensive aliases: 'nombre' and 'insumo_nombre', 'costo_subtotal' and 'costo_estimado'."""
        resp = self.client.get(self.api_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        items = data.get("items_sugeridos", [])
        self.assertTrue(len(items) > 0, "Should have suggested items")
        item = items[0]
        self.assertIn("nombre", item)
        self.assertIn("insumo_nombre", item)
        self.assertEqual(item["nombre"], item["insumo_nombre"])
        self.assertIn("costo_subtotal", item)
        self.assertIn("costo_estimado", item)
        self.assertEqual(item["costo_subtotal"], item["costo_estimado"])

    def test_adv_api_total_double_outage_resilience(self):
        """Even if forecaster AND heuristic fallback throw unexpected exceptions, API returns 200 with FALLBACK_EMPTY."""
        with patch("src.ai_forecast.forecaster.generar_sugerencias_compra", side_effect=RuntimeError("Primary crashed")):
            with patch("src.ai_forecast.fallback.calcular_reorden_heuristico", side_effect=RuntimeError("Secondary crashed")):
                resp = self.client.get(self.api_url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["metodo"], "FALLBACK_EMPTY")
        self.assertEqual(data["items_sugeridos"], [])
        self.assertEqual(data["presupuesto_estimado_total"], 0.0)
        self.assertIn("alertas", data)
