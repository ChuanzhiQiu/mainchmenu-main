"""
ViewSets DRF multi-tenant para el POS Restaurante (Sección 7 AGENTS.md).

Reglas de aislamiento aplicadas:
- NUNCA se usa `queryset = Modelo.objects.all()` sin filtrar.
- `get_queryset()` filtra SIEMPRE por el restaurante activo.
- `perform_create()` inyecta `restaurante` desde el contexto de la request (nunca desde payload).
- El endpoint de pago delega en `Menu/services/pago_service.py` y traduce las excepciones
  de dominio (`PagoValidationError`) a respuestas HTTP 400.
"""
from decimal import Decimal

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from Menu.models import Area, Cajero, Categoria, Mesa, Orden, TurnoCaja
from Menu.serializers import (
    AreaSerializer,
    CategoriaSerializer,
    MesaSerializer,
    OrdenSerializer,
    PagoSerializer,
    RegistroPagoSerializer,
)
from Menu.services.pago_service import PagoValidationError, registrar_pago
from Menu.views import get_current_restaurante


class _TenantModelViewSet(viewsets.ModelViewSet):
    """
    Base multi-tenant: resuelve el restaurante activo vía el mecanismo vigente
    (request.restaurante -> ContextVar -> sesión -> tenant 'mainch') y filtra/injecta.
    """

    def _restaurante(self):
        return get_current_restaurante(self.request)

    def get_queryset(self):
        return self.serializer_class.Meta.model.objects.filter(restaurante=self._restaurante())

    def perform_create(self, serializer):
        serializer.save(restaurante=self._restaurante())


class AreaViewSet(_TenantModelViewSet):
    serializer_class = AreaSerializer


class CategoriaViewSet(_TenantModelViewSet):
    serializer_class = CategoriaSerializer


class MesaViewSet(_TenantModelViewSet):
    serializer_class = MesaSerializer


class OrdenViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de órdenes + endpoint de pago (registrar-pago)."""

    serializer_class = OrdenSerializer

    def get_queryset(self):
        restaurante = get_current_restaurante(self.request)
        return Orden.objects.filter(restaurante=restaurante).select_related(
            "mesa", "cajero", "turno"
        )

    def _resolver_cajero(self, restaurante):
        request = self.request
        if not hasattr(request, "session"):
            return None
        cajero_id = request.session.get("cajero_id")
        if not cajero_id:
            return None
        try:
            return Cajero.all_objects.filter(
                id=int(cajero_id), restaurante=restaurante, activo=True
            ).first()
        except (TypeError, ValueError):
            return None

    def _resolver_turno(self, restaurante, cajero):
        request = self.request
        if not hasattr(request, "session"):
            return None
        turno_id = request.session.get("turno_id")
        if turno_id:
            try:
                turno = TurnoCaja.all_objects.filter(
                    id=int(turno_id), restaurante=restaurante
                ).first()
                if turno:
                    # El servicio validará que esté abierto y pertenezca a la orden/cajero.
                    return turno
            except (TypeError, ValueError):
                pass
        if cajero:
            return TurnoCaja.all_objects.filter(
                restaurante=restaurante, cajero=cajero, estado=TurnoCaja.ESTADO_ABIERTO
            ).first()
        return None

    @action(detail=True, methods=["post"], url_path="registrar-pago")
    def registrar_pago(self, request, pk=None, **kwargs):
        # get_object() ya aplica el filtro de tenant vía get_queryset().
        orden = self.get_object()

        serializer = RegistroPagoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        restaurante = get_current_restaurante(request)
        cajero = self._resolver_cajero(restaurante)
        turno = self._resolver_turno(restaurante, cajero)

        try:
            resultado = registrar_pago(
                orden=orden,
                metodo_pago=data["metodo_pago"],
                monto=data["monto"],
                propina=data.get("propina", Decimal("0.00")),
                cajero=cajero,
                turno=turno,
                referencia_transaccion=data.get("referencia_transaccion"),
                restaurante=restaurante,
            )
        except PagoValidationError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "pago": PagoSerializer(resultado["pago"], context={"request": request}).data,
                "total_pagado": str(resultado["total_pagado"]),
                "saldo_pendiente": str(resultado["saldo_pendiente"]),
                "orden_completada": resultado["orden_completada"],
                "cambio": str(resultado["cambio"]),
            },
            status=status.HTTP_201_CREATED,
        )


router = DefaultRouter()
router.register("areas", AreaViewSet, basename="area")
router.register("mesas", MesaViewSet, basename="mesa")
router.register("categorias", CategoriaViewSet, basename="categoria")
router.register("ordenes", OrdenViewSet, basename="orden")
