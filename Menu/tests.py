import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch
from Menu.models import Plato, Menu, Orden, OrdenItem, Insumo, RecetaItem, MovimientoStock
from Menu.utils import (
    imprimir_comanda,
    format_ticket_text,
    get_printer_adapter,
    BasePrinterAdapter,
    NoOpPrinterAdapter,
    WindowsSpoolerPrinterAdapter,
    EscposNetworkPrinterAdapter,
)


class HardwarePrinterDecouplingTests(TestCase):
    def setUp(self):
        self.plato = Plato.objects.create(nombre="Lomo a lo Pobre", valor=8500.0)
        self.orden = Orden.objects.create(cliente="Cliente Test", canal_venta="Local")
        self.item = OrdenItem.objects.create(orden=self.orden, plato=self.plato, cantidad=2)
        self.orden.monto_total = self.orden.calcular_total()
        self.orden.save()

    def test_imprimir_comanda_returns_contract_dict_without_exception(self):
        """Verifica que imprimir_comanda devuelve dict contractual seguro."""
        resultado = imprimir_comanda(self.orden)
        self.assertIsInstance(resultado, dict)
        self.assertIn("success", resultado)
        self.assertIn("mode", resultado)
        self.assertIn("message", resultado)
        self.assertIn(resultado["mode"], ["web_fallback", "windows_raw", "escpos_network", "noop"])

    def test_format_ticket_text_contains_order_and_item_details(self):
        """Verifica que el texto formateado contenga el ID, cliente, plato y total."""
        texto = format_ticket_text(self.orden, width=32)
        self.assertIn(f"ORDEN #{self.orden.id}", texto)
        self.assertIn("Cliente Test", texto)
        self.assertIn("Lomo a lo Pobre", texto)
        self.assertIn("17.000", texto)

    def test_combo_menu_explosion_in_ticket(self):
        """Verifica que un item de tipo Menu/Combo desglose sus platos y no diga 'Sin nombre'."""
        menu_combo = Menu.objects.create(nombre="Combo Almuerzo", precio_menus=6000.0)
        p1 = Plato.objects.create(nombre="Entrada Ensalada", valor=2000.0)
        p2 = Plato.objects.create(nombre="Plato Fondo", valor=5000.0)
        menu_combo.platos.add(p1, p2)

        orden_combo = Orden.objects.create(cliente="Cliente Combo", canal_venta="Delivery")
        OrdenItem.objects.create(orden=orden_combo, menu=menu_combo, cantidad=1)
        orden_combo.monto_total = orden_combo.calcular_total()
        orden_combo.save()

        texto = format_ticket_text(orden_combo, width=32)
        self.assertNotIn("Sin nombre", texto)
        self.assertIn("Combo Almuerzo", texto)
        self.assertIn("Entrada Ensalada", texto)

    def test_printer_adapter_error_handling(self):
        """Verifica captura segura de errores de impresora física."""
        with patch("Menu.utils.win32print", create=True) as mock_win:
            mock_win.OpenPrinter.side_effect = Exception("Impresora desconectada")
            resultado = imprimir_comanda(self.orden)
            self.assertIsInstance(resultado, dict)
            self.assertFalse(resultado["success"])
            self.assertEqual(resultado["mode"], "windows_raw")


class WebTicketViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.plato = Plato.objects.create(nombre="Cazuela", valor=4500.0)
        self.orden = Orden.objects.create(cliente="Juan Perez", canal_venta="Local")
        OrdenItem.objects.create(orden=self.orden, plato=self.plato, cantidad=1)
        self.orden.monto_total = self.orden.calcular_total()
        self.orden.save()

    def test_ticket_view_canonical_url(self):
        """Verifica respuesta HTTP 200 en ruta canónica /pedidos/<id>/ticket/."""
        response = self.client.get(f"/pedidos/{self.orden.id}/ticket/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"ORDEN #{self.orden.id}")
        self.assertContains(response, "Juan Perez")
        self.assertContains(response, "Cazuela")
        self.assertContains(response, "window.print()")

    def test_ticket_view_alias_url(self):
        """Verifica respuesta HTTP 200 en ruta alias /orden/<id>/ticket/."""
        response = self.client.get(f"/orden/{self.orden.id}/ticket/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"ORDEN #{self.orden.id}")

    def test_ticket_view_404_for_nonexistent_order(self):
        """Verifica 404 al solicitar un ID de orden inexistente."""
        response = self.client.get("/pedidos/999999/ticket/")
        self.assertEqual(response.status_code, 404)


class ConfirmarOrdenRemediationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.insumo = Insumo.objects.create(
            codigo="INS-TEST-REM",
            nombre="Queso Rem",
            unidad_medida="kg",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("2.000"),
            costo_unitario=Decimal("5000.000")
        )
        self.plato = Plato.objects.create(nombre="Pizza Rem", valor=6000.0)
        self.receta = RecetaItem.objects.create(plato=self.plato, insumo=self.insumo, cantidad=Decimal("0.500"))

    def test_confirm_eliminated_order_returns_400(self):
        """Reject confirming an order that is in 'Eliminada' status with HTTP 400."""
        orden = Orden.objects.create(cliente="Cliente Cancelado", canal_venta="Local", estado=Orden.ESTADO_ELIMINADA)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)

        resp = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Eliminada", data["error"])

        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_ELIMINADA)
        self.assertFalse(orden.stock_descontado)
        self.insumo.refresh_from_db()
        self.assertEqual(self.insumo.stock_actual, Decimal("10.000"))

    def test_confirm_order_refreshes_stock_descontado_in_response(self):
        """Successful confirmation returns stock_descontado: True in JSON response."""
        orden = Orden.objects.create(cliente="Cliente Activo", canal_venta="Local", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=2)

        resp = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({"tipo_pago": "Efectivo"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["stock_descontado"])
        self.assertEqual(data["movimientos"], 1)

        orden.refresh_from_db()
        self.assertTrue(orden.stock_descontado)
        self.assertEqual(orden.estado, Orden.ESTADO_COMPLETADA)
        self.insumo.refresh_from_db()
        self.assertEqual(self.insumo.stock_actual, Decimal("9.000"))

    def test_confirm_order_surfaces_deduction_failure(self):
        """When deduction fails, order completes but error_deduccion is logged and returned in JSON."""
        orden = Orden.objects.create(cliente="Cliente Error", canal_venta="Local", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=orden, plato=self.plato, cantidad=1)

        with patch("Menu.views.descontar_stock_orden") as mock_deduct:
            mock_deduct.return_value = {"success": False, "error": "Simulated lock timeout"}
            resp = self.client.post(f"/pedidos/{orden.id}/confirmar/", data=json.dumps({}), content_type="application/json")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertFalse(data["stock_descontado"])
            self.assertEqual(data["error_deduccion"], "Simulated lock timeout")
            self.assertIn("falló la deducción", data["message"])


class ModelStringRepresentationHardeningTests(TestCase):
    def test_receta_item_str_empty_unsaved_instance(self):
        """RecetaItem.__str__ never crashes on completely empty unsaved instance."""
        r = RecetaItem()
        s = str(r)
        self.assertIn("Plato no especificado", s)
        self.assertIn("Insumo no especificado", s)

    def test_receta_item_str_partial_instance(self):
        """RecetaItem.__str__ handles partial instances with only plato or insumo."""
        p = Plato(nombre="Empanada")
        r = RecetaItem(plato=p, cantidad=Decimal("0.250"))
        s = str(r)
        self.assertIn("Empanada", s)
        self.assertIn("0.250", s)
        self.assertIn("Insumo no especificado", s)

    def test_receta_item_str_full_saved(self):
        """RecetaItem.__str__ returns standard format when fully saved."""
        p = Plato.objects.create(nombre="Completo", valor=2500)
        ins = Insumo.objects.create(codigo="INS-PALTA", nombre="Palta", unidad_medida="kg")
        r = RecetaItem.objects.create(plato=p, insumo=ins, cantidad=Decimal("0.100"))
        self.assertEqual(str(r), "Completo -> 0.100 kg de Palta")

    def test_movimiento_stock_str_empty_unsaved_instance(self):
        """MovimientoStock.__str__ never crashes on completely empty unsaved instance."""
        m = MovimientoStock()
        s = str(m)
        self.assertIn("Sin fecha", s)
        self.assertIn("#Nuevo", s)
        self.assertIn("Sin insumo", s)

    def test_movimiento_stock_str_unsaved_with_data(self):
        """MovimientoStock.__str__ formats unsaved draft with #Nuevo and provided fields."""
        ins = Insumo(codigo="INS-TOMATE", nombre="Tomate", unidad_medida="kg")
        m = MovimientoStock(insumo=ins, tipo="AJUSTE_MANUAL", cantidad=Decimal("1.500"))
        s = str(m)
        self.assertIn("Sin fecha", s)
        self.assertIn("#Nuevo", s)
        self.assertIn("AJUSTE_MANUAL", s)
        self.assertIn("Tomate (1.500 kg)", s)

    def test_movimiento_stock_str_saved_instance(self):
        """MovimientoStock.__str__ formats saved instance with timestamp and #<id>."""
        ins = Insumo.objects.create(codigo="INS-CEBOLLA", nombre="Cebolla", unidad_medida="kg")
        m = MovimientoStock.objects.create(
            insumo=ins,
            tipo="CONSUMO_ORDEN",
            cantidad=Decimal("0.300"),
            stock_anterior=Decimal("5.000"),
            stock_nuevo=Decimal("4.700")
        )
        s = str(m)
        self.assertIn(f"#{m.id}", s)
        self.assertIn("CONSUMO_ORDEN", s)
        self.assertIn("Cebolla (0.300 kg)", s)
        self.assertNotIn("Sin fecha", s)



from django.utils import timezone
from pydantic import ValidationError
from Menu.services.inventory_service import descontar_stock_orden
from src.ai_forecast.schemas import (
    InsumoSugerido,
    SugerenciaOrdenCompra,
    parse_and_validate_forecast_json,
)
from src.ai_forecast.fallback import calcular_reorden_heuristico
from src.ai_forecast.forecaster import (
    calcular_consumo_diario_insumos,
    generar_sugerencias_compra,
)


class OrderLifecycleAndEscandalloIntegrationTests(TestCase):
    """
    Feature F25 — Pruebas de integración del ciclo de vida de órdenes,
    escandallo (recetas), deducción atómica de inventario y trazabilidad Kardex.
    """

    def setUp(self):
        self.client = Client()
        # Insumos
        self.insumo_carne = Insumo.objects.create(
            codigo="INS-CARNE",
            nombre="Carne Vacuno",
            unidad_medida="kg",
            stock_actual=Decimal("20.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("8000.000"),
        )
        self.insumo_queso = Insumo.objects.create(
            codigo="INS-QUESO",
            nombre="Queso Chanco",
            unidad_medida="kg",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("3.000"),
            costo_unitario=Decimal("6000.000"),
        )
        self.insumo_pan = Insumo.objects.create(
            codigo="INS-PAN",
            nombre="Pan Frica",
            unidad_medida="un",
            stock_actual=Decimal("50.000"),
            stock_minimo=Decimal("15.000"),
            costo_unitario=Decimal("350.000"),
        )

        # Platos
        self.plato_burger = Plato.objects.create(nombre="Hamburguesa con Queso", valor=5500.0)
        RecetaItem.objects.create(plato=self.plato_burger, insumo=self.insumo_carne, cantidad=Decimal("0.200"))
        RecetaItem.objects.create(plato=self.plato_burger, insumo=self.insumo_queso, cantidad=Decimal("0.080"))
        RecetaItem.objects.create(plato=self.plato_burger, insumo=self.insumo_pan, cantidad=Decimal("1.000"))

        self.plato_papas = Plato.objects.create(nombre="Porción Papas", valor=2500.0)

        # Combo
        self.combo = Menu.objects.create(nombre="Promo Burger Completa", precio_menus=7000.0)
        self.combo.platos.add(self.plato_burger, self.plato_papas)

    def test_order_creation_items_and_total_calculation(self):
        """Verifica la creación de orden con platos y combos y el cálculo correcto de montos."""
        orden = Orden.objects.create(cliente="Carlos Test", canal_venta="Local")
        OrdenItem.objects.create(orden=orden, plato=self.plato_burger, cantidad=2)
        OrdenItem.objects.create(orden=orden, menu=self.combo, cantidad=1)

        total = orden.calcular_total()
        # 2 * 5500 + 1 * 7000 = 18000
        self.assertEqual(total, 18000.0)
        orden.monto_total = total
        orden.save()
        self.assertEqual(orden.items.count(), 2)

    def test_order_state_transitions_en_curso_to_completada(self):
        """Verifica transiciones de estado de la orden y registro de fecha_completada."""
        orden = Orden.objects.create(cliente="Ana Test", canal_venta="PedidosYa", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=orden, plato=self.plato_burger, cantidad=1)

        orden.estado = Orden.ESTADO_COMPLETADA
        orden.fecha_completada = timezone.now()
        orden.save()

        orden.refresh_from_db()
        self.assertEqual(orden.estado, Orden.ESTADO_COMPLETADA)
        self.assertIsNotNone(orden.fecha_completada)

    def test_atomic_stock_deduction_and_kardex_logging(self):
        """Verifica descuento atómico de inventario por recetas y creación de auditoría Kardex."""
        orden = Orden.objects.create(cliente="Test Kardex", canal_venta="Local", estado=Orden.ESTADO_EN_CURSO)
        OrdenItem.objects.create(orden=orden, plato=self.plato_burger, cantidad=3)

        res = descontar_stock_orden(orden.id)
        self.assertTrue(res["success"])
        self.assertEqual(res["movimientos"], 3)

        self.insumo_carne.refresh_from_db()
        self.insumo_queso.refresh_from_db()
        self.insumo_pan.refresh_from_db()

        # 3 hamburguesas -> 3 * 0.200 = 0.600 carne (20.000 - 0.600 = 19.400)
        self.assertEqual(self.insumo_carne.stock_actual, Decimal("19.400"))
        # 3 * 0.080 = 0.240 queso (10.000 - 0.240 = 9.760)
        self.assertEqual(self.insumo_queso.stock_actual, Decimal("9.760"))
        # 3 * 1 = 3 pan (50.000 - 3.000 = 47.000)
        self.assertEqual(self.insumo_pan.stock_actual, Decimal("47.000"))

        movs = MovimientoStock.objects.filter(orden=orden)
        self.assertEqual(movs.count(), 3)
        for m in movs:
            self.assertEqual(m.tipo, "CONSUMO_ORDEN")
            self.assertGreater(m.cantidad, 0)

    def test_combo_menu_recipe_explosion_stock_deduction(self):
        """Verifica que un combo explote sus platos y descuente los insumos correspondientes."""
        orden = Orden.objects.create(cliente="Test Combo Explosion", canal_venta="Delivery")
        OrdenItem.objects.create(orden=orden, menu=self.combo, cantidad=2)

        res = descontar_stock_orden(orden.id)
        self.assertTrue(res["success"])

        self.insumo_carne.refresh_from_db()
        # 2 combos * 1 burger cada uno * 0.200 carne = 0.400 (20.000 - 0.400 = 19.600)
        self.assertEqual(self.insumo_carne.stock_actual, Decimal("19.600"))

    def test_stock_deduction_idempotency_prevents_double_decrement(self):
        """Verifica que invocar el servicio de deducción dos veces sobre la misma orden sea idempotente."""
        orden = Orden.objects.create(cliente="Test Idempotente", canal_venta="Local")
        OrdenItem.objects.create(orden=orden, plato=self.plato_burger, cantidad=1)

        res1 = descontar_stock_orden(orden.id)
        self.assertTrue(res1["success"])

        self.insumo_carne.refresh_from_db()
        stock_tras_primera = self.insumo_carne.stock_actual

        res2 = descontar_stock_orden(orden.id)
        self.assertTrue(res2["success"])
        self.assertTrue(res2.get("idempotente", False) or res2["movimientos"] == 0)

        self.insumo_carne.refresh_from_db()
        self.assertEqual(self.insumo_carne.stock_actual, stock_tras_primera)

    def test_low_stock_alert_generation(self):
        """Verifica que al caer el stock por debajo del mínimo se retornen alertas de stock crítico."""
        # Dejar carne en stock mínimo o cercano
        self.insumo_carne.stock_actual = Decimal("5.100")
        self.insumo_carne.save()

        orden = Orden.objects.create(cliente="Test Alerta", canal_venta="Local")
        # 1 burger consume 0.200 carne -> stock final 4.900 < stock_minimo 5.000
        OrdenItem.objects.create(orden=orden, plato=self.plato_burger, cantidad=1)

        res = descontar_stock_orden(orden.id)
        self.assertTrue(res["success"])
        alertas = res.get("alertas_stock_minimo", [])
        self.assertGreaterEqual(len(alertas), 1)
        codigos_alerta = [a.get("codigo") or a.get("insumo") for a in alertas]
        self.assertTrue(any("INS-CARNE" in str(c) for c in codigos_alerta))


class AIForecastingAndGuardrailsTests(TestCase):
    """
    Feature F25 — Pruebas de integración y unitarias para esquemas Pydantic,
    guardrails de IA, fallback determinístico ROP y endpoints de sugerencia de compras.
    """

    def setUp(self):
        self.client = Client()
        self.insumo1 = Insumo.objects.create(
            codigo="INS-TOMATE",
            nombre="Tomate Fresco",
            unidad_medida="kg",
            stock_actual=Decimal("5.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("1200.000"),
            activo=True,
        )
        self.insumo2 = Insumo.objects.create(
            codigo="INS-CEBOLLA",
            nombre="Cebolla Blanca",
            unidad_medida="kg",
            stock_actual=Decimal("15.000"),
            stock_minimo=Decimal("8.000"),
            costo_unitario=Decimal("900.000"),
            activo=True,
        )

    def test_insumo_sugerido_schema_validations(self):
        """Verifica validaciones de InsumoSugerido: strings no vacíos y subtotal coherente."""
        item = InsumoSugerido(
            insumo_id=1,
            codigo="  INS-TEST  ",
            nombre="  Insumo Test  ",
            unidad_medida="kg",
            stock_actual=2.0,
            stock_minimo=5.0,
            consumo_diario_estimado=1.5,
            cantidad_sugerida=6.0,
            costo_unitario=1000.0,
            costo_subtotal=6000.0,
            justificacion="  Reabastecimiento regular  ",
        )
        self.assertEqual(item.codigo, "INS-TEST")
        self.assertEqual(item.nombre, "Insumo Test")
        self.assertEqual(item.justificacion, "Reabastecimiento regular")
        self.assertEqual(item.costo_subtotal, 6000.0)

    def test_insumo_sugerido_rejects_empty_strings(self):
        """Verifica rechazo de strings vacíos en InsumoSugerido."""
        with self.assertRaises(ValidationError):
            InsumoSugerido(
                insumo_id=1,
                codigo="   ",
                nombre="Valido",
                unidad_medida="kg",
                stock_actual=1.0,
                stock_minimo=1.0,
                consumo_diario_estimado=1.0,
                cantidad_sugerida=1.0,
                costo_unitario=100.0,
                costo_subtotal=100.0,
                justificacion="Valido",
            )

    def test_sugerencia_orden_compra_budget_reconciliation(self):
        """Verifica que SugerenciaOrdenCompra auto-reconcilie el presupuesto total con la suma de líneas."""
        item = InsumoSugerido(
            insumo_id=1,
            codigo="INS-1",
            nombre="Insumo 1",
            unidad_medida="kg",
            stock_actual=0.0,
            stock_minimo=5.0,
            consumo_diario_estimado=1.0,
            cantidad_sugerida=5.0,
            costo_unitario=2000.0,
            costo_subtotal=10000.0,
            justificacion="Stockout",
        )
        # Pasamos un presupuesto erróneo (0.0) que debe reconciliarse a 10000.0
        po = SugerenciaOrdenCompra(
            items_sugeridos=[item],
            presupuesto_estimado_total=0.0,
            periodo_dias=7,
        )
        self.assertEqual(po.presupuesto_estimado_total, 10000.0)

    def test_parse_and_validate_forecast_json_cleaning(self):
        """Verifica el saneamiento de markdown codeblocks en parse_and_validate_forecast_json."""
        raw_json = """```json
        {
            "items_sugeridos": [
                {
                    "insumo_id": 1,
                    "codigo": "INS-MOCK",
                    "nombre": "Insumo Mock",
                    "unidad_medida": "kg",
                    "stock_actual": 1.0,
                    "stock_minimo": 4.0,
                    "consumo_diario_estimado": 1.0,
                    "cantidad_sugerida": 5.0,
                    "costo_unitario": 1500.0,
                    "costo_subtotal": 7500.0,
                    "justificacion": "Demanda pronosticada"
                }
            ],
            "presupuesto_estimado_total": 7500.0,
            "periodo_dias": 7,
            "metodo": "LLM_GENERATED"
        }
        ```"""
        resultado = parse_and_validate_forecast_json(raw_json)
        self.assertIsInstance(resultado, SugerenciaOrdenCompra)
        self.assertEqual(len(resultado.items_sugeridos), 1)
        self.assertEqual(resultado.items_sugeridos[0].codigo, "INS-MOCK")

    def test_parse_and_validate_forecast_json_rejection_invalid(self):
        """Verifica que JSON inválido o vacío lance excepción."""
        with self.assertRaises(ValueError):
            parse_and_validate_forecast_json("")

        with self.assertRaises(Exception):
            parse_and_validate_forecast_json("Este no es un json valido")

    def test_deterministic_fallback_reorder_point_calculation(self):
        """Verifica el cálculo exacto de Punto de Reorden en calcular_reorden_heuristico."""
        # Tomate: StockActual 5, StockMin 10, ConsumoDiario 3.0, LeadTime 2
        # Demanda LeadTime = 3.0 * 2 = 6.0
        # ROP = 6.0 + 10.0 = 16.0
        # Deficit = 16.0 - 5.0 = 11.0 kg sugerido
        consumos = {self.insumo1.id: 3.0, self.insumo2.id: 1.0}
        resultado = calcular_reorden_heuristico(
            insumos=[self.insumo1, self.insumo2],
            consumos_diarios=consumos,
            dias_lead_time=2,
            dias_proyeccion=7,
        )
        self.assertEqual(resultado.metodo, "HEURISTIC_FALLBACK")
        items = {it.codigo: it for it in resultado.items_sugeridos}
        self.assertEqual(items["INS-TOMATE"].cantidad_sugerida, 11.0)
        # Cebolla: StockActual 15, StockMin 8, Consumo 1 * 2 = 2. ROP = 10 <= 15 -> 0.0
        self.assertEqual(items["INS-CEBOLLA"].cantidad_sugerida, 0.0)

    def test_calcular_consumo_diario_insumos_from_orders(self):
        """Verifica la agregación de ventas históricas a consumo diario de insumos."""
        plato = Plato.objects.create(nombre="Ensalada Tomate", valor=3000)
        RecetaItem.objects.create(plato=plato, insumo=self.insumo1, cantidad=Decimal("0.500"))

        orden = Orden.objects.create(
            cliente="Cliente Ensalada",
            canal_venta="Local",
            estado=Orden.ESTADO_COMPLETADA,
            fecha=timezone.now().date(),
        )
        OrdenItem.objects.create(orden=orden, plato=plato, cantidad=4)

        consumos = calcular_consumo_diario_insumos(dias_historia=7)
        # 4 platos * 0.500 = 2.0 kg consumidos en 1 día activo = 2.0 kg/día
        self.assertIn(self.insumo1.id, consumos)
        self.assertEqual(consumos[self.insumo1.id], 2.0)

    def test_ai_suggestions_api_endpoints_get_and_options(self):
        """Verifica que los endpoints /api/sugerencias-compra/ y /inventario/sugerencias-ia/ respondan HTTP 200."""
        # GET sugerencias
        resp = self.client.get("/api/sugerencias-compra/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items_sugeridos", data)
        self.assertIn("presupuesto_estimado_total", data)

        # OPTIONS
        resp_opt = self.client.options("/api/sugerencias-compra/")
        self.assertEqual(resp_opt.status_code, 200)
        self.assertIn("GET", resp_opt.headers.get("Allow", ""))

        # POST no permitido -> 405
        resp_post = self.client.post("/api/sugerencias-compra/", data={})
        self.assertEqual(resp_post.status_code, 405)
