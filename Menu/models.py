from decimal import Decimal
import math
from django.db import models
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.utils import timezone

# Modelo para representar cada plato de manera individual
class Plato(models.Model):
    nombre = models.CharField(max_length=100)
    valor = models.FloatField()

    def __str__(self):
        return self.nombre

# Modelo para representar una promoción (colaciones, combos, etc.)
class Menu(models.Model):
    nombre = models.CharField(max_length=100)
    platos = models.ManyToManyField(Plato)
    precio_menus = models.FloatField()

    def __str__(self):
        return self.nombre

    @property
    def precio_real(self):
        # Evita calcular si el objeto aún no está guardado
        if not self.pk:
            return 0
        return sum(plato.valor for plato in self.platos.all())

    @property
    def descuento(self):
        return self.precio_real - self.precio_menus

    def clean(self):
        # Solo validar platos si el menú ya tiene un ID (es decir, ha sido guardado previamente)
        if self.pk and not self.platos.exists():
            raise ValidationError("El menú debe contener al menos un plato.")


    def save(self, *args, **kwargs):
        # Llama a clean para realizar la validación antes de guardar
        self.clean()
        super().save(*args, **kwargs)


# Modelo para representar una orden
class Orden(models.Model):
    # Opciones para el estado de la orden
    ESTADO_EN_CURSO = 'En curso'
    ESTADO_COMPLETADA = 'Completada'
    ESTADO_ELIMINADA = 'Eliminada'
    ESTADO_CHOICES = [
        (ESTADO_EN_CURSO, 'En curso'),
        (ESTADO_COMPLETADA, 'Completada'),
        (ESTADO_ELIMINADA, 'Eliminada'),
    ]

    PAGO_CHOICES = [
        ('Efectivo', 'Efectivo'),
        ('Cta.Cte', 'Cuenta Corriente'),
        ('Delivery', 'Delivery'),
        ('Cheque', 'Cheque'),
        ('Junaeb', 'Junaeb'),
        ('Tarj. Crédito', 'Tarjeta de Crédito'),
        ('Tarj. Débito', 'Tarjeta de Débito'),
        ('Transferencia', 'Transferencia'),
    ]
    
    CANAL_CHOICES = [
        ('Local','Local'),
        ('Whatsapp','Whatsapp'),
        ('Delivery', 'Delivery'),
    ]

    cliente = models.CharField(max_length=40)
    fecha = models.DateField(auto_now_add=True)
    hora = models.TimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_EN_CURSO)
    tipo_pago = models.CharField(max_length=20, choices=PAGO_CHOICES, blank=True, null=True)
    canal_venta = models.CharField(max_length=20, choices=CANAL_CHOICES, default='Local')
    monto_total = models.FloatField(default=0.0)
    descuento = models.FloatField(default=0.0, blank=True)
    stock_descontado = models.BooleanField(
        default=False,
        verbose_name="Stock Descontado",
        help_text="Bandera de idempotencia: True si los insumos de la orden ya fueron deducidos del inventario."
    )
    fecha_completada = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha Completada",
        help_text="Timestamp exacto en que la orden pasó a estado Completada y descontó stock."
    )

    def __str__(self):
        return f"Orden {self.id} - {self.cliente} ({self.estado})"

    def calcular_total(self):
        subtotal = sum(item.subtotal for item in self.items.all())
        try:
            raw_desc = float(self.descuento or 0.0)
            if math.isnan(raw_desc) or math.isinf(raw_desc) or raw_desc < 0.0:
                descuento = 0.0
            else:
                descuento = raw_desc
        except (ValueError, TypeError):
            descuento = 0.0
        total_con_descuento = subtotal - descuento
        return max(round(total_con_descuento, 2), 0.0)

    def save(self, *args, **kwargs):
        if self.descuento is None:
            self.descuento = 0.0
        else:
            try:
                val = float(self.descuento)
                if math.isnan(val) or math.isinf(val) or val < 0.0:
                    self.descuento = 0.0
                else:
                    self.descuento = val
            except (ValueError, TypeError):
                self.descuento = 0.0
        if self.pk:
            self.monto_total = self.calcular_total() 
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Orden"
        verbose_name_plural = "Órdenes"

# Modelo para representar los ítems de una orden
class OrdenItem(models.Model):
    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='items')
    plato = models.ForeignKey(Plato, on_delete=models.CASCADE, null=True, blank=True)
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, null=True, blank=True)
    cantidad = models.PositiveIntegerField(default=1)

    def __str__(self):
        if self.plato:
            return f"{self.cantidad}x {self.plato.nombre}"
        elif self.menu:
            return f"{self.cantidad}x {self.menu.nombre}"
        return "Item sin plato ni menú"

    @property
    def subtotal(self):
        if self.plato:
            return self.cantidad * self.plato.valor
        elif self.menu:
            return self.cantidad * self.menu.precio_menus
        return 0


# ==============================================================================
# INVENTORY & ESCANDALLO (RECETAS) DATA MODELS - MILESTONE 2
# ==============================================================================

class Insumo(models.Model):
    """
    Representa una materia prima o insumo unitario en el inventario del restaurante.
    Soporta unidades de medida culinarias, stocks de seguridad y costeo unitario.
    """
    UNIDAD_KG = 'kg'
    UNIDAD_G = 'g'
    UNIDAD_LT = 'lt'
    UNIDAD_ML = 'ml'
    UNIDAD_UN = 'un'

    UNIDAD_CHOICES = [
        (UNIDAD_KG, 'Kilogramos (kg)'),
        (UNIDAD_G, 'Gramos (g)'),
        (UNIDAD_LT, 'Litros (lt)'),
        (UNIDAD_ML, 'Mililitros (ml)'),
        (UNIDAD_UN, 'Unidades (un)'),
    ]

    codigo = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Código Insumo",
        help_text="Identificador único o SKU del insumo (ej: INS-001, INS-PAN)."
    )
    nombre = models.CharField(
        max_length=100,
        verbose_name="Nombre del Insumo"
    )
    unidad_medida = models.CharField(
        max_length=10,
        choices=UNIDAD_CHOICES,
        default=UNIDAD_UN,
        verbose_name="Unidad de Medida"
    )
    stock_actual = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name="Stock Actual",
        help_text="Permite valores negativos para continuidad operativa en cocina."
    )
    stock_minimo = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name="Stock Mínimo / Seguridad"
    )
    costo_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0.000'),
        verbose_name="Costo Unitario ($)"
    )
    activo = models.BooleanField(
        default=True,
        verbose_name="Activo",
        help_text="Permite borrado lógico (soft-delete) de insumos descontinuados."
    )

    class Meta:
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"
        ordering = ['nombre']

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

    @property
    def esta_bajo_minimo(self) -> bool:
        """Determina si el insumo está estrictamente bajo el umbral de seguridad (TC-B02-25)."""
        return self.stock_actual < self.stock_minimo


class RecetaItem(models.Model):
    """
    Representa el escandallo (Bill of Materials) vinculando un Plato con un Insumo.
    Define la cantidad requerida de materia prima para preparar una porción del plato.
    """
    plato = models.ForeignKey(
        Plato,
        on_delete=models.CASCADE,
        related_name='receta_items',
        verbose_name="Plato"
    )
    insumo = models.ForeignKey(
        Insumo,
        on_delete=models.CASCADE,
        related_name='receta_items',
        verbose_name="Insumo"
    )
    cantidad = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Cantidad Requerida",
        help_text="Cantidad requerida en la unidad_medida del insumo (ej: 0.150 kg)."
    )

    class Meta:
        verbose_name = "Ítem de Receta / Escandallo"
        verbose_name_plural = "Ítems de Receta / Escandallo"
        unique_together = ('plato', 'insumo')
        constraints = [
            models.UniqueConstraint(
                fields=['plato', 'insumo'],
                name='unique_plato_insumo_receta'
            )
        ]
        ordering = ['plato', 'insumo']

    def __str__(self):
        try:
            plato = self.plato
            plato_nombre = plato.nombre if plato and hasattr(plato, 'nombre') else 'Plato no especificado'
        except (AttributeError, ObjectDoesNotExist):
            plato_nombre = 'Plato no especificado'

        try:
            insumo = self.insumo
            insumo_nombre = insumo.nombre if insumo and hasattr(insumo, 'nombre') else 'Insumo no especificado'
            unidad = f" {insumo.unidad_medida}" if insumo and hasattr(insumo, 'unidad_medida') and insumo.unidad_medida else ""
        except (AttributeError, ObjectDoesNotExist):
            insumo_nombre = 'Insumo no especificado'
            unidad = ""

        cantidad_str = f"{self.cantidad}{unidad}" if self.cantidad is not None else "0"
        return f"{plato_nombre} -> {cantidad_str} de {insumo_nombre}"


class MovimientoStock(models.Model):
    """
    Kardex auditable e inmutable para registrar cada alteración en los niveles de inventario.
    Guarda snapshots antes/después para auditoría estricta y reconciliación contable.
    """
    TIPO_CONSUMO_ORDEN = 'CONSUMO_ORDEN'
    TIPO_INGRESO_COMPRA = 'INGRESO_COMPRA'
    TIPO_AJUSTE_MANUAL = 'AJUSTE_MANUAL'
    TIPO_MERMA = 'MERMA'
    TIPO_MERMA_DESPERDICIO = 'MERMA_DESPERDICIO'

    TIPO_CHOICES = [
        (TIPO_CONSUMO_ORDEN, 'Consumo por Orden'),
        (TIPO_INGRESO_COMPRA, 'Ingreso por Compra / Reabastecimiento'),
        (TIPO_AJUSTE_MANUAL, 'Ajuste Manual'),
        (TIPO_MERMA, 'Merma / Desperdicio'),
        (TIPO_MERMA_DESPERDICIO, 'Merma / Desperdicio (Alias Extendido)'),
    ]

    insumo = models.ForeignKey(
        Insumo,
        on_delete=models.CASCADE,
        related_name='movimientos',
        verbose_name="Insumo"
    )
    tipo = models.CharField(
        max_length=30,
        choices=TIPO_CHOICES,
        verbose_name="Tipo de Movimiento"
    )
    cantidad = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Cantidad Afectada",
        help_text="Cantidad absoluta movida (ej: 0.500 kg)."
    )
    stock_anterior = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Stock Anterior"
    )
    stock_nuevo = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Stock Nuevo"
    )
    orden = models.ForeignKey(
        Orden,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movimientos_stock',
        verbose_name="Orden Asociada",
        help_text="Nulo si el movimiento proviene de compras o ajustes manuales."
    )
    fecha_hora = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Fecha y Hora de Registro"
    )
    notas = models.TextField(
        blank=True,
        default='',
        verbose_name="Notas de Auditoría"
    )

    class Meta:
        verbose_name = "Movimiento de Stock (Kardex)"
        verbose_name_plural = "Movimientos de Stock (Kardex)"
        ordering = ['-fecha_hora', '-id']

    def __str__(self):
        # 1. Manejo seguro de fecha_hora (localizada en zona horaria si es aware)
        if self.fecha_hora:
            dt = timezone.localtime(self.fecha_hora) if timezone.is_aware(self.fecha_hora) else self.fecha_hora
            fecha_str = dt.strftime('%Y-%m-%d %H:%M')
        else:
            fecha_str = 'Sin fecha'

        # 2. Identificador de persistencia (distingue instancias en memoria de registros guardados)
        id_str = f"#{self.id}" if self.id else "#Nuevo"

        # 3. Acceso seguro a relación ForeignKey con Insumo (evita RelatedObjectDoesNotExist / ObjectDoesNotExist)
        try:
            insumo = self.insumo
            insumo_nombre = insumo.nombre if insumo and hasattr(insumo, 'nombre') else 'Sin insumo'
            unidad = f" {insumo.unidad_medida}" if insumo and hasattr(insumo, 'unidad_medida') and insumo.unidad_medida else ""
        except (AttributeError, ObjectDoesNotExist):
            insumo_nombre = 'Sin insumo'
            unidad = ""

        # 4. Manejo seguro de cantidad y tipo de movimiento
        cantidad_str = f"{self.cantidad}{unidad}" if self.cantidad is not None else "0"
        tipo_str = self.tipo or "MOVIMIENTO"

        return f"[{fecha_str}] {id_str} {tipo_str}: {insumo_nombre} ({cantidad_str})"

