"""
Menu/context_processors.py: Injects tenant-aware URLs and the active tenant into
every template.

Por directriz de tenancy estricta (Sección 2.A y 4 AGENTS.md), este processor
NO expone listados de restaurantes ni flags de "superadmin global". Únicamente
entrega la información del restaurante al que pertenece la sesión actual.
"""
from django.urls import reverse


def _build_tenant_urls(slug):
    """Construye las URLs canónicas multi-tenant para un slug dado."""
    return {
        "url_pos": f"/r/{slug}/pos/",
        "url_kds": f"/r/{slug}/kds/",
        "url_inventario": f"/r/{slug}/inventario/",
        "url_analisis": f"/r/{slug}/analisis/",
        "url_crud": f"/r/{slug}/crud/",
        "url_cajeros": f"/r/{slug}/cajeros/",
        "url_configuracion": f"/r/{slug}/configuracion/",
    }


def _build_modulos(tenant):
    """Flags de módulos opcionales del tenant (True si no hay tenant en contexto)."""
    return {
        "inventario": bool(getattr(tenant, "modulo_inventario", True)),
        "salones_mesas": bool(getattr(tenant, "modulo_salones_mesas", True)),
        "delivery": bool(getattr(tenant, "modulo_delivery", True)),
        "cajeros": bool(getattr(tenant, "modulo_cajeros", True)),
    }


def tenant_navigation(request):
    """
    Provides canonical tenant URLs when inside a tenant context (/r/<slug>/...),
    or falls back to standard reverse routes for legacy compatibility.

    El contexto solo expone `current_tenant` (restaurante autenticado); nunca
    una lista conmutable de restaurantes.

    También expone `modulos`: mapa de flags de módulos opcionales del tenant
    activo (ausente => habilitado) para que los templates oculten navegación.
    """
    tenant = getattr(request, "restaurante", None)
    modulos = _build_modulos(tenant)

    if tenant and getattr(tenant, "slug", None):
        ctx = {"current_tenant": tenant, "modulos": modulos}
        ctx.update(_build_tenant_urls(tenant.slug))
        return ctx

    return {
        "current_tenant": None,
        "modulos": modulos,
        "url_pos": reverse("Menu:pedidos_crear"),
        "url_kds": reverse("Menu:inicio"),
        "url_inventario": reverse("Menu:inventario"),
        "url_analisis": reverse("Menu:data_analisis"),
        "url_crud": reverse("Menu:crud"),
        "url_cajeros": reverse("Menu:cajeros_admin"),
        "url_configuracion": reverse("Menu:configuracion_general"),
    }
