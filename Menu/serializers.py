"""
Serializers DRF multi-tenant para el POS Restaurante (Sección 7 AGENTS.md).

Reglas aplicadas:
- `restaurante` NUNCA se expone en `fields` (el ViewSet lo inyecta en perform_create).
- FKs en payload se validan cross-tenant (ej. `Mesa.area`).
- Montos monetarios como DecimalField (nunca FloatField).
"""
from decimal import Decimal

from rest_framework import serializers

from Menu.models import Area, Categoria, Mesa, Orden, Pago


class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ["id", "nombre", "activo"]
        # restaurante: omitido del payload. Inyectado por el ViewSet vía perform_create.


class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = ["id", "nombre", "orden", "activo"]
        # restaurante: omitido del payload. Inyectado por el ViewSet vía perform_create.


class MesaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mesa
        fields = ["id", "area", "numero", "capacidad", "estado"]
        # restaurante: omitido del payload. Inyectado por el ViewSet vía perform_create.

    def validate_area(self, area):
        """Validación cross-tenant: el área debe pertenecer al mismo restaurante activo."""
        request = self.context.get("request")
        restaurante = getattr(request, "restaurante", None) if request is not None else None
        if restaurante is not None and area.restaurante_id != restaurante.id:
            raise serializers.ValidationError(
                "El área seleccionada no pertenece al restaurante activo."
            )
        return area


class PagoSerializer(serializers.ModelSerializer):
    """Representación de salida de un pago registrado."""

    class Meta:
        model = Pago
        fields = [
            "id",
            "orden",
            "metodo_pago",
            "monto",
            "propina",
            "referencia_transaccion",
            "creado_el",
        ]


class OrdenSerializer(serializers.ModelSerializer):
    """Representación de solo lectura de una orden (listado/detalle)."""

    class Meta:
        model = Orden
        fields = [
            "id",
            "cliente",
            "estado",
            "tipo_pago",
            "canal_venta",
            "tipo_servicio",
            "mesa",
            "monto_total",
            "descuento",
            "fecha",
            "hora",
            "cajero",
            "turno",
        ]
        read_only_fields = fields


class RegistroPagoSerializer(serializers.Serializer):
    """Payload de entrada para el endpoint @action registrar-pago."""

    metodo_pago = serializers.CharField(max_length=50)
    monto = serializers.DecimalField(max_digits=12, decimal_places=2)
    propina = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        default=Decimal("0.00"),
        required=False,
    )
    referencia_transaccion = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
