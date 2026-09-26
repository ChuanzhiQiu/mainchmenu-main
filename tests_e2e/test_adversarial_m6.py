"""
Adversarial Stress Test Suite for Milestone M6 (challenger_m6_1).
Empirical verification of Multi-Tenant Isolation (Requirement R1 / Milestone M6).

Targets:
1. Cross-tenant order manipulation:
   - Tenant A attempts to confirm, delete, or modify Tenant B's order.
   - Assert requests are strictly rejected (HTTP 404 / 403 / 400).
   - Assert inventory stock of victim Tenant B is NEVER deducted (0.000 kg).
   - Assert zero MovimientoStock created for victim tenant.
2. Cross-tenant dish / combo / recipe injection:
   - Tenant A attempts to create an order containing dishes belonging to Tenant B (JSON & form-encoded).
   - Mixed / trojan order payload: atomic rejection, zero partial order created.
   - Cross-tenant combo creation and recipe ingredient assignment.
   - Assert relational validation rejects creation and confirmation.
3. Multi-threaded concurrency attack:
   - 10 concurrent threads switching between Tenant A, Tenant B, Tenant C simultaneously.
   - Zero ContextVar tenant pollution or race condition across threads.
   - Clean reset guaranteed even upon view exceptions.
"""

from decimal import Decimal
import json
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.test import TestCase, TransactionTestCase, Client, RequestFactory
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.http import HttpResponse

from Menu.models import (
    Restaurante, Plato, Menu as ComboMenu, Insumo, Orden, OrdenItem, RecetaItem, MovimientoStock
)
from Menu.tenant_context import (
    get_current_tenant, set_current_tenant, reset_current_tenant, tenant_context
)
from Menu.middleware import TenantMiddleware
from Menu.services.inventory_service import descontar_stock_orden


class TestAdversarialCrossTenantOrderManipulation(TestCase):
    """
    Dimension 1: Cross-tenant order manipulation attacks.
    Tenant A attempts to confirm, delete, modify, or snoop on Tenant B's orders.
    """

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_adv_m6", password="password123", email="admin_m6@test.com"
        )
        self.client.force_login(self.admin_user)

        # Tenant A: Alpha Bistro
        self.tenant_a = Restaurante.objects.create(
            nombre="Alpha Bistro",
            slug="alpha-bistro",
            direccion="Calle 1, Viña del Mar",
            activo=True
        )

        # Tenant B: Beta Grill (Victim)
        self.tenant_b = Restaurante.objects.create(
            nombre="Beta Grill",
            slug="beta-grill",
            direccion="Calle 2, Valparaíso",
            activo=True
        )

        # Insumos Tenant A
        self.insumo_a = Insumo.objects.create(
            restaurante=self.tenant_a,
            codigo="INS-CARNE",
            nombre="Carne Alpha",
            unidad_medida="kg",
            stock_actual=Decimal("50.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("5000.000")
        )

        # Insumos Tenant B (Victim)
        self.insumo_b = Insumo.objects.create(
            restaurante=self.tenant_b,
            codigo="INS-CARNE",
            nombre="Carne Premium Beta",
            unidad_medida="kg",
            stock_actual=Decimal("40.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("10000.000")
        )

        # Dishes
        self.plato_a = Plato.objects.create(
            restaurante=self.tenant_a,
            nombre="Hamburguesa Alpha",
            valor=6000.0
        )
        RecetaItem.objects.create(
            restaurante=self.tenant_a,
            plato=self.plato_a,
            insumo=self.insumo_a,
            cantidad=Decimal("0.200")
        )

        self.plato_b = Plato.objects.create(
            restaurante=self.tenant_b,
            nombre="Bife Chorizo Beta",
            valor=12000.0
        )
        RecetaItem.objects.create(
            restaurante=self.tenant_b,
            plato=self.plato_b,
            insumo=self.insumo_b,
            cantidad=Decimal("0.400")
        )

        # Order in Tenant B (Victim Order)
        self.orden_b = Orden.objects.create(
            restaurante=self.tenant_b,
            cliente="Victim Customer B",
            canal_venta=Orden.CANAL_LOCAL,
            estado=Orden.ESTADO_EN_CURSO,
            monto_total=12000.0,
            descuento=0.0,
            stock_descontado=False
        )
        self.item_b = OrdenItem.objects.create(
            orden=self.orden_b,
            plato=self.plato_b,
            cantidad=2,
            precio_unitario=12000.0
        )

    def test_adv_01_cross_tenant_confirmation_hijack_rejected(self):
        """
        Attack 1.1: Tenant A attempts to confirm Tenant B's order via canonical slug route.
        Assert: HTTP 404 rejected, Tenant B order state remains 'En curso',
        stock is NEVER deducted (remains 40.000 kg), zero MovimientoStock created.
        """
        initial_stock_b = self.insumo_b.stock_actual
        self.assertEqual(initial_stock_b, Decimal("40.000"))

        # Adversary calls confirmation endpoint under Tenant A's slug
        resp = self.client.post(f"/r/{self.tenant_a.slug}/pedidos/{self.orden_b.id}/confirmar/")
        self.assertEqual(resp.status_code, 404, "Cross-tenant confirmation MUST return 404")
        data = resp.json()
        self.assertFalse(data.get("success"), "Confirmation response must indicate failure")

        # Verify victim order is completely unmolested
        self.orden_b.refresh_from_db()
        self.assertEqual(self.orden_b.estado, Orden.ESTADO_EN_CURSO, "Order status must remain 'En curso'")
        self.assertFalse(self.orden_b.stock_descontado, "stock_descontado must remain False")

        # Verify victim inventory is strictly untouched
        self.insumo_b.refresh_from_db()
        self.assertEqual(self.insumo_b.stock_actual, initial_stock_b, "Victim stock must NEVER be deducted")

        # Verify no Kardex audit trail was created
        movimientos = MovimientoStock.objects.filter(orden=self.orden_b)
        self.assertEqual(movimientos.count(), 0, "No stock movement should be recorded")

    def test_adv_02_cross_tenant_confirmation_alias_routes_rejected(self):
        """
        Attack 1.2: Tenant A attempts to confirm Tenant B's order via alias routes.
        (/r/<slug>/orden/<id>/confirmar/ and /r/<slug>/orden/confirmar/<id>/)
        Assert: Both return HTTP 404 and stock is never deducted.
        """
        for path_template in [
            f"/r/{self.tenant_a.slug}/orden/{self.orden_b.id}/confirmar/",
            f"/r/{self.tenant_a.slug}/orden/confirmar/{self.orden_b.id}/",
        ]:
            resp = self.client.post(path_template)
            self.assertEqual(resp.status_code, 404, f"Route {path_template} must return 404")

        self.insumo_b.refresh_from_db()
        self.assertEqual(self.insumo_b.stock_actual, Decimal("40.000"))

    def test_adv_03_cross_tenant_order_deletion_url_rejected(self):
        """
        Attack 1.3: Tenant A attempts to delete Tenant B's order via URL parameter.
        Assert: HTTP 404 rejected, order status remains 'En curso' (NOT 'Eliminada').
        """
        resp = self.client.post(f"/r/{self.tenant_a.slug}/pedidos/{self.orden_b.id}/eliminar/")
        self.assertEqual(resp.status_code, 404, "Cross-tenant deletion via URL must return 404")

        self.orden_b.refresh_from_db()
        self.assertEqual(self.orden_b.estado, Orden.ESTADO_EN_CURSO, "Victim order must not be deleted")

    def test_adv_04_cross_tenant_order_deletion_json_payload_rejected(self):
        """
        Attack 1.4: Tenant A attempts to delete Tenant B's order via JSON body ID tampering.
        Assert: HTTP 404 rejected, order status remains 'En curso'.
        """
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/eliminar_orden/",
            data=json.dumps({"id": self.orden_b.id}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404, "Cross-tenant deletion via JSON body must return 404")

        self.orden_b.refresh_from_db()
        self.assertEqual(self.orden_b.estado, Orden.ESTADO_EN_CURSO, "Victim order must not be deleted")

    def test_adv_05_cross_tenant_order_field_tampering_rejected(self):
        """
        Attack 1.5: Tenant A attempts to tamper with Tenant B's order fields (huge discount / fake payment).
        Assert: Request is rejected, fields on victim order remain unmodified.
        """
        payload = {
            "tipo_pago": "Efectivo",
            "descuento": 99999.0
        }
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/{self.orden_b.id}/confirmar/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)

        self.orden_b.refresh_from_db()
        self.assertEqual(self.orden_b.descuento, 0.0, "Discount must not be manipulated")
        self.assertEqual(self.orden_b.monto_total, 12000.0, "Total must remain intact")

    def test_adv_06_cross_tenant_ticket_snooping_rejected(self):
        """
        Attack 1.6: Tenant A attempts to view Tenant B's customer ticket / receipt.
        Assert: HTTP 404, zero customer info or items leaked.
        """
        for ticket_url in [
            f"/r/{self.tenant_a.slug}/pedidos/{self.orden_b.id}/ticket/",
            f"/r/{self.tenant_a.slug}/orden/{self.orden_b.id}/ticket/",
        ]:
            resp = self.client.get(ticket_url)
            self.assertEqual(resp.status_code, 404, f"Ticket endpoint {ticket_url} must return 404")
            self.assertNotIn("Victim Customer B", resp.content.decode("utf-8", errors="ignore"))

    def test_adv_07_direct_inventory_service_cross_tenant_guard(self):
        """
        Attack 1.7: Direct exploit of inventory service bypassing HTTP views.
        Assert: descontar_stock_orden rejects cross-tenant mismatch, 0 stock deducted.
        """
        result = descontar_stock_orden(self.orden_b.id, restaurante_esperado=self.tenant_a)
        self.assertFalse(result["success"], "Service must reject cross-tenant order ID")
        self.assertIn("no pertenece al restaurante", result["error"])

        self.insumo_b.refresh_from_db()
        self.assertEqual(self.insumo_b.stock_actual, Decimal("40.000"))

    def test_adv_08_legacy_route_with_tenant_a_header_rejected(self):
        """
        Attack 1.8: Calling legacy route (/pedidos/<id>/confirmar/) while Tenant A is active via X-Tenant-Slug.
        Assert: Scoped to Tenant A, raising 404 for Tenant B's order. Stock untouched.
        """
        resp = self.client.post(
            f"/pedidos/{self.orden_b.id}/confirmar/",
            HTTP_X_TENANT_SLUG=self.tenant_a.slug
        )
        self.assertEqual(resp.status_code, 404)

        self.insumo_b.refresh_from_db()
        self.assertEqual(self.insumo_b.stock_actual, Decimal("40.000"))


class TestAdversarialCrossTenantDishInjection(TestCase):
    """
    Dimension 2: Cross-tenant dish, combo, and recipe injection attacks.
    Tenant A attempts to introduce Tenant B's catalog items into its orders, combos, or recipes.
    """

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_adv_inject", password="password123", email="inject@test.com"
        )
        self.client.force_login(self.admin_user)

        self.tenant_a = Restaurante.objects.create(nombre="Tenant A", slug="tenant-a", activo=True)
        self.tenant_b = Restaurante.objects.create(nombre="Tenant B", slug="tenant-b", activo=True)

        self.insumo_a = Insumo.objects.create(
            restaurante=self.tenant_a, codigo="INS-A", nombre="Insumo A", unidad_medida="kg",
            stock_actual=Decimal("100.000"), stock_minimo=Decimal("10.000")
        )
        self.insumo_b = Insumo.objects.create(
            restaurante=self.tenant_b, codigo="INS-B", nombre="Insumo B", unidad_medida="kg",
            stock_actual=Decimal("100.000"), stock_minimo=Decimal("10.000")
        )

        self.plato_a = Plato.objects.create(restaurante=self.tenant_a, nombre="Plato A", valor=5000.0)
        self.plato_b = Plato.objects.create(restaurante=self.tenant_b, nombre="Plato B", valor=8000.0)

        RecetaItem.objects.create(
            restaurante=self.tenant_a, plato=self.plato_a, insumo=self.insumo_a, cantidad=Decimal("0.500")
        )
        RecetaItem.objects.create(
            restaurante=self.tenant_b, plato=self.plato_b, insumo=self.insumo_b, cantidad=Decimal("0.500")
        )

        self.combo_b = ComboMenu.objects.create(
            restaurante=self.tenant_b, nombre="Combo B", precio_menus=7500.0
        )
        self.combo_b.platos.add(self.plato_b)

    def test_adv_21_order_creation_with_foreign_dish_json_rejected(self):
        """
        Attack 2.1: Tenant A orders foreign dish belonging to Tenant B via JSON POS payload.
        Assert: HTTP 400 rejected, zero orders created, zero stock deducted.
        """
        payload = {
            "cliente": "Adversary A",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [
                {"tipo": "plato", "id": self.plato_b.id, "cantidad": 2}
            ]
        }
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400, "Must reject foreign dish injection with 400")
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("no existe", data["message"])

        # Verify no order created
        self.assertEqual(Orden.objects.filter(cliente="Adversary A").count(), 0)

        # Verify victim stock is intact
        self.insumo_b.refresh_from_db()
        self.assertEqual(self.insumo_b.stock_actual, Decimal("100.000"))

    def test_adv_22_trojan_mixed_order_atomic_rollback(self):
        """
        Attack 2.2: Tenant A attempts a trojan order mixing a valid Plato A with an invalid Plato B.
        Assert: Entire order is rejected atomically with 400. Zero partial orders created.
        """
        payload = {
            "cliente": "Trojan Adversary",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [
                {"tipo": "plato", "id": self.plato_a.id, "cantidad": 1},
                {"tipo": "plato", "id": self.plato_b.id, "cantidad": 1}
            ]
        }
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Orden.objects.filter(cliente="Trojan Adversary").count(), 0)

        # Verify neither tenant had stock deducted
        self.insumo_a.refresh_from_db()
        self.assertEqual(self.insumo_a.stock_actual, Decimal("100.000"))
        self.insumo_b.refresh_from_db()
        self.assertEqual(self.insumo_b.stock_actual, Decimal("100.000"))

    def test_adv_23_order_creation_with_foreign_combo_rejected(self):
        """
        Attack 2.3: Tenant A orders a combo belonging to Tenant B.
        Assert: HTTP 400 rejected, zero orders created.
        """
        payload = {
            "cliente": "Adversary Combo",
            "canal_venta": "Local",
            "tipo_pago": "Efectivo",
            "items": [
                {"tipo": "menu", "id": self.combo_b.id, "cantidad": 1}
            ]
        }
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("no existe", data["message"])
        self.assertEqual(Orden.objects.filter(cliente="Adversary Combo").count(), 0)

    def test_adv_24_legacy_format_foreign_dish_injection_rejected(self):
        """
        Attack 2.4: Tenant A injects foreign dish via legacy platos payload.
        Assert: Rejected in both AJAX (HTTP 400) and standard form POST (HTTP 302 redirect),
        with zero orders created.
        """
        payload = {
            "cliente": "Legacy Adversary",
            "platos": json.dumps({str(self.plato_b.id): {"cantidad": 2}})
        }
        # 1. AJAX request returns HTTP 400
        resp_ajax = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=payload,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        self.assertEqual(resp_ajax.status_code, 400)
        self.assertFalse(resp_ajax.json()["success"])

        # 2. Standard form POST returns HTTP 302 redirect to POS
        resp_form = self.client.post(
            f"/r/{self.tenant_a.slug}/pedidos/crear/",
            data=payload
        )
        self.assertEqual(resp_form.status_code, 302)

        # In both cases, zero orders created
        self.assertEqual(Orden.objects.filter(cliente="Legacy Adversary").count(), 0)

    def test_adv_25_combo_creation_with_foreign_dish_rejected(self):
        """
        Attack 2.5: Tenant A calls /r/<slug>/menu/guardar/ attempting to create a Combo with Tenant B's dish.
        Assert: HTTP 400 rejected ("Platos no pertenecen al restaurante.").
        """
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/menu/guardar/",
            data={
                "nombre": "Combo Contaminado",
                "precio_menus": "9990",
                "platos": [str(self.plato_a.id), str(self.plato_b.id)]
            }
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("no pertenecen al restaurante", data["message"])
        self.assertEqual(ComboMenu.objects.filter(nombre="Combo Contaminado").count(), 0)

    def test_adv_26_direct_model_combo_cross_tenant_validation(self):
        """
        Attack 2.6: Direct ORM attempt to add Tenant B dish to Tenant A Combo.
        Assert: clean() raises ValidationError with cross-tenant contamination message.
        """
        combo_a = ComboMenu(restaurante=self.tenant_a, nombre="Combo Directo", precio_menus=5000.0)
        combo_a.save()
        combo_a.platos.add(self.plato_b)

        with self.assertRaises(ValidationError) as ctx:
            combo_a.clean()
        self.assertIn("Contaminación cross-tenant detectada", str(ctx.exception))

    def test_adv_27_recipe_ingredient_foreign_insumo_rejected(self):
        """
        Attack 2.7: Tenant A attempts to add Tenant B's Insumo to Plato A's recipe via HTTP.
        Assert: Operation rejected (status 404 or 500 error catch), success=False,
        and RecetaItem is NEVER created.
        """
        resp = self.client.post(
            f"/r/{self.tenant_a.slug}/plato/{self.plato_a.id}/receta/guardar/",
            data=json.dumps({"insumo_id": self.insumo_b.id, "cantidad": "0.500"}),
            content_type="application/json"
        )
        # Note: Broad except Exception in guardar_ingrediente_receta catches Http404 and returns 500
        self.assertIn(resp.status_code, [404, 500], "Must reject foreign insumo injection")
        data = resp.json()
        self.assertFalse(data.get("success"))
        self.assertEqual(RecetaItem.objects.filter(plato=self.plato_a, insumo=self.insumo_b).count(), 0)

    def test_adv_28_direct_model_receta_and_ordenitem_cross_tenant_validation(self):
        """
        Attack 2.8: Direct ORM defense-in-depth on RecetaItem and OrdenItem models.
        Assert: Both raise ValidationError upon cross-tenant assignment.
        """
        # RecetaItem cross-tenant check
        receta_bad = RecetaItem(
            restaurante=self.tenant_a,
            plato=self.plato_a,
            insumo=self.insumo_b,
            cantidad=Decimal("0.100")
        )
        with self.assertRaises(ValidationError) as ctx:
            receta_bad.clean()
        self.assertIn("Contaminación cross-tenant detectada", str(ctx.exception))

        # OrdenItem cross-tenant check
        orden_a = Orden.objects.create(restaurante=self.tenant_a, cliente="Test ORM")
        item_bad = OrdenItem(orden=orden_a, plato=self.plato_b, cantidad=1)
        with self.assertRaises(ValidationError) as ctx:
            item_bad.clean()
        self.assertIn("Contaminación cross-tenant detectada", str(ctx.exception))


class TestAdversarialMultiThreadedConcurrency(TransactionTestCase):
    """
    Dimension 3: Multi-threaded concurrency attack.
    10 threads switching tenants simultaneously to verify zero ContextVar leakage,
    zero ORM query pollution, and clean ContextVar resets under concurrency.
    """

    def setUp(self):
        # 3 distinct tenants
        self.tenants = [
            Restaurante.objects.create(nombre=f"Tenant Conc {i}", slug=f"tenant-conc-{i}", activo=True)
            for i in range(3)
        ]

        # Populate each tenant with 3 unique dishes
        self.dishes_by_tenant = {}
        for tenant in self.tenants:
            dishes = [
                Plato.objects.create(
                    restaurante=tenant,
                    nombre=f"Plato {tenant.slug} #{j}",
                    valor=1000.0 * (j + 1)
                )
                for j in range(3)
            ]
            self.dishes_by_tenant[tenant.id] = set(d.id for d in dishes)

    def test_adv_31_10_threads_simultaneous_contextvar_switching(self):
        """
        Attack 3.1: 10 concurrent threads rapidly switching ContextVar tenant contexts.
        Each thread runs 30 iterations switching between tenants.
        Assert:
        - get_current_tenant() strictly matches selected tenant inside context.
        - Plato.objects.all() returns ONLY dishes for the active tenant (zero cross-tenant dishes).
        - get_current_tenant() is strictly None after exiting context.
        - Zero exceptions or context corruptions across all 10 threads.
        """
        num_threads = 10
        iterations_per_thread = 30
        errors = []
        barrier = threading.Barrier(num_threads)

        def worker_task(thread_idx):
            try:
                # Synchronize start across all threads
                barrier.wait(timeout=10)

                for it in range(iterations_per_thread):
                    # Pick a tenant
                    target_tenant = self.tenants[(thread_idx + it) % len(self.tenants)]
                    expected_dish_ids = self.dishes_by_tenant[target_tenant.id]

                    with tenant_context(target_tenant):
                        # 1. Assert ContextVar inside thread
                        active = get_current_tenant()
                        if active is None or active.id != target_tenant.id:
                            errors.append(
                                f"[Thread {thread_idx}, Iter {it}] ContextVar mismatch: "
                                f"expected {target_tenant.slug}, got {getattr(active, 'slug', None)}"
                            )

                        # 2. Query scoped models
                        platos = list(Plato.objects.all())
                        plato_ids = set(p.id for p in platos)

                        # Assert dishes match exactly
                        if plato_ids != expected_dish_ids:
                            errors.append(
                                f"[Thread {thread_idx}, Iter {it}] Dish pollution in {target_tenant.slug}: "
                                f"expected {expected_dish_ids}, got {plato_ids}"
                            )

                        # Verify every single dish has correct tenant
                        for p in platos:
                            if p.restaurante_id != target_tenant.id:
                                errors.append(
                                    f"[Thread {thread_idx}, Iter {it}] Foreign dish {p.id} leaked into {target_tenant.slug}"
                                )

                    # 3. Assert clean reset after context exit
                    post_context = get_current_tenant()
                    if post_context is not None:
                        errors.append(
                            f"[Thread {thread_idx}, Iter {it}] ContextVar leak after exit: {post_context.slug}"
                        )

            except Exception as e:
                errors.append(f"[Thread {thread_idx}] Unhandled exception: {str(e)}")

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Concurrency attack detected failures: {errors}")

    def test_adv_32_10_threads_concurrent_middleware_requests(self):
        """
        Attack 3.2: 10 concurrent threads executing HTTP requests through TenantMiddleware.
        Simulates high-traffic concurrent web server with interleaved tenant URLs.
        Assert:
        - X-Tenant-Slug header matches the requested tenant URL.
        - Body contains only dishes for the requested tenant.
        - ContextVar is completely cleaned up after each request in the thread.
        """
        num_threads = 10
        iterations = 15
        errors = []
        barrier = threading.Barrier(num_threads)
        factory = RequestFactory()

        # Dummy view simulating POS rendering
        def pos_view(request):
            current = get_current_tenant()
            platos = list(Plato.objects.all())
            body_text = f"Tenant: {current.slug}; Platos: {[p.nombre for p in platos]}"
            return HttpResponse(body_text)

        middleware = TenantMiddleware(pos_view)

        def request_worker(thread_idx):
            try:
                barrier.wait(timeout=10)

                for it in range(iterations):
                    target_tenant = self.tenants[(thread_idx + it) % len(self.tenants)]
                    req = factory.get(f"/r/{target_tenant.slug}/pos/")

                    # Process through middleware
                    response = middleware(req)

                    # 1. Assert header
                    tenant_header = response.headers.get("X-Tenant-Slug")
                    if tenant_header != target_tenant.slug:
                        errors.append(
                            f"[Thread {thread_idx}] Header mismatch: expected {target_tenant.slug}, got {tenant_header}"
                        )

                    # 2. Assert body scoping
                    content = response.content.decode("utf-8")
                    if f"Tenant: {target_tenant.slug}" not in content:
                        errors.append(f"[Thread {thread_idx}] Body missing tenant slug")

                    # Check that other tenants' dishes are NOT in content
                    for other_tenant in self.tenants:
                        if other_tenant.id != target_tenant.id:
                            if f"Plato {other_tenant.slug}" in content:
                                errors.append(
                                    f"[Thread {thread_idx}] Leak! Dish from {other_tenant.slug} found in {target_tenant.slug}"
                                )

                    # 3. Assert zero residual ContextVar
                    if get_current_tenant() is not None:
                        errors.append(
                            f"[Thread {thread_idx}] ContextVar residual after middleware: {get_current_tenant()}"
                        )

            except Exception as e:
                errors.append(f"[Thread {thread_idx}] Exception in middleware worker: {str(e)}")

        threads = [threading.Thread(target=request_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Middleware concurrency errors: {errors}")

    def test_adv_33_contextvar_clean_reset_on_unhandled_view_exception(self):
        """
        Attack 3.3: Verify guaranteed ContextVar cleanup when views crash with unhandled exceptions.
        Assert: reset_current_tenant in middleware's finally block always restores context to None.
        """
        factory = RequestFactory()

        def crashing_view(request):
            raise RuntimeError("Unexpected server crash inside view logic")

        middleware = TenantMiddleware(crashing_view)
        req = factory.get(f"/r/{self.tenants[0].slug}/pos/")

        with self.assertRaises(RuntimeError):
            middleware(req)

        # ContextVar MUST be None despite the unhandled exception
        self.assertIsNone(get_current_tenant(), "ContextVar MUST reset even if view crashes")

    def test_adv_34_contextvar_leakage_in_threadpool_reuse(self):
        """
        Attack 3.4: Worker thread reuse in ThreadPoolExecutor (simulating WSGI server worker pool).
        Assert: Reused thread workers never inherit residual ContextVar state from prior tasks.
        """
        pool_size = 4
        total_tasks = 60
        errors = []

        def pooled_task(task_id):
            # 1. At start of task, thread MUST have None as tenant (no residue from previous task)
            initial_state = get_current_tenant()
            if initial_state is not None:
                errors.append(f"[Task {task_id}] Thread started with residual tenant: {initial_state.slug}")

            # 2. Set tenant and do simulated work
            tenant = self.tenants[task_id % len(self.tenants)]
            with tenant_context(tenant):
                active = get_current_tenant()
                if active != tenant:
                    errors.append(f"[Task {task_id}] Context mismatch inside context manager")

            # 3. After exit, context must be strictly cleaned
            post_state = get_current_tenant()
            if post_state is not None:
                errors.append(f"[Task {task_id}] Thread left with residual tenant: {post_state.slug}")

        with ThreadPoolExecutor(max_workers=pool_size) as executor:
            futures = [executor.submit(pooled_task, i) for i in range(total_tasks)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    errors.append(f"Task raised unexpected exception: {e}")

        self.assertEqual(len(errors), 0, f"Thread pool reuse leaked tenant context: {errors}")


class TestAdversarialParameterMatrix(TestCase):
    """
    Dimension 4: Parameter tampering & boundary fuzzing on multi-tenant endpoints.
    Verifies that malformed or malicious inputs are safely rejected.
    """

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin_adv_fuzz", password="password123", email="fuzz@test.com"
        )
        self.client.force_login(self.admin_user)
        self.tenant = Restaurante.objects.create(nombre="Fuzz Tenant", slug="fuzz-tenant", activo=True)

    def test_adv_41_malformed_dish_ids_in_order_creation(self):
        """
        Attack 4.1: Inject malformed, non-numeric, negative, or SQL-injection payloads into dish IDs.
        Assert: HTTP 400 rejected safely without HTTP 500 or SQL crashes.
        """
        malformed_ids = [-1, -9999, "abc", "' OR 1=1--", None, {}, []]

        for bad_id in malformed_ids:
            payload = {
                "cliente": "Fuzzer",
                "items": [{"tipo": "plato", "id": bad_id, "cantidad": 1}]
            }
            resp = self.client.post(
                f"/r/{self.tenant.slug}/pedidos/crear/",
                data=json.dumps(payload),
                content_type="application/json"
            )
            self.assertEqual(
                resp.status_code, 400,
                f"Payload with bad id {bad_id} must return 400, got {resp.status_code}"
            )
            data = resp.json()
            self.assertFalse(data["success"])

