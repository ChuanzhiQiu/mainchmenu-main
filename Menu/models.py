from decimal import Decimal
import math
import uuid
from django.db import models
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.utils import timezone
from django.utils.text import slugify
from Menu.managers import TenantManager



# ==============================================================================
# MULTI-RESTAURANTE / MULTI-TENANCY (MAINCH COMO CLIENTE BASE)
# ==============================================================================

class Restaurante(models.Model):
    """
    Representa a cada restaurante cliente dentro del sistema multi-inquilino.
    Por defecto, el inquilino principal y único inicial es 'Mainch'.
    """
    nombre = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    direccion = models.CharField(max_length=255, blank=True, default="Valparaíso, Chile")
    telefono = models.CharField(max_length=50, blank=True, default="+56 9 1234 5678")
    activo = models.BooleanField(default=True, db_index=True)
    api_key_delivery = models.CharField(
        max_length=100,
        unique=True,
        default=uuid.uuid4,
        verbose_name="API Key Delivery (Webhooks)"
    )
    creado_el = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Restaurante"
        verbose_name_plural = "Restaurantes"

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        if not self.slug and self.nombre:
            self.slug = slugify(self.nombre)
        elif self.slug:
            self.slug = slugify(self.slug)

    def save(self, *args, **kwargs):
        if not self.slug and self.nombre:
            self.slug = slugify(self.nombre)
        elif self.slug:
            self.slug = slugify(self.slug)
        self.clean()
        super().save(*args, **kwargs)



def get_default_restaurante():
    """Retorna o crea el restaurante por defecto 'Mainch'."""
    restaurante, _ = Restaurante.objects.get_or_create(
        slug="mainch",
        defaults={"nombre": "Mainch", "direccion": "Valparaíso, Chile"}
    )
    return restaurante.pk


# Modelo para representar cada plato de manera individual
class Plato(models.Model):
    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name="platos",
        default=get_default_restaurante
    )
    nombre = models.CharField(max_length=100)
    valor = models.FloatField(verbose_name="Precio Base / Salón Local ($)")

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        base_manager_name = 'all_objects'
        verbose_name = "Plato"
        verbose_name_plural = "Platos"

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()

    def save(self, *args, **kwargs):
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()
        self.clean()
        super().save(*args, **kwargs)

    def get_precio_para_canal(self, canal: str) -> float:
        """Devuelve el precio del plato según el canal de venta (Price Tiering)."""
        precio_tier = self.precios_canales.filter(canal=canal, disponible=True).first()
        if precio_tier and precio_tier.precio > 0:
            return float(precio_tier.precio)
        return float(self.valor)

    def get_sku_para_canal(self, canal: str) -> str:
        """Devuelve el SKU mapeado para un canal externo o un identificador por defecto."""
        precio_tier = self.precios_canales.filter(canal=canal).first()
        if precio_tier and precio_tier.sku_externo:
            return precio_tier.sku_externo
        return f"PLATO-{self.id}"



# Price Tiering y SKU Mapping por Canal (Local vs Delivery Apps)
class PlatoPrecioCanal(models.Model):
    CANAL_CHOICES = [
        ('Local', 'Local / Mesas'),
        ('UberEats', 'Uber Eats'),
        ('PedidosYa', 'Pedidos Ya'),
        ('Delivery', 'Delivery Propio'),
    ]

    plato = models.ForeignKey(
        Plato,
        on_delete=models.CASCADE,
        related_name='precios_canales'
    )
    canal = models.CharField(max_length=20, choices=CANAL_CHOICES)
    sku_externo = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="SKU Externo en Plataforma",
        help_text="Identificador único en Uber Eats o Pedidos Ya para sincronización automática."
    )
    precio = models.FloatField(
        verbose_name="Precio en Canal ($)",
        help_text="Precio con recargo de plataforma o precio específico para este canal."
    )
    disponible = models.BooleanField(
        default=True,
        verbose_name="Disponible en este canal"
    )

    class Meta:
        verbose_name = "Price Tier y Mapeo SKU por Canal"
        verbose_name_plural = "Price Tiers y Mapeos SKU"
        unique_together = ('plato', 'canal')

    def __str__(self):
        return f"{self.plato.nombre} [{self.canal}]: ${self.precio} (SKU: {self.sku_externo or 'N/A'})"


# Modelo para representar una promoción (colaciones, combos, etc.)
class Menu(models.Model):
    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name="menus",
        default=get_default_restaurante
    )
    nombre = models.CharField(max_length=100)
    platos = models.ManyToManyField(Plato)
    precio_menus = models.FloatField()

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        base_manager_name = 'all_objects'
        verbose_name = "Menú"
        verbose_name_plural = "Menús"

    def __str__(self):
        return self.nombre

    @property
    def precio_real(self):
        if not self.pk:
            return 0
        return sum(plato.valor for plato in self.platos.all())

    @property
    def descuento(self):
        return self.precio_real - self.precio_menus

    def clean(self):
        super().clean()
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()
        if self.pk:
            if not self.platos.exists():
                raise ValidationError("El menú debe contener al menos un plato.")
            for plato in self.platos.all():
                if plato.restaurante_id != self.restaurante_id:
                    raise ValidationError(
                        f"Contaminación cross-tenant detectada: El plato '{plato.nombre}' pertenece a "
                        f"'{plato.restaurante.nombre}', no al combo del restaurante '{self.restaurante.nombre}'."
                    )

    def save(self, *args, **kwargs):
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()
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
    
    CANAL_LOCAL = 'Local'
    CANAL_WHATSAPP = 'Whatsapp'
    CANAL_UBER_EATS = 'UberEats'
    CANAL_PEDIDOS_YA = 'PedidosYa'
    CANAL_DELIVERY = 'Delivery'

    CANAL_CHOICES = [
        (CANAL_LOCAL, 'Local / Salón'),
        (CANAL_WHATSAPP, 'WhatsApp'),
        (CANAL_UBER_EATS, 'Uber Eats'),
        (CANAL_PEDIDOS_YA, 'Pedidos Ya'),
        (CANAL_DELIVERY, 'Delivery Propio'),
    ]

    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name="ordenes",
        default=get_default_restaurante
    )
    cliente = models.CharField(max_length=60)
    fecha = models.DateField(auto_now_add=True)
    hora = models.TimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_EN_CURSO)
    tipo_pago = models.CharField(max_length=20, choices=PAGO_CHOICES, blank=True, null=True)
    canal_venta = models.CharField(max_length=20, choices=CANAL_CHOICES, default=CANAL_LOCAL)
    monto_total = models.FloatField(default=0.0)
    descuento = models.FloatField(default=0.0, blank=True)
    order_id_externo = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="ID Externo Delivery (Uber Eats / Pedidos Ya)"
    )
    detalles_entrega = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Detalles de Entrega / Repartidor"
    )
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
        return f"Orden {self.id} - {self.cliente} ({self.canal_venta} - {self.estado})"

    @property
    def es_delivery(self) -> bool:
        """Determina si la comanda proviene de canales de delivery."""
        return self.canal_venta in [self.CANAL_UBER_EATS, self.CANAL_PEDIDOS_YA, self.CANAL_DELIVERY]

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

    objects = TenantManager()
    all_objects = models.Manager()

    def clean(self):
        super().clean()
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()

    def save(self, *args, **kwargs):
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()
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
        base_manager_name = 'all_objects'
        verbose_name = "Orden"
        verbose_name_plural = "Órdenes"

# Modelo para representar los ítems de una orden
class OrdenItem(models.Model):
    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name='orden_items',
        verbose_name="Restaurante"
    )
    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='items')
    plato = models.ForeignKey(Plato, on_delete=models.CASCADE, null=True, blank=True)
    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, null=True, blank=True)
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.FloatField(
        default=0.0,
        verbose_name="Precio Unitario Venta ($)",
        help_text="Precio congelado al momento de la venta respetando Price Tier del canal."
    )

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        base_manager_name = 'all_objects'
        verbose_name = "Ítem de Orden"
        verbose_name_plural = "Ítems de Orden"

    def __str__(self):
        if self.plato:
            return f"{self.cantidad}x {self.plato.nombre}"
        elif self.menu:
            return f"{self.cantidad}x {self.menu.nombre}"
        return "Item sin plato ni menú"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.restaurante_id:
            if self.orden_id and hasattr(self, 'orden') and self.orden:
                self.restaurante = self.orden.restaurante
            elif self.plato_id and hasattr(self, 'plato') and self.plato:
                self.restaurante = self.plato.restaurante
            elif self.menu_id and hasattr(self, 'menu') and self.menu:
                self.restaurante = self.menu.restaurante

    def clean(self):
        super().clean()
        if self.orden_id and hasattr(self, 'orden') and self.orden:
            if self.restaurante_id and self.restaurante_id != self.orden.restaurante_id:
                raise ValidationError(
                    f"Contaminación cross-tenant detectada: El item está asignado al restaurante ID {self.restaurante_id}, "
                    f"pero la orden #{self.orden.id} pertenece al restaurante ID {self.orden.restaurante_id}."
                )
        if self.plato_id and self.menu_id:
            raise ValidationError("El item de orden no puede tener simultáneamente plato y menú/combo.")

        if self.plato_id and hasattr(self, 'plato') and self.plato:
            if self.restaurante_id and self.plato.restaurante_id != self.restaurante_id:
                raise ValidationError(
                    f"Contaminación cross-tenant detectada: El plato '{self.plato.nombre}' "
                    f"pertenece a '{self.plato.restaurante.nombre}', no a '{self.restaurante.nombre}'."
                )

        if self.menu_id and hasattr(self, 'menu') and self.menu:
            if self.restaurante_id and self.menu.restaurante_id != self.restaurante_id:
                raise ValidationError(
                    f"Contaminación cross-tenant detectada: El menú '{self.menu.nombre}' "
                    f"pertenece a '{self.menu.restaurante.nombre}', no a '{self.restaurante.nombre}'."
                )

    def save(self, *args, **kwargs):
        if self.orden_id and hasattr(self, 'orden') and self.orden:
            self.restaurante = self.orden.restaurante
        elif not self.restaurante_id:
            if self.plato_id and hasattr(self, 'plato') and self.plato:
                self.restaurante = self.plato.restaurante
            elif self.menu_id and hasattr(self, 'menu') and self.menu:
                self.restaurante = self.menu.restaurante
            else:
                self.restaurante_id = get_default_restaurante()

        if not self.precio_unitario or self.precio_unitario <= 0:
            if self.plato:
                # Tomar price tier si la orden ya tiene canal
                if self.orden_id and hasattr(self.orden, 'canal_venta'):
                    self.precio_unitario = self.plato.get_precio_para_canal(self.orden.canal_venta)
                else:
                    self.precio_unitario = self.plato.valor
            elif self.menu:
                self.precio_unitario = self.menu.precio_menus

        self.clean()
        super().save(*args, **kwargs)

    @property
    def subtotal(self):
        unit_price = self.precio_unitario
        if not unit_price or unit_price <= 0:
            if self.plato:
                unit_price = self.plato.valor
            elif self.menu:
                unit_price = self.menu.precio_menus
            else:
                unit_price = 0
        return self.cantidad * unit_price




# ==============================================================================
# INVENTORY & ESCANDALLO (RECETAS) DATA MODELS - MILESTONE 2
# ==============================================================================

class Insumo(models.Model):
    """
    Representa una materia prima o insumo unitario en el inventario del restaurante.
    Soporta unidades de medida culinarias, stocks de seguridad y costeo unitario.
    """
    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name="insumos",
        default=get_default_restaurante
    )
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
        verbose_name="Código Insumo",
        help_text="Identificador único o SKU del insumo en el restaurante (ej: INS-001, INS-PAN)."
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

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        base_manager_name = 'all_objects'
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"
        ordering = ['nombre']
        constraints = [
            models.UniqueConstraint(
                fields=['restaurante', 'codigo'],
                name='unique_restaurante_insumo_codigo'
            )
        ]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

    @property
    def esta_bajo_minimo(self) -> bool:
        """Determina si el insumo está estrictamente bajo el umbral de seguridad (TC-B02-25)."""
        return self.stock_actual < self.stock_minimo

    def clean(self):
        super().clean()
        if self.codigo:
            self.codigo = self.codigo.strip().upper()
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()
        existing = Insumo.objects.filter(
            restaurante_id=self.restaurante_id,
            codigo__iexact=self.codigo
        )
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                f"Ya existe un insumo con el código '{self.codigo}' en este restaurante."
            )

    def save(self, *args, **kwargs):
        if not self.restaurante_id:
            self.restaurante_id = get_default_restaurante()
        if self.codigo:
            self.codigo = self.codigo.strip().upper()
        self.clean()
        super().save(*args, **kwargs)



class RecetaItem(models.Model):
    """
    Representa el escandallo (Bill of Materials) vinculando un Plato con un Insumo.
    Define la cantidad requerida de materia prima para preparar una porción del plato.
    """
    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name='receta_items',
        verbose_name="Restaurante"
    )
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

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        base_manager_name = 'all_objects'
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.restaurante_id:
            if self.plato_id and hasattr(self, 'plato') and self.plato:
                self.restaurante = self.plato.restaurante
            elif self.insumo_id and hasattr(self, 'insumo') and self.insumo:
                self.restaurante = self.insumo.restaurante

    def clean(self):
        super().clean()
        if self.plato_id and hasattr(self, 'plato') and self.plato:
            if not self.restaurante_id:
                self.restaurante = self.plato.restaurante
            elif self.restaurante_id != self.plato.restaurante_id:
                raise ValidationError(
                    f"Contaminación cross-tenant detectada: El plato '{self.plato.nombre}' pertenece a "
                    f"'{self.plato.restaurante.nombre}', pero la receta pertenece a '{self.restaurante.nombre}'."
                )

        if self.insumo_id and hasattr(self, 'insumo') and self.insumo:
            if not self.restaurante_id:
                self.restaurante = self.insumo.restaurante
            elif self.restaurante_id != self.insumo.restaurante_id:
                raise ValidationError(
                    f"Contaminación cross-tenant detectada: El insumo '{self.insumo.nombre}' pertenece a "
                    f"'{self.insumo.restaurante.nombre}', pero la receta pertenece a '{self.restaurante.nombre}'."
                )

        if self.cantidad is not None and self.cantidad <= Decimal('0.000'):
            raise ValidationError("La cantidad requerida de insumo en la receta debe ser mayor a 0.")

    def save(self, *args, **kwargs):
        if not self.restaurante_id:
            if self.plato_id and hasattr(self, 'plato') and self.plato:
                self.restaurante = self.plato.restaurante
            elif self.insumo_id and hasattr(self, 'insumo') and self.insumo:
                self.restaurante = self.insumo.restaurante
            else:
                self.restaurante_id = get_default_restaurante()
        self.clean()
        super().save(*args, **kwargs)


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

    restaurante = models.ForeignKey(
        Restaurante,
        on_delete=models.CASCADE,
        related_name='movimientos_stock',
        verbose_name="Restaurante"
    )
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

    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        base_manager_name = 'all_objects'
        verbose_name = "Movimiento de Stock (Kardex)"
        verbose_name_plural = "Movimientos de Stock (Kardex)"
        ordering = ['-fecha_hora', '-id']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.restaurante_id:
            if self.orden_id and hasattr(self, 'orden') and self.orden:
                self.restaurante = self.orden.restaurante
            elif self.insumo_id and hasattr(self, 'insumo') and self.insumo:
                self.restaurante = self.insumo.restaurante

    def clean(self):
        super().clean()
        if not self.restaurante_id:
            if self.orden_id and hasattr(self, 'orden') and self.orden:
                self.restaurante = self.orden.restaurante
            elif self.insumo_id and hasattr(self, 'insumo') and self.insumo:
                self.restaurante = self.insumo.restaurante

        if self.insumo_id and hasattr(self, 'insumo') and self.insumo and self.restaurante_id and self.insumo.restaurante_id != self.restaurante_id:
            raise ValidationError(
                f"Contaminación cross-tenant detectada: El insumo '{self.insumo.nombre}' pertenece a "
                f"'{self.insumo.restaurante.nombre}', pero el movimiento Kardex está asignado a '{self.restaurante.nombre}'."
            )

        if self.orden_id and hasattr(self, 'orden') and self.orden and self.restaurante_id and self.orden.restaurante_id != self.restaurante_id:
            raise ValidationError(
                f"Contaminación cross-tenant detectada: La orden #{self.orden.id} pertenece a "
                f"'{self.orden.restaurante.nombre}', pero el movimiento Kardex está asignado a '{self.restaurante.nombre}'."
            )

    def save(self, *args, **kwargs):
        if not self.restaurante_id:
            if self.orden_id and hasattr(self, 'orden') and self.orden:
                self.restaurante = self.orden.restaurante
            elif self.insumo_id and hasattr(self, 'insumo') and self.insumo:
                self.restaurante = self.insumo.restaurante
            else:
                self.restaurante_id = get_default_restaurante()
        self.clean()
        super().save(*args, **kwargs)

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


