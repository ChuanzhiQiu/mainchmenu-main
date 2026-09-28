"""
Service for multi-tenant payment processing and split payments.

Guarantees transactional integrity (ACID) and strict row-level tenant isolation
for the `Menu_pago` model, including:
1. Cross-tenant validation for orden, cajero, turno and restaurante.
2. Cash register shift (turno) must be open.
3. Order must not be cancelled or already fully paid.
4. Creation of the `Menu_pago` record.
5. Automatic order completion and table release once the balance is settled.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Union

from django.db import transaction
from django.db.models import Sum

from Menu.models import Cajero, Mesa, Orden, Pago, Restaurante, TurnoCaja

logger = logging.getLogger(__name__)


class PagoValidationError(Exception):
    """Error de dominio lanzado cuando un pago viola reglas de negocio o multi-tenancy."""


def _to_decimal(value: Any, default: str = "0.00") -> Decimal:
    """Convierte un valor monetario a Decimal de forma defensiva."""
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal(default)


def _normalizar_orden(orden: Union[int, Orden]) -> Orden:
    """Normaliza el parámetro `orden` (ID o instancia) y bloquea la fila."""
    if hasattr(orden, "id"):
        orden_id = orden.id
    else:
        orden_id = orden

    try:
        return Orden.objects.select_for_update().get(id=orden_id)
    except Orden.DoesNotExist as exc:
        raise PagoValidationError(f"La orden #{orden_id} no existe.") from exc


def _validar_multi_tenant(
    *,
    orden: Orden,
    cajero: Optional[Cajero],
    turno: Optional[TurnoCaja],
    restaurante: Optional[Union[Restaurante, int]],
) -> int:
    """Valida que orden, cajero y turno pertenezcan estrictamente al mismo restaurante."""
    expected_tenant_id = getattr(restaurante, "id", restaurante) if restaurante is not None else orden.restaurante_id

    if orden.restaurante_id != expected_tenant_id:
        raise PagoValidationError(
            f"La orden #{orden.id} no pertenece al restaurante especificado."
        )

    if cajero is not None and cajero.restaurante_id != expected_tenant_id:
        raise PagoValidationError(
            f"El cajero '{cajero.nombre}' no pertenece al restaurante de la orden."
        )

    if turno is not None and turno.restaurante_id != expected_tenant_id:
        raise PagoValidationError(
            f"El turno #{turno.id} no pertenece al restaurante de la orden."
        )

    return expected_tenant_id


@transaction.atomic
def registrar_pago(
    orden: Union[int, Orden],
    metodo_pago: str,
    monto: Any,
    propina: Any = Decimal("0.00"),
    cajero: Optional[Cajero] = None,
    turno: Optional[TurnoCaja] = None,
    referencia_transaccion: Optional[str] = None,
    restaurante: Optional[Union[Restaurante, int]] = None,
) -> Dict[str, Any]:
    """
    Registra un pago (individual o parcial) contra una orden, validando multi-tenancy
    y actualizando la orden/mesa cuando el saldo queda cubierto.

    :param orden: Instancia o ID de la Orden a pagar.
    :param metodo_pago: Método de pago (ej. Efectivo, Débito, Crédito, Transferencia).
    :param monto: Monto abonado en este pago.
    :param propina: Propina opcional asociada al pago.
    :param cajero: Cajero que registra el pago (opcional).
    :param turno: Turno de caja vigente (opcional, pero si se entrega debe estar abierto).
    :param referencia_transaccion: Referencia externa opcional de la transacción.
    :param restaurante: Restaurante esperado para validación cruzada (opcional).
    :return: Diccionario con pago, total_pagado, saldo_pendiente, orden_completada y cambio.
    """
    # 1. Bloquear y normalizar la orden para serializar pagos concurrentes.
    orden = _normalizar_orden(orden)

    # 2. Validaciones multi-tenant estrictas.
    tenant_id = _validar_multi_tenant(
        orden=orden,
        cajero=cajero,
        turno=turno,
        restaurante=restaurante,
    )

    # 3. Validar estado del turno de caja.
    if turno is not None and turno.estado != TurnoCaja.ESTADO_ABIERTO:
        raise PagoValidationError(
            f"El turno de caja #{turno.id} no está abierto (estado actual: {turno.estado})."
        )

    # 4. Validar estado de la orden.
    if orden.estado == Orden.ESTADO_ELIMINADA:
        raise PagoValidationError("No se puede pagar una orden cancelada/eliminada.")

    # 4. Normalizar el total de la orden (puede llegar como Decimal o float).
    orden_monto_total = _to_decimal(orden.monto_total)

    total_pagado_previo = (
        Pago.all_objects.filter(orden=orden).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    total_pagado_previo = _to_decimal(total_pagado_previo)
    if total_pagado_previo >= orden_monto_total:
        raise PagoValidationError(
            f"La orden #{orden.id} ya está totalmente pagada "
            f"(pagado: {total_pagado_previo}, total: {orden_monto_total})."
        )

    # 5. Normalizar montos y validar monto positivo.
    monto_decimal = _to_decimal(monto)
    propina_decimal = _to_decimal(propina)
    if monto_decimal <= Decimal("0.00"):
        raise PagoValidationError("El monto del pago debe ser mayor a 0.")

    # 6. Crear el registro de pago.
    pago = Pago.objects.create(
        restaurante_id=tenant_id,
        orden=orden,
        metodo_pago=metodo_pago,
        monto=monto_decimal,
        propina=propina_decimal,
        referencia_transaccion=referencia_transaccion,
        cajero=cajero,
        turno=turno,
    )

    # 7. Calcular saldos y cambio antes de mutar el estado de la orden.
    total_pagado = total_pagado_previo + monto_decimal
    orden_completada = total_pagado >= orden_monto_total
    saldo_pendiente = max(Decimal("0.00"), orden_monto_total - total_pagado)

    cambio = Decimal("0.00")
    metodo_normalizado = (metodo_pago or "").strip().lower()
    if metodo_normalizado == "efectivo":
        cambio = max(Decimal("0.00"), total_pagado - orden_monto_total)

    # 8. Actualizar la orden y liberar la mesa si el saldo quedó cubierto.
    if orden_completada:
        orden.estado = Orden.ESTADO_COMPLETADA
        orden.save(update_fields=["estado"])

        if orden.mesa_id:
            Mesa.all_objects.filter(id=orden.mesa_id).update(estado=Mesa.ESTADO_LIBRE)

    logger.info(
        "Pago #%s registrado para orden #%s (método=%s, monto=%s, completada=%s)",
        pago.id,
        orden.id,
        metodo_pago,
        monto_decimal,
        orden_completada,
    )

    return {
        "pago": pago,
        "total_pagado": total_pagado,
        "saldo_pendiente": saldo_pendiente,
        "orden_completada": orden_completada,
        "cambio": cambio,
    }
