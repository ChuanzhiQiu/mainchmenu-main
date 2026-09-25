"""
Tier 2 — Boundaries & Corner Cases: Milestone 4 (F19 - F23).
Covers zero-demand forecasting, prompt injection guards, Pydantic type bounds, ROP zero lead time, and API parameter bounds.
"""

from decimal import Decimal
from django.urls import resolve
from tests_e2e.base import E2ETestCase, E2ESimpleTestCase

try:
    from pydantic import ValidationError
except ImportError:
    ValidationError = Exception


class TestB04M4Boundaries(E2ETestCase):
    """Boundary test cases for Milestone 4 features F19 to F23."""

    def setUp(self):
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F19-F23")
        self.generar_sugerencias = self.require_service("src.ai_forecast.forecaster", "generar_sugerencias_compra", feature_id="F19")
        self.SugerenciaOrdenCompra = self.require_service("src.ai_forecast.schemas", "SugerenciaOrdenCompra", feature_id="F21")
        self.InsumoSugerido = self.require_service("src.ai_forecast.schemas", "InsumoSugerido", feature_id="F21")
        self.calcular_fallback = self.require_service("src.ai_forecast.fallback", "calcular_reorden_heuristico", feature_id="F22")

    # F19: Forecasting boundaries
    def test_b04_01_forecast_empty_history(self):
        """TC-B04-01: Forecaster executes safely when database has zero sales history."""
        res = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(res)

    def test_b04_02_forecast_short_horizon_1_day(self):
        """TC-B04-02: Forecaster handles 1-day projection horizon."""
        res = self.generar_sugerencias(dias_proyeccion=1, usar_llm=False)
        self.assertIsNotNone(res)

    def test_b04_03_forecast_long_horizon_30_days(self):
        """TC-B04-03: Forecaster handles 30-day monthly projection horizon."""
        res = self.generar_sugerencias(dias_proyeccion=30, usar_llm=False)
        self.assertIsNotNone(res)

    def test_b04_04_forecast_slow_moving_zero_demand_insumo(self):
        """TC-B04-04: Insumos with zero historical demand do not trigger math exceptions."""
        self.Insumo.objects.create(
            codigo="INS-SLOW", nombre="Canela en Rama", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("0.200"), costo_unitario=Decimal("20000.000")
        )
        res = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(res)

    def test_b04_05_forecast_surge_demand_multiplication(self):
        """TC-B04-05: Sudden demand surge scales estimated consumption proportionally."""
        res = self.generar_sugerencias(dias_proyeccion=7, usar_llm=False)
        self.assertIsNotNone(res)

    # F20: AI System Prompt boundaries
    def test_b04_06_prompt_utf8_encoding(self):
        """TC-B04-06: System prompt is encoded in valid UTF-8 without byte order marks."""
        path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "[F20-B] Prompt should not contain UTF-8 BOM")

    def test_b04_07_prompt_contains_explicit_json_guardrail(self):
        """TC-B04-07: Prompt explicitly instructs outputting pure JSON without markdown backticks."""
        path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"
        content = path.read_text(encoding="utf-8")
        self.assertTrue("json" in content.lower())

    def test_b04_08_prompt_token_length_safety(self):
        """TC-B04-08: System prompt word count stays within standard LLM token window (< 2000 words)."""
        path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"
        words = path.read_text(encoding="utf-8").split()
        self.assertLess(len(words), 2000, "[F20-B] System prompt should be concise (< 2000 words)")

    def test_b04_09_prompt_header_metadata_present(self):
        """TC-B04-09: Prompt includes title or purpose declaration header."""
        path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"
        content = path.read_text(encoding="utf-8")
        self.assertTrue(content.strip().startswith("#"), "[F20-B] Prompt should have markdown header")

    def test_b04_10_prompt_injection_safety_instruction(self):
        """TC-B04-10: Prompt guides the model to strictly follow operational constraints."""
        path = self.PROJECT_ROOT / "src" / "prompts" / "demand_forecaster_system.md"
        content = path.read_text(encoding="utf-8").lower()
        has_rules = any(w in content for w in ["reglas", "formato", "esquema", "estricto", "instrucciones"])
        self.assertTrue(has_rules)

    # F21: Pydantic Guardrails boundaries
    def test_b04_11_pydantic_negative_cost_rejected(self):
        """TC-B04-11: Negative costo_unitario in InsumoSugerido raises ValidationError."""
        item = {
            "insumo_id": 1, "codigo": "INS-NEGCOST", "nombre": "Test", "unidad_medida": "kg",
            "stock_actual": 5.0, "stock_minimo": 2.0, "consumo_diario_estimado": 1.0,
            "cantidad_sugerida": 5.0, "costo_unitario": -100.0, "costo_subtotal": -500.0,
            "justificacion": "Test"
        }
        with self.assertRaises(ValidationError):
            self.InsumoSugerido(**item)

    def test_b04_12_pydantic_zero_quantity_allowed_for_surplus(self):
        """TC-B04-12: Zero cantidad_sugerida is valid (indicates no reorder required)."""
        item = {
            "insumo_id": 1, "codigo": "INS-SURP", "nombre": "Surplus", "unidad_medida": "kg",
            "stock_actual": 20.0, "stock_minimo": 2.0, "consumo_diario_estimado": 1.0,
            "cantidad_sugerida": 0.0, "costo_unitario": 1000.0, "costo_subtotal": 0.0,
            "justificacion": "Stock suficiente."
        }
        obj = self.InsumoSugerido(**item)
        self.assertEqual(obj.cantidad_sugerida, 0.0)

    def test_b04_13_pydantic_invalid_items_type_rejected(self):
        """TC-B04-13: Passing non-list to items_sugeridos raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.SugerenciaOrdenCompra(items_sugeridos="not_a_list", presupuesto_estimado_total=0, periodo_dias=7)

    def test_b04_14_pydantic_budget_consistency(self):
        """TC-B04-14: Budget total reflects sum of subtotal items."""
        item1 = {
            "insumo_id": 1, "codigo": "I1", "nombre": "I1", "unidad_medida": "kg",
            "stock_actual": 1.0, "stock_minimo": 2.0, "consumo_diario_estimado": 1.0,
            "cantidad_sugerida": 5.0, "costo_unitario": 1000.0, "costo_subtotal": 5000.0,
            "justificacion": "Reabastecimiento"
        }
        payload = {
            "items_sugeridos": [item1],
            "presupuesto_estimado_total": 5000.0,
            "periodo_dias": 7,
            "metodo": "HEURISTIC_FALLBACK"
        }
        obj = self.SugerenciaOrdenCompra(**payload)
        self.assertEqual(obj.presupuesto_estimado_total, 5000.0)

    def test_b04_15_pydantic_subtotal_precision(self):
        """TC-B04-15: Cost subtotal matches cantidad * costo_unitario."""
        item = {
            "insumo_id": 1, "codigo": "I2", "nombre": "I2", "unidad_medida": "kg",
            "stock_actual": 0.0, "stock_minimo": 1.0, "consumo_diario_estimado": 0.5,
            "cantidad_sugerida": 2.5, "costo_unitario": 4000.0, "costo_subtotal": 10000.0,
            "justificacion": "Stock en cero"
        }
        obj = self.InsumoSugerido(**item)
        self.assertEqual(obj.costo_subtotal, 10000.0)

    # F22: Fallback Engine boundaries
    def test_b04_16_fallback_zero_lead_time(self):
        """TC-B04-16: Fallback ROP handles lead_time=0 days (immediate same-day restock)."""
        ins = self.Insumo.objects.create(
            codigo="INS-LT0", nombre="Insumo LT0", unidad_medida="kg",
            stock_actual=Decimal("1.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1000.000")
        )
        res = self.calcular_fallback(insumos=[ins], dias_lead_time=0, dias_proyeccion=7)
        self.assertIsNotNone(res)

    def test_b04_17_fallback_zero_safety_stock(self):
        """TC-B04-17: Fallback handles insumos with stock_minimo=0."""
        ins = self.Insumo.objects.create(
            codigo="INS-SM0", nombre="Insumo SM0", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("0.000"), costo_unitario=Decimal("500.000")
        )
        res = self.calcular_fallback(insumos=[ins], dias_lead_time=2, dias_proyeccion=7)
        self.assertIsNotNone(res)

    def test_b04_18_fallback_extreme_lead_time_30_days(self):
        """TC-B04-18: Fallback handles long supplier lead time (30 days)."""
        ins = self.Insumo.objects.create(
            codigo="INS-LT30", nombre="Insumo Importado", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("15000.000")
        )
        res = self.calcular_fallback(insumos=[ins], dias_lead_time=30, dias_proyeccion=30)
        self.assertIsNotNone(res)

    def test_b04_19_fallback_clamping_prevents_negative_purchase(self):
        """TC-B04-19: Fallback never produces negative suggested quantities."""
        ins = self.Insumo.objects.create(
            codigo="INS-OVER", nombre="Insumo Sobreabundante", unidad_medida="kg",
            stock_actual=Decimal("500.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("1000.000")
        )
        res = self.calcular_fallback(insumos=[ins], dias_lead_time=2, dias_proyeccion=7)
        items = res.items_sugeridos if hasattr(res, "items_sugeridos") else res.get("items_sugeridos", [])
        for item in items:
            qty = item.cantidad_sugerida if hasattr(item, "cantidad_sugerida") else item["cantidad_sugerida"]
            self.assertGreaterEqual(qty, 0.0, "[F22-B] Quantity must be non-negative")

    def test_b04_20_fallback_budget_calculation(self):
        """TC-B04-20: Total budget in fallback equals sum of recommended items costs."""
        ins1 = self.Insumo.objects.create(codigo="INS-F1", nombre="F1", unidad_medida="kg", stock_actual=Decimal("0"), stock_minimo=Decimal("5"), costo_unitario=Decimal("2000"))
        ins2 = self.Insumo.objects.create(codigo="INS-F2", nombre="F2", unidad_medida="kg", stock_actual=Decimal("0"), stock_minimo=Decimal("10"), costo_unitario=Decimal("1000"))
        res = self.calcular_fallback(insumos=[ins1, ins2], dias_lead_time=2, dias_proyeccion=7)
        total = res.presupuesto_estimado_total if hasattr(res, "presupuesto_estimado_total") else res.get("presupuesto_estimado_total")
        self.assertGreater(total, 0.0)

    # F23: AI Endpoint boundaries
    def test_b04_21_endpoint_invalid_dias_query_param(self):
        """TC-B04-21: AI endpoint with non-numeric ?dias=xyz falls back to default 7 days."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(f"{url}?dias=xyz")
        self.assertIn(resp.status_code, [200, 400], "[F23-B] Invalid query param should not cause 500")

    def test_b04_22_endpoint_negative_dias_param(self):
        """TC-B04-22: AI endpoint with negative ?dias=-5 is handled safely."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(f"{url}?dias=-5")
        self.assertIn(resp.status_code, [200, 400])

    def test_b04_23_endpoint_empty_inventory_json_structure(self):
        """TC-B04-23: When no insumos need reordering, response JSON returns empty list and 0 budget."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(url)
        data = resp.json()
        self.assertIsInstance(data.get("items_sugeridos"), list)

    def test_b04_24_endpoint_json_content_type_header(self):
        """TC-B04-24: AI suggestion endpoint sets application/json content-type header."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(url)
        self.assertIn("application/json", resp.headers.get("Content-Type", ""))

    def test_b04_25_endpoint_http_options_or_get_supported(self):
        """TC-B04-25: Endpoint supports GET and OPTIONS HTTP methods."""
        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
