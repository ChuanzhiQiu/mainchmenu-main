"""
Menu/context_processors.py: Injects tenant-aware URLs, active tenant and the
tenant switcher context (for global admins) into every template.
"""
from django.urls import reverse

from Menu.models import Restaurante


def _build_tenant_urls(slug):
    """Construye las URLs canónicas multi-tenant para un slug dado."""
    return {
        "url_pos": f"/r/{slug}/pos/",
        "url_kds": f"/r/{slug}/kds/",
        "url_inventario": f"/r/{slug}/inventario/",
        "url_analisis": f"/r/{slug}/analisis/",
        "url_crud": f"/r/{slug}/crud/",
        "url_cajeros": f"/r/{slug}/cajeros/",
    }


def tenant_navigation(request):
    """
    Provides canonical tenant URLs when inside a tenant context (/r/<slug>/...),
    or falls back to standard reverse routes for legacy compatibility.

    Additionally exposes:
    - `es_global_admin`: True when the request user is an authenticated staff/superuser
      (global admin) who may switch between tenants.
    - `restaurantes_disponibles`: active tenants for the tenant switcher UI.
    """
    tenant = getattr(request, "restaurante", None)

    es_global_admin = bool(
        hasattr(request, "user")
        and request.user.is_authenticated
        and (request.user.is_staff or request.user.is_superuser)
    )

    # Listado de tenants activos para el switch (solo relevante para admin global).
    restaurantes_disponibles = []
    if es_global_admin:
        restaurantes_disponibles = list(
            Restaurante.objects.filter(activo=True).order_by("nombre").values("slug", "nombre")
        )

    if tenant and getattr(tenant, "slug", None):
        ctx = {
            "current_tenant": tenant,
            "es_global_admin": es_global_admin,
            "restaurantes_disponibles": restaurantes_disponibles,
        }
        ctx.update(_build_tenant_urls(tenant.slug))
        return ctx

    return {
        "current_tenant": None,
        "es_global_admin": es_global_admin,
        "restaurantes_disponibles": restaurantes_disponibles,
        "url_pos": reverse("Menu:pedidos_crear"),
        "url_kds": reverse("Menu:inicio"),
        "url_inventario": reverse("Menu:inventario"),
        "url_analisis": reverse("Menu:data_analisis"),
        "url_crud": reverse("Menu:crud"),
        "url_cajeros": reverse("Menu:cajeros_admin"),
    }
