"""
Multi-Tenant Isolation Middleware for MainchApp.
Enforces per-tenant scoping via ContextVars and sets anti-CDN cache poisoning headers.
"""
import re
import logging
from urllib.parse import urlparse
from django.http import Http404
from Menu.models import Restaurante
from Menu.tenant_context import set_current_tenant, reset_current_tenant

logger = logging.getLogger(__name__)

TENANT_URL_REGEX = re.compile(r"^/r/(?P<slug>[a-zA-Z0-9_-]+)(?P<rest>/.*)?$")


class TenantMiddleware:
    """
    Resolves the active tenant for each request using a strict 5-tier hierarchy:
    1. Canonical URL slug: /r/<slug>/... (Strict: raises Http404 if invalid/inactive)
    2. Custom header: X-Tenant-Slug (For REST / AJAX clients)
    3. Referer inspection: extracts /r/<slug>/... to prevent multi-tab session race conditions
    4. Session attribute: request.session['active_tenant_slug']
    5. Fallback: Base tenant with slug 'mainch'
    
    Guarantees thread-safe ContextVar cleanup in a finally block and injects anti-CDN headers (R3).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        restaurante = None
        match = TENANT_URL_REGEX.match(request.path_info)

        try:
            if match:
                # Tier 1: Canonical URL slug (Absolute Precedence)
                slug = match.group("slug")
                restaurante = Restaurante.objects.filter(slug__iexact=slug, activo=True).first()
                if not restaurante:
                    raise Http404(f"El restaurante '{slug}' no existe o se encuentra inactivo.")

                # Synchronize session with active slug
                if hasattr(request, "session"):
                    request.session["active_tenant_slug"] = restaurante.slug
            else:
                # Tier 2: Check custom request header (X-Tenant-Slug)
                header_slug = request.headers.get("X-Tenant-Slug") or request.META.get("HTTP_X_TENANT_SLUG")
                if header_slug:
                    restaurante = Restaurante.objects.filter(slug__iexact=header_slug.strip(), activo=True).first()

                # Tier 3: Check Referer header (prevents multi-tab session overwrites on naked endpoints)
                if not restaurante:
                    referer = request.headers.get("Referer") or request.META.get("HTTP_REFERER")
                    if referer:
                        try:
                            ref_path = urlparse(referer).path
                            ref_match = TENANT_URL_REGEX.match(ref_path)
                            if ref_match:
                                ref_slug = ref_match.group("slug")
                                restaurante = Restaurante.objects.filter(slug__iexact=ref_slug, activo=True).first()
                        except Exception:
                            pass

                # Tier 4: Check Session
                if not restaurante and hasattr(request, "session"):
                    session_slug = request.session.get("active_tenant_slug")
                    if session_slug:
                        restaurante = Restaurante.objects.filter(slug__iexact=session_slug, activo=True).first()

                # Tier 5: Fallback to default tenant 'mainch'
                if not restaurante:
                    restaurante = Restaurante.objects.filter(slug="mainch").first()
                    if not restaurante:
                        restaurante, _ = Restaurante.objects.get_or_create(
                            slug="mainch",
                            defaults={"nombre": "Mainch", "direccion": "Valparaíso, Chile", "activo": True}
                        )
        except Http404:
            raise
        except Exception as exc:
            logger.error("Error resolving tenant in TenantMiddleware (possible unmigrated database): %s", exc)
            restaurante = None

        # Attach to request and activate ContextVar
        request.restaurante = restaurante
        request.tenant = restaurante
        token = set_current_tenant(restaurante)

        try:
            response = self.get_response(request)
        finally:
            # Guaranteed cleanup even if view raises an uncaught exception
            reset_current_tenant(token)

        # R3: Anti-CDN Cache Mitigation for Vercel / Edge Proxies
        # Prevent caching of dynamic, authenticated, POS, KDS, or tenant-scoped pages
        if (
            request.path_info.startswith("/r/")
            or "/api/" in request.path_info
            or request.path_info.startswith("/inventario")
            or request.path_info.startswith("/data_analisis")
            or request.path_info.startswith("/crud")
            or (hasattr(request, "user") and request.user.is_authenticated)
        ):
            response["Cache-Control"] = "private, no-store, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            response["Surrogate-Control"] = "no-store"
            existing_vary = response.get("Vary", "")
            vary_tokens = {v.strip() for v in existing_vary.split(",") if v.strip()}
            vary_tokens.update(["Cookie", "Authorization", "X-Requested-With", "X-Tenant-Slug"])
            response["Vary"] = ", ".join(sorted(vary_tokens))

        if restaurante:
            response["X-Tenant-Slug"] = restaurante.slug

        return response
