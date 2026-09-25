from django.contrib import admin
from django.utils.html import format_html
from .models import Plato, Orden, Menu, OrdenItem, Insumo, RecetaItem, MovimientoStock

# Inline de Escandallo para PlatoAdmin (F07)
class RecetaItemInline(admin.TabularInline):
    model = RecetaItem
    extra = 1
    fields = ('insumo', 'cantidad')
    autocomplete_fields = ['insumo']

# Configuración del modelo Plato con Escandallo Inline
@admin.register(Plato)
class PlatoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'valor')
    search_fields = ('nombre',)
    ordering = ('nombre',)
    inlines = [RecetaItemInline]

# Configuración del modelo Insumo (F06)
@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'codigo', 'nombre', 'unidad_medida',
        'stock_actual', 'stock_minimo', 'costo_unitario',
        'activo', 'estado_stock'
    )
    list_filter = ('activo', 'unidad_medida')
    search_fields = ('codigo', 'nombre')
    ordering = ('nombre',)
    list_editable = ('stock_minimo', 'costo_unitario', 'activo')

    @admin.display(description="Estado de Stock")
    def estado_stock(self, obj):
        if obj.stock_actual <= obj.stock_minimo:
            return format_html(
                '<span style="color: white; background-color: #dc3545; padding: 3px 8px; border-radius: 4px; font-weight: bold;">Crítico / Bajo</span>'
            )
        return format_html(
            '<span style="color: white; background-color: #28a745; padding: 3px 8px; border-radius: 4px; font-weight: bold;">Normal</span>'
        )

# Configuración del modelo MovimientoStock (Kardex de Solo Lectura - F08)
@admin.register(MovimientoStock)
class MovimientoStockAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'fecha_hora', 'insumo', 'tipo',
        'cantidad', 'stock_anterior', 'stock_nuevo', 'orden'
    )
    list_filter = ('tipo', 'fecha_hora')
    search_fields = ('insumo__nombre', 'insumo__codigo', 'orden__id', 'notas')
    ordering = ('-fecha_hora',)
    readonly_fields = (
        'insumo', 'tipo', 'cantidad',
        'stock_anterior', 'stock_nuevo', 'orden',
        'fecha_hora', 'notas'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

# Configuración del modelo Menu (Combos)
@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'precio_menus', 'precio_real', 'descuento')
    search_fields = ('nombre',)
    filter_horizontal = ('platos',)

# Configuración inline para OrdenItem
class OrdenItemInline(admin.TabularInline):
    model = OrdenItem
    extra = 1

# Configuración del modelo Orden (F09 Extended)
@admin.register(Orden)
class OrdenAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'cliente', 'fecha', 'hora', 'estado',
        'tipo_pago', 'monto_total', 'stock_descontado', 'fecha_completada'
    )
    list_filter = ('estado', 'tipo_pago', 'stock_descontado', 'fecha')
    search_fields = ('cliente', 'id')
    readonly_fields = ('fecha', 'hora', 'monto_total', 'fecha_completada', 'stock_descontado')
    inlines = [OrdenItemInline]
