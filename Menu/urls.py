"""
Menu/urls.py: Multi-Tenant Dual-Routing Architecture (Milestone M6 / Feature F32).
Provides:
1. Canonical tenant routes: /r/<slug>/pos/, /r/<slug>/kds/, /r/<slug>/inventario/
2. Centralized authentication: /login/, /logout/
3. 100% backward-compatible legacy routes without redirection: /, /pedidos/crear/, /inventario/
"""

from functools import wraps
from django.urls import path, include
from . import views

app_name = 'Menu'


def tenant_action(view_func):
    """
    Decorator / Dispatch wrapper for tenant-scoped URL patterns.
    Strips 'slug' kwarg injected by include((tenant_patterns, 'tenant'))
    so underlying views with standard signatures (request, ...) or (request, id)
    execute cleanly without raising TypeError.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        kwargs.pop("slug", None)
        return view_func(request, *args, **kwargs)
    return _wrapped


# ==============================================================================
# 1. CANONICAL MULTI-TENANT ROUTES: /r/<slug>/...
# ==============================================================================
tenant_patterns = [
    # Core Operations
    path('pos/', tenant_action(views.crear_orden), name='tenant_pos'),
    path('kds/', tenant_action(views.inicio), name='tenant_kds'),
    path('inventario/', tenant_action(views.inventario_view), name='tenant_inventario'),
    path('inventario/ajuste/', tenant_action(views.ajustar_stock_view), name='tenant_ajustar_stock'),
    path('inventario/sugerencias-ia/', tenant_action(views.sugerencias_compra_api_view), name='tenant_inventario_sugerencias_ia'),
    path('analisis/', tenant_action(views.data_analisis), name='tenant_data_analisis'),
    path('crud/', tenant_action(views.crud), name='tenant_crud'),

    # Order Management & Flow
    path('pedidos/crear/', tenant_action(views.crear_orden), name='tenant_pedidos_crear'),
    path('pedidos/<int:id>/confirmar/', tenant_action(views.confirmar_orden), name='tenant_confirmar_pedido'),
    path('orden/<int:id>/confirmar/', tenant_action(views.confirmar_orden), name='tenant_orden_confirmar_alias'),
    path('orden/confirmar/<int:id>/', tenant_action(views.confirmar_orden), name='tenant_confirmar_orden'),
    path('pedidos/<int:id>/eliminar/', tenant_action(views.eliminar_orden), name='tenant_eliminar_pedido'),
    path('orden/<int:id>/eliminar/', tenant_action(views.eliminar_orden), name='tenant_orden_eliminar_alias'),
    path('orden/eliminar/<int:id>/', tenant_action(views.eliminar_orden), name='tenant_eliminar_orden'),
    path('eliminar_orden/', tenant_action(views.eliminar_orden), name='tenant_eliminar_orden_action'),
    path('pedidos/<int:id>/ticket/', tenant_action(views.ticket_orden), name='tenant_ticket_orden'),
    path('orden/<int:id>/ticket/', tenant_action(views.ticket_orden), name='tenant_orden_ticket'),

    # AI Forecast REST Endpoint
    path('api/sugerencias-compra/', tenant_action(views.sugerencias_compra_api_view), name='tenant_api_sugerencias_compra'),

    # Recipes, Tiers & Entities
    path('plato/<int:plato_id>/receta/', tenant_action(views.obtener_receta_plato), name='tenant_obtener_receta_plato'),
    path('plato/<int:plato_id>/receta/guardar/', tenant_action(views.guardar_ingrediente_receta), name='tenant_guardar_ingrediente_receta'),
    path('receta/item/<int:item_id>/eliminar/', tenant_action(views.eliminar_ingrediente_receta), name='tenant_eliminar_ingrediente_receta'),
    path('plato/<int:plato_id>/price-tiers/', tenant_action(views.obtener_price_tiers_plato), name='tenant_obtener_price_tiers_plato'),
    path('plato/<int:plato_id>/price-tiers/guardar/', tenant_action(views.guardar_price_tier_plato), name='tenant_guardar_price_tier_plato'),
    path('plato/guardar/', tenant_action(views.guardar_plato), name='tenant_guardar_plato'),
    path('plato/eliminar/', tenant_action(views.eliminar_plato), name='tenant_eliminar_plato'),
    path('menu/guardar/', tenant_action(views.guardar_menu), name='tenant_guardar_menu'),
    path('menu/eliminar/', tenant_action(views.eliminar_menu), name='tenant_eliminar_menu'),
    path('menu/detalles/<int:id>/', tenant_action(views.detalles_menu), name='tenant_detalles_menu'),

    # Delivery Webhook per Tenant
    path('api/delivery/webhook/<str:plataforma>/', tenant_action(views.delivery_webhook_api), name='tenant_delivery_webhook'),
]


# ==============================================================================
# 2. MASTER URL PATTERNS (Dual-Routing Registry)
# ==============================================================================
urlpatterns = [
    # 2.1 Centralized Authentication Portal
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # 2.2 Canonical Tenant Root: /r/<slug>/...
    path('r/<slug:slug>/', include(tenant_patterns)),

    # 2.3 Webhooks (Slugged & Non-Slugged Fallback)
    path('api/delivery/webhook/<slug:slug>/<str:plataforma>/', views.delivery_webhook_api, name='delivery_webhook_tenant'),
    path('api/delivery/webhook/<str:plataforma>/', views.delivery_webhook_api, name='delivery_webhook_api'),

    # 2.4 Backward-Compatible Legacy Routes (Zero-Redirect Fallback)
    path('', views.inicio, name='inicio'),
    path('pedidos/crear/', views.crear_orden, name='pedidos_crear'),
    path('orden/crear/', views.crear_orden, name='crear_orden'),
    path('pedidos/<int:id>/confirmar/', views.confirmar_orden, name='confirmar_pedido'),
    path('orden/confirmar/<int:id>/', views.confirmar_orden, name='confirmar_orden'),
    path('pedidos/<int:id>/eliminar/', views.eliminar_orden, name='eliminar_pedido'),
    path('orden/eliminar/<int:id>/', views.eliminar_orden, name='eliminar_orden'),
    path('pedidos/<int:id>/ticket/', views.ticket_orden, name='ticket_orden'),
    path('orden/<int:id>/ticket/', views.ticket_orden, name='orden_ticket'),
    path('inventario/', views.inventario_view, name='inventario'),
    path('inventario/ajuste/', views.ajustar_stock_view, name='ajustar_stock'),
    path('api/sugerencias-compra/', views.sugerencias_compra_api_view, name='api_sugerencias_compra'),
    path('inventario/sugerencias-ia/', views.sugerencias_compra_api_view, name='inventario_sugerencias_ia'),
    path('data_analisis/', views.data_analisis, name='data_analisis'),
    path('crud/', views.crud, name='crud'),
    path('plato/<int:plato_id>/receta/', views.obtener_receta_plato, name='obtener_receta_plato'),
    path('plato/<int:plato_id>/receta/guardar/', views.guardar_ingrediente_receta, name='guardar_ingrediente_receta'),
    path('receta/item/<int:item_id>/eliminar/', views.eliminar_ingrediente_receta, name='eliminar_ingrediente_receta'),
    path('plato/<int:plato_id>/price-tiers/', views.obtener_price_tiers_plato, name='obtener_price_tiers_plato'),
    path('plato/<int:plato_id>/price-tiers/guardar/', views.guardar_price_tier_plato, name='guardar_price_tier_plato'),
    path('plato/guardar/', views.guardar_plato, name='guardar_plato'),
    path('plato/eliminar/', views.eliminar_plato, name='eliminar_plato'),
    path('menu/guardar/', views.guardar_menu, name='guardar_menu'),
    path('menu/eliminar/', views.eliminar_menu, name='eliminar_menu'),
    path('menu/detalles/<int:id>/', views.detalles_menu, name='detalles_menu'),
]
