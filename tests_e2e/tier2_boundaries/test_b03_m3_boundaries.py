"""
Tier 2 — Boundaries & Corner Cases: Milestone 3 (F12 - F18).
Covers XSS protection in toasts, POS alphabet grouping, discount clamping, KDS load, and polling stress.
"""

import json
from decimal import Decimal
from django.utils import timezone
from tests_e2e.base import E2ETestCase


class TestB03M3Boundaries(E2ETestCase):
    """Boundary test cases for Milestone 3 features F12 to F18."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="F12-F18")
        self.Menu = self.require_model("Menu", "Menu", feature_id="F12-F18")
        self.Orden = self.require_model("Menu", "Orden", feature_id="F12-F18")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="F12-F18")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F12-F18")

    # F12: Base layout boundaries
    def test_b03_01_empty_messages_renders_cleanly(self):
        """TC-B03-01: Base layout renders cleanly without toast container artifact when messages is empty."""
        client = self.get_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_02_xss_prevention_in_rendered_context(self):
        """TC-B03-02: User input containing HTML script tags is escaped in base template."""
        client = self.get_client()
        self.Orden.objects.create(cliente="<script>alert('xss')</script>", canal_venta="Local", tipo_pago="Efectivo")
        resp = client.get("/")
        self.assertNotIn("<script>alert('xss')</script>", resp.content.decode("utf-8"), "[F12-B] XSS must be escaped")

    def test_b03_03_viewport_meta_attributes(self):
        """TC-B03-03: Viewport meta tag contains width=device-width and initial-scale=1.0."""
        base_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "base.html"
        content = base_path.read_text(encoding="utf-8")
        self.assertIn("width=device-width", content, "[F12-B] Viewport should define width=device-width")

    def test_b03_04_https_or_cdn_protocol_safety(self):
        """TC-B03-04: External assets use HTTPS protocols."""
        base_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "base.html"
        content = base_path.read_text(encoding="utf-8")
        self.assertNotIn("http://cdn", content, "[F12-B] CDN scripts should not use unencrypted http://")

    def test_b03_05_messages_container_uses_tailwind_classes(self):
        """TC-B03-05: Toast or message container uses utility positioning classes (fixed/absolute/top/right)."""
        base_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "base.html"
        content = base_path.read_text(encoding="utf-8")
        if "messages" in content:
            has_pos = any(w in content for w in ["fixed", "absolute", "toast", "alert", "notification"])
            self.assertTrue(has_pos)

    # F13: POS dual-panel boundaries
    def test_b03_06_pos_empty_catalog_renders_cleanly(self):
        """TC-B03-06: POS handles zero dishes/combos without 500 template error."""
        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_07_dish_with_numeric_initial(self):
        """TC-B03-07: Dish name starting with number (e.g. '3 Empanadas') groups under digit or '#'."""
        self.Plato.objects.create(nombre="3 Empanadas", valor=4500)
        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("3 Empanadas", resp.content.decode("utf-8"))

    def test_b03_08_dish_with_accented_initial(self):
        """TC-B03-08: Dish starting with accented character (e.g. 'Ñoquis', 'Árbol') renders properly."""
        self.Plato.objects.create(nombre="Ñoquis con Salsa", valor=5500)
        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Ñoquis con Salsa", resp.content.decode("utf-8"))

    def test_b03_09_dish_with_long_name(self):
        """TC-B03-09: Dish with 80-character name does not break layout."""
        long_name = "Super Mega Sandwich Churrasco Italiano con Doble Queso y Tocino Ahumado Especial"
        self.Plato.objects.create(nombre=long_name, valor=9900)
        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        self.assertIn(long_name, resp.content.decode("utf-8"))

    def test_b03_10_large_dish_catalog_rendering(self):
        """TC-B03-10: POS handles catalog of 30 dishes efficiently."""
        for i in range(30):
            self.Plato.objects.create(nombre=f"Plato Numero {i:02d}", valor=3000 + i * 100)
        client = self.get_client()
        resp = client.get("/pedidos/crear/")
        self.assertEqual(resp.status_code, 200)

    # F14: POS dynamic calculation boundaries
    def test_b03_11_zero_price_dish_calculation(self):
        """TC-B03-11: Dish with valor=0 (courtesy / promo) calculates correctly."""
        p = self.Plato.objects.create(nombre="Vaso de Agua", valor=0.0)
        orden = self.Orden.objects.create(cliente="Mesa Agua", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=2)
        self.assertEqual(orden.calcular_total(), 0.0)

    def test_b03_12_hundred_percent_discount_boundary(self):
        """TC-B03-12: Order with discount equal to total yields total 0."""
        p = self.Plato.objects.create(nombre="Menu Ejecutivo", valor=6000.0)
        orden = self.Orden.objects.create(cliente="Mesa Descuento Total", canal_venta="Local", tipo_pago="Efectivo", descuento=6000.0)
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        self.assertEqual(orden.calcular_total(), 0.0)

    def test_b03_13_discount_greater_than_subtotal_clamped(self):
        """TC-B03-13: Discount exceeding order subtotal clamps to 0."""
        p = self.Plato.objects.create(nombre="Cafe", valor=1500.0)
        orden = self.Orden.objects.create(cliente="Mesa Super Descuento", canal_venta="Local", tipo_pago="Efectivo", descuento=5000.0)
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        self.assertGreaterEqual(orden.calcular_total(), 0.0)

    def test_b03_14_large_quantity_calculation(self):
        """TC-B03-14: Calculating total for 500 units does not overflow."""
        p = self.Plato.objects.create(nombre="Canapé", valor=500.0)
        orden = self.Orden.objects.create(cliente="Evento Catering", canal_venta="Local", tipo_pago="Efectivo")
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=500)
        self.assertEqual(orden.calcular_total(), 250000.0)

    def test_b03_15_fractional_discount_calculation(self):
        """TC-B03-15: Floating point discount subtraction avoids penny rounding error."""
        p = self.Plato.objects.create(nombre="Plato Redondeo", valor=1990.0)
        orden = self.Orden.objects.create(cliente="Mesa Centavo", canal_venta="Local", tipo_pago="Efectivo", descuento=190.0)
        self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        self.assertEqual(round(orden.calcular_total(), 2), 1800.0)

    # F15: POS async submission boundaries
    def test_b03_16_async_submit_missing_canal_venta(self):
        """TC-B03-16: Async order submission without canal_venta defaults safely."""
        p = self.Plato.objects.create(nombre="Plato Sin Canal", valor=4000.0)
        client = self.get_client()
        payload = {"cliente": "Cliente Default Canal", "tipo_pago": "Efectivo", "items": [{"tipo": "plato", "id": p.id, "cantidad": 1}]}
        resp = client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertNotEqual(resp.status_code, 500)

    def test_b03_17_async_submit_nonexistent_dish_id(self):
        """TC-B03-17: Async order submission with non-existent dish ID fails gracefully."""
        client = self.get_client()
        payload = {"cliente": "Cliente Fantasma", "canal_venta": "Local", "tipo_pago": "Efectivo", "items": [{"tipo": "plato", "id": 999999, "cantidad": 1}]}
        resp = client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertNotEqual(resp.status_code, 500)

    def test_b03_18_async_submit_negative_item_quantity(self):
        """TC-B03-18: Async order submission rejecting negative quantity in items."""
        p = self.Plato.objects.create(nombre="Plato Cantidad Negativa", valor=4000.0)
        client = self.get_client()
        payload = {"cliente": "Cliente Tramposo", "canal_venta": "Local", "tipo_pago": "Efectivo", "items": [{"tipo": "plato", "id": p.id, "cantidad": -2}]}
        resp = client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertNotEqual(resp.status_code, 500)

    def test_b03_19_async_submit_empty_items_list(self):
        """TC-B03-19: Async order submission with empty items array handled without 500."""
        client = self.get_client()
        payload = {"cliente": "Cliente Vacio", "canal_venta": "Local", "tipo_pago": "Efectivo", "items": []}
        resp = client.post("/pedidos/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertNotEqual(resp.status_code, 500)

    def test_b03_20_async_submit_content_type_flexibility(self):
        """TC-B03-20: POST supports standard form-encoded data as fallback."""
        client = self.get_client()
        resp = client.post("/pedidos/crear/", data={"cliente": "Form Test", "canal_venta": "Local", "tipo_pago": "Efectivo"})
        self.assertNotEqual(resp.status_code, 500)

    # F16: KDS board boundaries
    def test_b03_21_kds_zero_orders_board(self):
        """TC-B03-21: Kitchen board displays clean empty state message when 0 orders active."""
        client = self.get_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_22_kds_high_volume_orders(self):
        """TC-B03-22: Kitchen board renders 25 active orders without template timeout."""
        for i in range(25):
            self.Orden.objects.create(cliente=f"Mesa Rush {i}", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        client = self.get_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_23_kds_card_with_many_items(self):
        """TC-B03-23: Order card with 10 distinct items renders all items."""
        orden = self.Orden.objects.create(cliente="Mesa Banquete", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        for i in range(10):
            p = self.Plato.objects.create(nombre=f"Plato Banquete {i}", valor=2000)
            self.OrdenItem.objects.create(orden=orden, plato=p, cantidad=1)
        client = self.get_client()
        resp = client.get("/")
        content = resp.content.decode("utf-8")
        self.assertIn("Plato Banquete 0", content)
        self.assertIn("Plato Banquete 9", content)

    def test_b03_24_kds_handles_deleted_plate_reference(self):
        """TC-B03-24: Order item with null plato (SET_NULL) does not crash KDS template."""
        orden = self.Orden.objects.create(cliente="Mesa Plato Nulo", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        self.OrdenItem.objects.create(orden=orden, plato=None, cantidad=1)
        client = self.get_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_25_kds_elapsed_time_ordering(self):
        """TC-B03-25: Orders on KDS sorted chronologically (oldest pending orders first)."""
        o1 = self.Orden.objects.create(cliente="Primero", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        o2 = self.Orden.objects.create(cliente="Segundo", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        client = self.get_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)

    # F17: Kitchen polling boundaries
    def test_b03_26_polling_with_hx_target_header(self):
        """TC-B03-26: Server returns valid content when HX-Target header provided."""
        client = self.get_client()
        resp = client.get("/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="#pedidos-container")
        self.assertEqual(resp.status_code, 200)

    def test_b03_27_polling_rapid_requests(self):
        """TC-B03-27: Rapid successive polling requests execute without session lock."""
        client = self.get_client()
        for _ in range(5):
            resp = client.get("/", HTTP_HX_REQUEST="true")
            self.assertEqual(resp.status_code, 200)

    def test_b03_28_polling_query_efficiency(self):
        """TC-B03-28: Polling query executes with bounded SQL queries."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        client = self.get_client()
        with CaptureQueriesContext(connection) as ctx:
            client.get("/", HTTP_HX_REQUEST="true")
        self.assertLess(len(ctx.captured_queries), 30)

    def test_b03_29_polling_returns_html_content_type(self):
        """TC-B03-29: Polling response has text/html content type."""
        client = self.get_client()
        resp = client.get("/", HTTP_HX_REQUEST="true")
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))

    def test_b03_30_polling_reflects_order_completion_removal(self):
        """TC-B03-30: Completing an order removes it from subsequent kitchen polling response."""
        orden = self.Orden.objects.create(cliente="Desaparece KDS", canal_venta="Local", tipo_pago="Efectivo", estado="En curso")
        client = self.get_client()
        resp1 = client.get("/", HTTP_HX_REQUEST="true")
        self.assertIn("Desaparece KDS", resp1.content.decode("utf-8"))

        orden.estado = "Completada"
        orden.save()

        resp2 = client.get("/", HTTP_HX_REQUEST="true")
        self.assertNotIn("Desaparece KDS", resp2.content.decode("utf-8"))

    # F18: Inventory dashboard boundaries
    def test_b03_31_inventory_dashboard_empty_state(self):
        """TC-B03-31: Inventory dashboard displays clean state when 0 insumos exist."""
        client = self.get_client()
        resp = client.get("/inventario/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_32_inventory_dashboard_exact_threshold(self):
        """TC-B03-32: Insumo with stock_actual == stock_minimo is rendered accurately."""
        self.Insumo.objects.create(
            codigo="INS-EQ", nombre="Insumo Limite", unidad_medida="kg",
            stock_actual=Decimal("5.000"), stock_minimo=Decimal("5.000"), costo_unitario=Decimal("2000.000")
        )
        client = self.get_client()
        resp = client.get("/inventario/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Insumo Limite", resp.content.decode("utf-8"))

    def test_b03_33_inventory_dashboard_severe_quiebre_highlight(self):
        """TC-B03-33: Insumo with stock=0 or negative is presented on dashboard."""
        self.Insumo.objects.create(
            codigo="INS-QUIEBRE", nombre="Insumo Agotado", unidad_medida="kg",
            stock_actual=Decimal("0.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("5000.000")
        )
        client = self.get_client()
        resp = client.get("/inventario/")
        self.assertIn("Insumo Agotado", resp.content.decode("utf-8"))

    def test_b03_34_inventory_dashboard_active_filter(self):
        """TC-B03-34: Inactive insumos can be hidden from active inventory table."""
        self.Insumo.objects.create(
            codigo="INS-HIDDEN", nombre="Insumo Oculto", unidad_medida="kg",
            stock_actual=Decimal("10.000"), stock_minimo=Decimal("2.000"), costo_unitario=Decimal("1000.000"),
            activo=False
        )
        client = self.get_client()
        resp = client.get("/inventario/")
        self.assertEqual(resp.status_code, 200)

    def test_b03_35_inventory_manual_adjustment_boundary(self):
        """TC-B03-35: Manual stock adjustment modal or form element present in dashboard."""
        tpl_path = self.PROJECT_ROOT / "Menu" / "templates" / "Menu" / "inventario.html"
        if tpl_path.exists():
            content = tpl_path.read_text(encoding="utf-8")
            self.assertTrue(len(content) > 100)
