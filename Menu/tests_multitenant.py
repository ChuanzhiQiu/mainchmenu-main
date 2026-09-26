"""
Comprehensive Test Suite for Multi-Tenant Core Architecture & Slug Routing (Milestone M6 / Requirement R1).
Track: MBAn UAI 2026-B Track A - MainchApp.

Verifies:
1. Cross-tenant isolation (Tenant A cannot see, confirm, or delete Tenant B data).
2. Composite natural SKU uniqueness (same SKU allowed across tenants, prohibited within tenant).
3. Relational integrity guardrails (preventing cross-tenant dishes in combos, recipes, or orders).
4. Canonical /r/<slug>/ dual routing and zero-redirect legacy endpoint compatibility.
5. Inactive and non-existent tenant 404 handling.
6. Anti-CDN cache headers (Cache-Control: private, no-store and Vary).
7. Inventory deduction service multi-tenant security and MovimientoStock tenant assignment.
8. AI Demand Forecasting and heuristic fallback tenant scoping.
9. ContextVar thread-safety and unscoped manager traversal.
"""

from decimal import Decimal
import json
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User

from Menu.models import (
    Restaurante, Plato, Menu, Insumo, Orden, OrdenItem, RecetaItem, MovimientoStock
)
from Menu.tenant_context import (
    get_current_tenant, set_current_tenant, reset_current_tenant, tenant_context
)
from Menu.services.inventory_service import descontar_stock_orden
from src.ai_forecast.forecaster import calcular_consumo_diario_insumos, generar_sugerencias_compra
from src.ai_forecast.fallback import calcular_reorden_heuristico


class MultiTenantCoreArchitectureTests(TestCase):
    """Pruebas integrales de arquitectura multi-inquilino para MainchApp."""

    def setUp(self):
        self.client = Client()
        # Admin user for protected views
        self.admin_user = User.objects.create_superuser(
            username="admin_tenant", password="password123", email="admin@tenant.com"
        )
        self.client.force_login(self.admin_user)

        # Inquilino A: Mainch Central
        self.tenant_a = Restaurante.objects.create(
            nombre="Mainch Central",
            slug="mainch-central",
            direccion="Av. Valparaíso 123",
            activo=True
        )

        # Inquilino B: Smash Burger Express
        self.tenant_b = Restaurante.objects.create(
            nombre="Smash Burger Express",
            slug="smash-express",
            direccion="Calle Prat 456",
            activo=True
        )

        # Inquilino Inactivo
        self.tenant_inactive = Restaurante.objects.create(
            nombre="Restaurante Clausurado",
            slug="clausurado",
            direccion="Sin direccion",
            activo=False
        )

        # Catálogo Inquilino A
        self.insumo_carne_a = Insumo.objects.create(
            restaurante=self.tenant_a,
            codigo="INS-CARNE",
            nombre="Carne Vacuno Molida 80/20",
            unidad_medida="kg",
            stock_actual=Decimal("50.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("6000.000")
        )
        self.insumo_pan_a = Insumo.objects.create(
            restaurante=self.tenant_a,
            codigo="INS-PAN",
            nombre="Pan Brioche A",
            unidad_medida="un",
            stock_actual=Decimal("100.000"),
            stock_minimo=Decimal("20.000"),
            costo_unitario=Decimal("350.000")
        )
        self.plato_burger_a = Plato.objects.create(
            restaurante=self.tenant_a,
            nombre="Hamburguesa Clásica A",
            valor=6500.0
        )
        self.receta_a1 = RecetaItem.objects.create(
            restaurante=self.tenant_a,
            plato=self.plato_burger_a,
            insumo=self.insumo_carne_a,
            cantidad=Decimal("0.200")
        )
        self.receta_a2 = RecetaItem.objects.create(
            restaurante=self.tenant_a,
            plato=self.plato_burger_a,
            insumo=self.insumo_pan_a,
            cantidad=Decimal("1.000")
        )

        # Catálogo Inquilino B (Con el mismo SKU INS-CARNE e INS-PAN)
        self.insumo_carne_b = Insumo.objects.create(
            restaurante=self.tenant_b,
            codigo="INS-CARNE",
            nombre="Carne Wagyu Smash B",
            unidad_medida="kg",
            stock_actual=Decimal("30.000"),
            stock_minimo=Decimal("5.000"),
            costo_unitario=Decimal("12000.000")
        )
        self.insumo_pan_b = Insumo.objects.create(
            restaurante=self.tenant_b,
            codigo="INS-PAN",
            nombre="Pan Papa B",
            unidad_medida="un",
            stock_actual=Decimal("80.000"),
            stock_minimo=Decimal("15.000"),
            costo_unitario=Decimal("500.000")
        )
        self.plato_burger_b = Plato.objects.create(
            restaurante=self.tenant_b,
            nombre="Double Smash Burger B",
            valor=8900.0
        )
        self.receta_b1 = RecetaItem.objects.create(
            restaurante=self.tenant_b,
            plato=self.plato_burger_b,
            insumo=self.insumo_carne_b,
            cantidad=Decimal("0.250")
        )

    def test_01_composite_sku_uniqueness(self):
        """TC-MT-01: Mismo SKU permitido en diferentes inquilinos, duplicado rechazado dentro del mismo inquilino."""
        self.assertEqual(self.insumo_carne_a.codigo, "INS-CARNE")
        self.assertEqual(self.insumo_carne_b.codigo, "INS-CARNE")
        self.assertNotEqual(self.insumo_carne_a.restaurante_id, self.insumo_carne_b.restaurante_id)

        duplicado_a = Insumo(
            restaurante=self.tenant_a,
            codigo="ins-carne",
            nombre="Carne Duplicada",
            unidad_medida="kg",
            stock_actual=Decimal("10.000")
        )
        with self.assertRaises((ValidationError, IntegrityError)):
            duplicado_a.full_clean()
            duplicado_a.save()

    def test_02_relational_integrity_combo_cross_tenant_guard(self):
        """TC-MT-02: Un Combo (Menu) de Inquilino A no puede incluir platos de Inquilino B."""
        combo_invalido = Menu(
            restaurante=self.tenant_a,
            nombre="Combo Ilegal",
            precio_menus=9900.0
        )
        combo_invalido.save()
        combo_invalido.platos.add(self.plato_burger_b)

        with self.assertRaises(ValidationError):
            combo_invalido.clean()

    def test_03_relational_integrity_recipe_cross_tenant_guard(self):
        """TC-MT-03: Una RecetaItem no puede vincular un plato de Inquilino A con un insumo de Inquilino B."""
        receta_ilegal = RecetaItem(
            restaurante=self.tenant_a,
            plato=self.plato_burger_a,
            insumo=self.insumo_carne_b,
            cantidad=Decimal("0.150")
        )
        with self.assertRaises(ValidationError):
            receta_ilegal.clean()

    def test_04_relational_integrity_order_item_cross_tenant_guard(self):
        """TC-MT-04: Un OrdenItem no puede asociar una orden de Inquilino A con un plato de Inquilino B."""
        orden_a = Orden.objects.create(
            restaurante=self.tenant_a,
            cliente="Mesa 1",
            canal_venta="Local"
        )
        item_ilegal = OrdenItem(
            orden=orden_a,
            plato=self.plato_burger_b,
            cantidad=2
        )
        with self.assertRaises(ValidationError):
            item_ilegal.clean()

    def test_05_canonical_slug_routing_and_views(self):
        """TC-MT-05: Rutas canónicas /r/<slug>/ responden con 200 y aíslan el catálogo por inquilino."""
        resp_a = self.client.get(f"/r/{self.tenant_a.slug}/pos/")
        self.assertEqual(resp_a.status_code, 200)
        content_a = resp_a.content.decode("utf-8")
        self.assertIn("Hamburguesa Clásica A", content_a)
        self.assertNotIn("Double Smash Burger B", content_a)

        resp_b = self.client.get(f"/r/{self.tenant_b.slug}/pos/")
        self.assertEqual(resp_b.status_code, 200)
        content_b = resp_b.content.decode("utf-8")
        self.assertIn("Double Smash Burger B", content_b)
        self.assertNotIn("Hamburguesa Clásica A", content_b)

    def test_06_legacy_routing_compatibility_zero_redirect(self):
        """TC-MT-06: Rutas heredadas (/ y /pedidos/crear/) responden con 200 sin redirección HTTP."""
        resp_root = self.client.get("/")
        self.assertEqual(resp_root.status_code, 200, "Ruta raíz debe responder 200 sin 301/302")

        resp_pos = self.client.get("/pedidos/crear/")
        self.assertEqual(resp_pos.status_code, 200, "Ruta legado POS debe responder 200 sin 301/302")

        resp_inv = self.client.get("/inventario/")
        self.assertEqual(resp_inv.status_code, 200, "Ruta inventario debe responder 200")

    def test_07_nonexistent_or_inactive_tenant_returns_404(self):
        """TC-MT-07: Inquilino inexistente o inactivo en la URL canónica produce HTTP 404."""
        resp_404 = self.client.get("/r/restaurante-fantasma/pos/")
        self.assertEqual(resp_404.status_code, 404)

        resp_inactive = self.client.get(f"/r/{self.tenant_inactive.slug}/pos/")
        self.assertEqual(resp_inactive.status_code, 404)

    def test_08_anti_cdn_caching_and_vary_headers(self):
        """TC-MT-08: Respuestas dinámicas incluyen headers anti-caching de Edge CDN y Vary."""
        resp = self.client.get(f"/r/{self.tenant_a.slug}/pos/")
        self.assertEqual(resp.status_code, 200)

        cache_control = resp.headers.get("Cache-Control", "")
        self.assertIn("private", cache_control)
        self.assertIn("no-store", cache_control)

        vary = resp.headers.get("Vary", "")
        self.assertIn("X-Tenant-Slug", vary)
        self.assertEqual(resp.headers.get("X-Tenant-Slug"), self.tenant_a.slug)

    def test_09_order_cross_tenant_isolation(self):
        """TC-MT-09: Un inquilino no puede confirmar, eliminar ni ver órdenes de otro inquilino."""
        orden_b = Orden.objects.create(
            restaurante=self.tenant_b,
            cliente="Cliente Inquilino B",
            canal_venta="Local",
            monto_total=8900.0,
            estado=Orden.ESTADO_EN_CURSO
        )
        OrdenItem.objects.create(
            orden=orden_b,
            plato=self.plato_burger_b,
            cantidad=1
        )

        resp_ticket = self.client.get(f"/r/{self.tenant_a.slug}/pedidos/{orden_b.id}/ticket/")
        self.assertEqual(resp_ticket.status_code, 404)

        resp_confirmar = self.client.post(
            f"/r/{self.tenant_a.slug}/orden/{orden_b.id}/confirmar/"
        )
        self.assertIn(resp_confirmar.status_code, [400, 404])
        data_conf = resp_confirmar.json()
        self.assertFalse(data_conf["success"])

        resp_eliminar = self.client.post(
            f"/r/{self.tenant_a.slug}/eliminar_orden/",
            data=json.dumps({"id": orden_b.id}),
            content_type="application/json"
        )
        self.assertEqual(resp_eliminar.status_code, 404)
        self.assertFalse(resp_eliminar.json()["success"])

        orden_b.refresh_from_db()
        self.assertEqual(orden_b.estado, Orden.ESTADO_EN_CURSO)

    def test_10_inventory_deduction_multi_tenant_security(self):
        """TC-MT-10: La deducción de stock valida el restaurante y asigna el tenant a MovimientoStock."""
        orden_a = Orden.objects.create(
            restaurante=self.tenant_a,
            cliente="Mesa Deduccion",
            canal_venta="Local",
            estado=Orden.ESTADO_EN_CURSO
        )
        OrdenItem.objects.create(
            orden=orden_a,
            plato=self.plato_burger_a,
            cantidad=2
        )

        resultado_rechazado = descontar_stock_orden(orden_a.id, restaurante_esperado=self.tenant_b)
        self.assertFalse(resultado_rechazado["success"])
        self.assertIn("no pertenece al restaurante", resultado_rechazado["error"])

        resultado_ok = descontar_stock_orden(orden_a.id, restaurante_esperado=self.tenant_a)
        self.assertTrue(resultado_ok["success"])

        self.insumo_carne_a.refresh_from_db()
        self.assertEqual(self.insumo_carne_a.stock_actual, Decimal("49.600"))

        self.insumo_carne_b.refresh_from_db()
        self.assertEqual(self.insumo_carne_b.stock_actual, Decimal("30.000"))

        movimientos = MovimientoStock.objects.filter(orden=orden_a)
        self.assertGreater(movimientos.count(), 0)
        for mov in movimientos:
            self.assertEqual(mov.restaurante_id, self.tenant_a.id)

    def test_11_ai_demand_forecasting_tenant_isolation(self):
        """TC-MT-11: Ventas en Inquilino A no contaminan las sugerencias de compra ni el histórico de Inquilino B."""
        for i in range(10):
            ord_a = Orden.objects.create(
                restaurante=self.tenant_a,
                cliente=f"Cliente IA {i}",
                canal_venta="Local",
                estado=Orden.ESTADO_COMPLETADA,
                monto_total=6500.0,
                stock_descontado=True
            )
            OrdenItem.objects.create(
                orden=ord_a,
                plato=self.plato_burger_a,
                cantidad=2
            )

        consumo_a = calcular_consumo_diario_insumos(dias_historia=14, restaurante=self.tenant_a)
        self.assertIn(self.insumo_carne_a.id, consumo_a)
        self.assertGreater(consumo_a[self.insumo_carne_a.id], 0.0)

        consumo_b = calcular_consumo_diario_insumos(dias_historia=14, restaurante=self.tenant_b)
        self.assertEqual(consumo_b.get(self.insumo_carne_b.id, 0.0), 0.0)

        sugerencias_b = calcular_reorden_heuristico(
            dias_proyeccion=7,
            restaurante=self.tenant_b
        )
        insumo_ids_sugeridos = [item.insumo_id for item in sugerencias_b.items_sugeridos]
        self.assertIn(self.insumo_carne_b.id, insumo_ids_sugeridos)
        self.assertNotIn(self.insumo_carne_a.id, insumo_ids_sugeridos)

    def test_12_tenant_contextvar_and_unscoped_manager(self):
        """TC-MT-12: ContextVar conmuta limpiamente y unscoped() permite consultas sin filtro."""
        with tenant_context(self.tenant_a):
            self.assertEqual(get_current_tenant().id, self.tenant_a.id)
            platos = list(Plato.objects.all())
            self.assertIn(self.plato_burger_a, platos)
            self.assertNotIn(self.plato_burger_b, platos)

            platos_todos = list(Plato.objects.unscoped())
            self.assertIn(self.plato_burger_a, platos_todos)
            self.assertIn(self.plato_burger_b, platos_todos)

        self.assertIsNone(get_current_tenant())
