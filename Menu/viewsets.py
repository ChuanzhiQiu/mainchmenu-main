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

from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from Menu.models import Area, Cajero, Categoria, Mesa, Orden, TurnoCaja
from Menu.module_gates import ModuloSalonesMesasPermission
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
    permission_classes = [ModuloSalonesMesasPermission]
    serializer_class = AreaSerializer


class CategoriaViewSet(_TenantModelViewSet):
    serializer_class = CategoriaSerializer


class MesaViewSet(_TenantModelViewSet):
    permission_classes = [ModuloSalonesMesasPermission]
    serializer_class = MesaSerializer


class OrdenPagination(PageNumberPagination):
    """Paginación opt-in (solo cuando el cliente envía `?page=`)."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class OrdenViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de órdenes + endpoint de pago (registrar-pago)."""

    serializer_class = OrdenSerializer

    def get_queryset(self):
        restaurante = get_current_restaurante(self.request)
        queryset = (
            Orden.objects.filter(restaurante=restaurante)
            .select_related("cajero", "mesa")
            .order_by("-fecha", "-hora", "-id")
        )

        params = getattr(self.request, "query_params", {}) or {}

        estado = (params.get("estado") or "").strip()
        if estado:
            queryset = queryset.filter(estado=estado)

        canal_venta = (params.get("canal_venta") or "").strip()
        if canal_venta:
            queryset = queryset.filter(canal_venta=canal_venta)

        fecha_desde = parse_date((params.get("fecha_desde") or "").strip())
        if fecha_desde:
            queryset = queryset.filter(fecha__gte=fecha_desde)

        fecha_hasta = parse_date((params.get("fecha_hasta") or "").strip())
        if fecha_hasta:
            queryset = queryset.filter(fecha__lte=fecha_hasta)

        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        # Paginación DRF explícita solo cuando el cliente la solicita (?page=).
        if "page" in request.query_params:
            paginator = OrdenPagination()
            page = paginator.paginate_queryset(queryset, request, view=self)
            serializer = self.get_serializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        # Compatibilidad: listado JSON plano con tope defensivo (?limit=).
        try:
            limit = int(request.query_params.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 200))

        serializer = self.get_serializer(queryset[:limit], many=True)
        return Response(serializer.data)

    def _resolver_cajero(self, restaurante):
        request = self.request
        if not hasattr(request, "session"):
            raise PagoValidationError(
                "Se requiere un cajero y un turno de caja abierto para registrar el cobro."
            )
        cajero_id = request.session.get("cajero_id")
        if not cajero_id:
            raise PagoValidationError(
                "Se requiere un cajero y un turno de caja abierto para registrar el cobro."
            )
        try:
            cajero = Cajero.all_objects.filter(
                id=int(cajero_id), restaurante=restaurante, activo=True
            ).first()
        except (TypeError, ValueError):
            cajero = None
        if cajero is None:
            raise PagoValidationError(
                "El cajero no pertenece al restaurante activo o no se encuentra activo."
            )
        return cajero

    def _resolver_turno(self, restaurante, cajero):
        request = self.request
        if not hasattr(request, "session"):
            raise PagoValidationError(
                "Se requiere un cajero y un turno de caja abierto para registrar el cobro."
            )
        turno_id = request.session.get("turno_id")
        turno = None
        if turno_id:
            try:
                turno = TurnoCaja.all_objects.filter(
                    id=int(turno_id), restaurante=restaurante
                ).first()
            except (TypeError, ValueError):
                turno = None
            if turno is None:
                raise PagoValidationError(
                    "El turno de caja no pertenece al restaurante activo."
                )
        if turno is None and cajero is not None:
            turno = TurnoCaja.all_objects.filter(
                restaurante=restaurante, cajero=cajero, estado=TurnoCaja.ESTADO_ABIERTO
            ).first()
        if turno is None:
            raise PagoValidationError(
                "Se requiere un cajero y un turno de caja abierto para registrar el cobro."
            )
        if turno.estado != TurnoCaja.ESTADO_ABIERTO:
            raise PagoValidationError(
                f"El turno de caja #{turno.id} no está abierto (estado actual: {turno.estado})."
            )
        return turno

    @action(detail=True, methods=["post"], url_path="registrar-pago")
    def registrar_pago(self, request, pk=None, **kwargs):
        # get_object() ya aplica el filtro de tenant vía get_queryset().
        orden = self.get_object()

        serializer = RegistroPagoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        restaurante = get_current_restaurante(request)

        try:
            cajero = self._resolver_cajero(restaurante)
            turno = self._resolver_turno(restaurante, cajero)

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
