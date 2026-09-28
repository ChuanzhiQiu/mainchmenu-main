"""
Multi-Tenant Isolation Middleware for MainchApp.
Enforces per-tenant scoping via ContextVars and sets anti-CDN cache poisoning headers.

Arquitectura de autenticación en dos niveles:
- Nivel 1 (Sesión de Restaurante): el tenant activo proviene de
  `request.session['restaurante_id']`, establecida por `login_restaurante`.
- Nivel 2 (Elevación a Administrador): no modifica el tenant activo; el
  administrador solo se valida contra el restaurante de la sesión en `login_admin`.

No existe fallback anónimo a 'mainch' ni derivación de tenant desde la cuenta
de administrador (evita fusionar sesión de restaurante con cuenta admin).
"""
import re
import logging
from urllib.parse import urlparse
from django.http import Http404, HttpResponseForbidden
from Menu.models import Restaurante
from Menu.tenant_context import set_current_tenant, reset_current_tenant

logger = logging.getLogger(__name__)

TENANT_URL_REGEX = re.compile(r"^/r/(?P<slug>[a-zA-Z0-9_-]+)(?P<rest>/.*)?$")


class TenantMiddleware:
    """
    Resuelve el restaurante activo con la siguiente jerarquía estricta:
    1. Sesión de restaurante validada (`restaurante_id`).
    2. URL canónica: /r/<slug>/... (para API/DRF y rutas tenant explícitas).
    3. Header X-Tenant-Slug (clientes REST/AJAX).
    4. Referer con /r/<slug>/... (previene carreras multi-pestaña).
    5. Atributo legacy de sesión `active_tenant_slug`.

    No se deriva tenant desde `request.user` (PerfilAdministrador) ni se cae a
    'mainch'. Garantiza limpieza de ContextVar en `finally` y headers anti-CDN.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        restaurante = None
        session_tenant = None
        match = TENANT_URL_REGEX.match(request.path_info)

        try:
            # Tier 1: Sesión de restaurante validada (Nivel 1).
            if hasattr(request, "session"):
                restaurante_id = request.session.get("restaurante_id")
                if restaurante_id:
                    restaurante = Restaurante.objects.filter(
                        id=restaurante_id, activo=True
                    ).first()
                    if not restaurante:
                        # Sesión inválida: purgar para evitar contexto fantasma.
                        request.session.pop("restaurante_id", None)
                    else:
                        session_tenant = restaurante

            # Tier 2: URL canónica /r/<slug>/...
            if not restaurante and match:
                slug = match.group("slug")
                restaurante = Restaurante.objects.filter(
                    slug__iexact=slug, activo=True
                ).first()
                if not restaurante:
                    raise Http404(
                        f"El restaurante '{slug}' no existe o se encuentra inactivo."
                    )

            # Tier 3: Header X-Tenant-Slug.
            if not restaurante:
                header_slug = request.headers.get("X-Tenant-Slug") or request.META.get(
                    "HTTP_X_TENANT_SLUG"
                )
                if header_slug:
                    restaurante = Restaurante.objects.filter(
                        slug__iexact=header_slug.strip(), activo=True
                    ).first()

            # Tier 4: Referer con /r/<slug>/...
            if not restaurante:
                referer = request.headers.get("Referer") or request.META.get("HTTP_REFERER")
                if referer:
                    try:
                        ref_path = urlparse(referer).path
                        ref_match = TENANT_URL_REGEX.match(ref_path)
                        if ref_match:
                            ref_slug = ref_match.group("slug")
                            restaurante = Restaurante.objects.filter(
                                slug__iexact=ref_slug, activo=True
                            ).first()
                    except Exception:
                        pass

            # Tier 5: Atributo legacy de sesión (compatibilidad).
            if not restaurante and hasattr(request, "session"):
                session_slug = request.session.get("active_tenant_slug")
                if session_slug:
                    restaurante = Restaurante.objects.filter(
                        slug__iexact=session_slug, activo=True
                    ).first()

            # NOTA: no hay fallback a 'mainch' ni derivación desde request.user.
        except Http404:
            raise
        except Exception as exc:
            logger.error(
                "Error resolving tenant in TenantMiddleware (possible unmigrated database): %s",
                exc,
            )
            restaurante = None

        # =====================================================================
        # Guard de tenancy estricta por sesión de restaurante:
        # Una sesión de restaurante no puede navegar a un slug canónico ajeno.
        # =====================================================================
        if (
            session_tenant
            and match
            and match.group("slug").lower() != session_tenant.slug.lower()
        ):
            return HttpResponseForbidden(
                "Acceso denegado: la sesión del restaurante no corresponde a este slug."
            )

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
