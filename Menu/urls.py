from django.urls import path
from . import views

app_name = 'Menu'

urlpatterns = [
    path("", views.inicio, name="inicio"),

    # Rutas para crear pedidos (Dual: legacy 'orden' + test/REST 'pedidos')
    path("pedidos/crear/", views.crear_orden, name="pedidos_crear"),
    path("orden/crear/", views.crear_orden, name="crear_orden"),

    # Rutas para confirmar pedidos (Dual: legacy 'orden' + test/REST 'pedidos')
    path("pedidos/<int:id>/confirmar/", views.confirmar_orden, name="confirmar_pedido"),
    path("orden/confirmar/<int:id>/", views.confirmar_orden, name="confirmar_orden"),

    # Rutas para eliminar pedidos (Dual)
    path("pedidos/<int:id>/eliminar/", views.eliminar_orden, name="eliminar_pedido"),
    path("orden/eliminar/<int:id>/", views.eliminar_orden, name="eliminar_orden"),

    # Rutas de tickets comandas (F04)
    path("pedidos/<int:id>/ticket/", views.ticket_orden, name="ticket_orden"),
    path("orden/<int:id>/ticket/", views.ticket_orden, name="orden_ticket"),

    # Rutas de Autenticación Admin
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Rutas de Gestión de Recetas / Escandallos (Dueño)
    path("plato/<int:plato_id>/receta/", views.obtener_receta_plato, name="obtener_receta_plato"),
    path("plato/<int:plato_id>/receta/guardar/", views.guardar_ingrediente_receta, name="guardar_ingrediente_receta"),
    path("receta/item/<int:item_id>/eliminar/", views.eliminar_ingrediente_receta, name="eliminar_ingrediente_receta"),

    # Rutas CRUD y Analítica
    path("crud/", views.crud, name="crud"),
    path('plato/guardar/', views.guardar_plato, name='guardar_plato'),
    path('plato/eliminar/', views.eliminar_plato, name='eliminar_plato'),
    path('menu/eliminar/', views.eliminar_menu, name='eliminar_menu'),
    path('menu/detalles/<int:id>/', views.detalles_menu, name='detalles_menu'),
    path('menu/guardar/', views.guardar_menu, name='guardar_menu'),
    path("data_analisis/", views.data_analisis, name="data_analisis"),

    # Rutas de Inventario y Alertas (F18)
    path("inventario/", views.inventario_view, name="inventario"),
    path("inventario/ajuste/", views.ajustar_stock_view, name="ajustar_stock"),

    # Rutas de Previsión IA y Órdenes de Compra (F23)
    path("api/sugerencias-compra/", views.sugerencias_compra_api_view, name="api_sugerencias_compra"),
    path("inventario/sugerencias-ia/", views.sugerencias_compra_api_view, name="inventario_sugerencias_ia"),
]

