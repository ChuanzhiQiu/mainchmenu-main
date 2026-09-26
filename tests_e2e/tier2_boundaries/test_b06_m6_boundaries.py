"""
Tier 2 — Boundaries & Corner Cases: Milestone 6 (Multi-Tenant Architecture & Slug Routing).
Author: challenger_m6_2
Covers:
1. SKU composite uniqueness stress across 5 distinct tenants with identical SKU codes.
2. Intra-tenant duplicate SKU prevention (model validation and DB IntegrityError).
3. Routing boundaries: nonexistent and inactive tenant slug requests (assert 404).
4. Routing slug case-insensitivity (/r/MAINCH/pos/ vs /r/mainch/pos/).
5. Anti-CDN cache headers on dynamic and tenant-scoped endpoints.
6. Public caching retention for WhiteNoise static assets.
7. Legacy URL preservation without HTTP redirects (/, /pedidos/crear/, /inventario/).
8. Delivery webhook routing boundary analysis.
"""

from decimal import Decimal
import json
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.test import Client
from tests_e2e.base import E2ETestCase
from whitenoise.middleware import WhiteNoiseMiddleware
from whitenoise.base import WhiteNoise


class TestB06M6Boundaries(E2ETestCase):
    """Boundary and stress test cases for Milestone 6 (Multi-Tenant Core Architecture & Slug Routing)."""

    def setUp(self):
        self.Restaurante = self.require_model("Menu", "Restaurante", feature_id="F29-F32")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="F29-F32")
        self.Plato = self.require_model("Menu", "Plato", feature_id="F29-F32")
        self.client = self.get_client()

        # Ensure base tenant exists
        self.mainch, _ = self.Restaurante.objects.get_or_create(
            slug="mainch",
            defaults={"nombre": "Mainch Principal", "activo": True}
        )
        if not self.mainch.activo:
            self.mainch.activo = True
            self.mainch.save()

    # ==============================================================================
    # 1. SKU COMPOSITE UNIQUENESS STRESS
    # ==============================================================================
    def test_b06_01_sku_composite_uniqueness_across_5_tenants(self):
        """TC-B06-01: Identical SKUs ('INS-TEST-01', 'INS-PAN') coexist across 5 distinct tenants."""
        tenants = []
        for i in range(1, 6):
            t, _ = self.Restaurante.objects.get_or_create(
                slug=f"boundary-tenant-{i}",
                defaults={"nombre": f"Boundary Tenant {i}", "activo": True}
            )
            tenants.append(t)

        created_skus = []
        for t in tenants:
            ins1 = self.Insumo.objects.create(
                restaurante=t,
                codigo="INS-TEST-01",
                nombre=f"Insumo 01 {t.slug}",
                unidad_medida="kg",
                stock_actual=Decimal("25.000"),
                stock_minimo=Decimal("5.000")
            )
            ins2 = self.Insumo.objects.create(
                restaurante=t,
                codigo="INS-PAN",
                nombre=f"Insumo Pan {t.slug}",
                unidad_medida="un",
                stock_actual=Decimal("100.000"),
                stock_minimo=Decimal("10.000")
            )
            created_skus.extend([ins1, ins2])

        self.assertEqual(len(created_skus), 10, "10 insumos (2 per tenant across 5 tenants) must be created")
        
        # Verify all 5 have INS-TEST-01
        count_01 = self.Insumo.objects.filter(codigo="INS-TEST-01", restaurante__in=tenants).count()
        count_pan = self.Insumo.objects.filter(codigo="INS-PAN", restaurante__in=tenants).count()
        self.assertEqual(count_01, 5, "5 tenants must each hold their own INS-TEST-01")
        self.assertEqual(count_pan, 5, "5 tenants must each hold their own INS-PAN")

    def test_b06_02_intra_tenant_duplicate_sku_rejection(self):
        """TC-B06-02: Duplicate SKU within the same tenant triggers ValidationError and IntegrityError."""
        tenant, _ = self.Restaurante.objects.get_or_create(
            slug="tenant-duplicate-test",
            defaults={"nombre": "Tenant Duplicate Test", "activo": True}
        )
        self.Insumo.objects.create(
            restaurante=tenant,
            codigo="INS-UNIQUE-01",
            nombre="Insumo Original",
            stock_actual=Decimal("10.000")
        )

        # 1. Model layer validation rejection
        dup = self.Insumo(
            restaurante=tenant,
            codigo="INS-UNIQUE-01",
            nombre="Insumo Duplicado"
        )
        with self.assertRaises(ValidationError, msg="Model clean() must raise ValidationError on duplicate SKU in same tenant"):
            dup.full_clean()

        with self.assertRaises(ValidationError, msg="Model save() must raise ValidationError on duplicate SKU in same tenant"):
            dup.save()

        # 2. Database level constraint rejection (bypassing clean/save via bulk_create)
        with transaction.atomic():
            with self.assertRaises(IntegrityError, msg="Database unique constraint must raise IntegrityError on duplicate insert"):
                self.Insumo.objects.bulk_create([dup])

    def test_b06_03_sku_normalization_and_case_collision_prevention(self):
        """TC-B06-03: SKU normalization (whitespace stripping, uppercase) prevents case-collision duplicates."""
        tenant, _ = self.Restaurante.objects.get_or_create(
            slug="tenant-case-test",
            defaults={"nombre": "Tenant Case Test", "activo": True}
        )
        self.Insumo.objects.create(
            restaurante=tenant,
            codigo="  ins-alpha-01  ",
            nombre="Insumo Alpha",
            stock_actual=Decimal("10.000")
        )
        stored = self.Insumo.objects.get(restaurante=tenant, codigo="INS-ALPHA-01")
        self.assertEqual(stored.codigo, "INS-ALPHA-01", "SKU must be normalized to stripped uppercase")

        dup_case = self.Insumo(
            restaurante=tenant,
            codigo="ins-alpha-01",
            nombre="Insumo Alpha Duplicate"
        )
        with self.assertRaises(ValidationError):
            dup_case.full_clean()

    # ==============================================================================
    # 2. ROUTING BOUNDARIES & SLUG RESOLUTION
    # ==============================================================================
    def test_b06_04_routing_nonexistent_tenant_slug_returns_404(self):
        """TC-B06-04: Nonexistent tenant slug in canonical URLs returns HTTP 404."""
        endpoints = [
            "/r/nonexistent-tenant-xyz/pos/",
            "/r/nonexistent-tenant-xyz/kds/",
            "/r/nonexistent-tenant-xyz/inventario/",
            "/r/nonexistent-tenant-xyz/analisis/",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            self.assertEqual(resp.status_code, 404, f"Endpoint '{ep}' must return 404 for nonexistent tenant")

    def test_b06_05_routing_inactive_tenant_slug_returns_404(self):
        """TC-B06-05: Inactive tenant slug in canonical URLs returns HTTP 404."""
        inactive_tenant, _ = self.Restaurante.objects.get_or_create(
            slug="inactivo-boundary",
            defaults={"nombre": "Inactivo Boundary", "activo": False}
        )
        inactive_tenant.activo = False
        inactive_tenant.save()

        endpoints = [
            f"/r/{inactive_tenant.slug}/pos/",
            f"/r/{inactive_tenant.slug}/kds/",
            f"/r/{inactive_tenant.slug}/inventario/",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            self.assertEqual(resp.status_code, 404, f"Endpoint '{ep}' must return 404 for inactive tenant")

    def test_b06_06_routing_slug_case_insensitivity(self):
        """TC-B06-06: Tenant slug resolution is case-insensitive (/r/MAINCH/pos/ vs /r/mainch/pos/)."""
        slug_variants = [
            f"/r/{self.mainch.slug}/pos/",
            f"/r/{self.mainch.slug.upper()}/pos/",
            f"/r/{self.mainch.slug.capitalize()}/pos/",
        ]
        for url in slug_variants:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, f"URL variant '{url}' must resolve with HTTP 200")
            self.assertEqual(resp.headers.get("X-Tenant-Slug"), self.mainch.slug,
                             f"Response header X-Tenant-Slug must canonicalize to '{self.mainch.slug}'")

    # ==============================================================================
    # 3. ANTI-CDN CACHE HEADERS
    # ==============================================================================
    def test_b06_07_anti_cdn_cache_headers_on_dynamic_endpoints(self):
        """TC-B06-07: Dynamic endpoints include strict anti-CDN cache control and Vary headers."""
        dynamic_endpoints = [
            f"/r/{self.mainch.slug}/pos/",
            f"/r/{self.mainch.slug}/kds/",
            f"/r/{self.mainch.slug}/inventario/",
            f"/r/{self.mainch.slug}/analisis/",
            f"/r/{self.mainch.slug}/crud/",
        ]
        for ep in dynamic_endpoints:
            resp = self.client.get(ep)
            self.assertEqual(resp.status_code, 200, f"Dynamic endpoint '{ep}' must return 200")

            cache_control = resp.headers.get("Cache-Control", "")
            self.assertIn("private", cache_control, f"{ep} must declare 'private' in Cache-Control")
            self.assertIn("no-store", cache_control, f"{ep} must declare 'no-store' in Cache-Control")
            self.assertIn("must-revalidate", cache_control, f"{ep} must declare 'must-revalidate' in Cache-Control")
            self.assertIn("max-age=0", cache_control, f"{ep} must declare 'max-age=0' in Cache-Control")

            self.assertEqual(resp.headers.get("Pragma"), "no-cache", f"{ep} must declare Pragma: no-cache")
            self.assertEqual(resp.headers.get("Surrogate-Control"), "no-store", f"{ep} must declare Surrogate-Control: no-store")

            vary = resp.headers.get("Vary", "")
            self.assertIn("X-Tenant-Slug", vary, f"{ep} Vary header must include X-Tenant-Slug")
            self.assertIn("Cookie", vary, f"{ep} Vary header must include Cookie")

    def test_b06_08_delivery_webhook_anti_cdn_cache_headers(self):
        """TC-B06-08: Webhook endpoints retain anti-CDN cache headers."""
        resp = self.client.post(
            "/api/delivery/webhook/ubereats/",
            data=json.dumps({"test": 1}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        cache_control = resp.headers.get("Cache-Control", "")
        self.assertIn("private", cache_control)
        self.assertIn("no-store", cache_control)
        self.assertEqual(resp.headers.get("Pragma"), "no-cache")

    def test_b06_09_static_files_retain_public_caching_in_whitenoise(self):
        """TC-B06-09: Static files served by WhiteNoise retain public caching and are not marked no-store."""
        wn = WhiteNoiseMiddleware()
        self.assertTrue(hasattr(wn, "files"), "WhiteNoiseMiddleware must contain files dictionary")
        self.assertGreater(len(wn.files), 0, "WhiteNoise should have indexed static assets")

        # Pick an existing static asset from WhiteNoise index
        static_path = next(iter(wn.files.keys()))
        environ = {"PATH_INFO": static_path}
        res_headers = {}

        def start_response(status, headers):
            res_headers.update(dict(headers))

        WhiteNoise.__call__(wn, environ, start_response)
        cache_control = res_headers.get("Cache-Control", "")
        self.assertIn("public", cache_control, f"Static asset {static_path} must retain public caching")
        self.assertNotIn("no-store", cache_control, f"Static asset {static_path} must not be marked no-store")

    # ==============================================================================
    # 4. BACKWARD COMPATIBILITY: LEGACY URL PRESERVATION
    # ==============================================================================
    def test_b06_10_legacy_urls_return_200_without_redirect(self):
        """TC-B06-10: Legacy endpoints (/, /pedidos/crear/, /inventario/) return 200 without redirect."""
        # 1. Unauthenticated client on public legacy endpoints
        anon_client = Client()
        resp_root = anon_client.get("/")
        self.assertEqual(resp_root.status_code, 200, "Root legacy route '/' must return 200")
        self.assertFalse(hasattr(resp_root, "url"), "Root '/' must not redirect")

        resp_pos = anon_client.get("/pedidos/crear/")
        self.assertEqual(resp_pos.status_code, 200, "Legacy POS route '/pedidos/crear/' must return 200")
        self.assertFalse(hasattr(resp_pos, "url"), "Legacy POS must not redirect")

        # 2. Authenticated client on legacy inventory endpoint
        resp_inv = self.client.get("/inventario/")
        self.assertEqual(resp_inv.status_code, 200, "Legacy inventory route '/inventario/' must return 200 for staff")
        self.assertFalse(hasattr(resp_inv, "url"), "Legacy inventory must not redirect when authenticated")

    # ==============================================================================
    # 5. DELIVERY WEBHOOK ROUTING BOUNDARY ANALYSIS
    # ==============================================================================
    def test_b06_11_canonical_tenant_webhook_succeeds(self):
        """TC-B06-11: Canonical tenant webhook /r/<slug>/api/delivery/webhook/<plataforma>/ strips slug and runs."""
        resp = self.client.post(
            f"/r/{self.mainch.slug}/api/delivery/webhook/ubereats/",
            data=json.dumps({"test": 1}),
            content_type="application/json"
        )
        # Should return 400 (missing order ID payload), not 500 TypeError
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("Payload sin identificador", data["message"])

    def test_b06_12_slugged_webhook_unwrapped_kwarg_edge_case(self):
        """TC-B06-12: Documents routing boundary flaw in /api/delivery/webhook/<slug>/<plataforma>/ (Menu/urls.py:87)."""
        # In Menu/urls.py:87, 'api/delivery/webhook/<slug:slug>/<str:plataforma>/' was not wrapped with tenant_action.
        # Calling this endpoint passes slug as a kwarg to delivery_webhook_api, raising TypeError.
        with self.assertRaises(TypeError):
            self.client.post(
                f"/api/delivery/webhook/{self.mainch.slug}/ubereats/",
                data=json.dumps({"test": 1}),
                content_type="application/json"
            )

