"""
Menu/context_processors.py: Injects tenant-aware URLs and current tenant into template contexts.
"""
from django.urls import reverse


def tenant_navigation(request):
    """
    Provides canonical tenant URLs when inside a tenant context (/r/<slug>/...),
    or falls back to standard reverse routes for legacy compatibility.
    """
    tenant = getattr(request, 'restaurante', None)
    if tenant and getattr(tenant, 'slug', None):
        slug = tenant.slug
        return {
            'current_tenant': tenant,
            'url_pos': f"/r/{slug}/pos/",
            'url_kds': f"/r/{slug}/kds/",
            'url_inventario': f"/r/{slug}/inventario/",
            'url_analisis': f"/r/{slug}/analisis/",
            'url_crud': f"/r/{slug}/crud/",
        }
    return {
        'current_tenant': None,
        'url_pos': reverse('Menu:pedidos_crear'),
        'url_kds': reverse('Menu:inicio'),
        'url_inventario': reverse('Menu:inventario'),
        'url_analisis': reverse('Menu:data_analisis'),
        'url_crud': reverse('Menu:crud'),
    }
