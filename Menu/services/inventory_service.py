"""
Service for atomic, deadlock-safe inventory stock deductions and Kardex auditing.
Handles direct dish recipes, promotional combo explosions, idempotency, and low-stock alerting.
"""

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

from django.db import transaction
from django.utils import timezone

from Menu.models import Insumo, Menu, MovimientoStock, Orden, OrdenItem, Plato, RecetaItem

logger = logging.getLogger(__name__)


def descontar_stock_orden(orden_id: Union[int, Orden], restaurante_esperado: Optional[Any] = None) -> Dict[str, Any]:
    """
    Descuenta de forma atómica e idempotente el stock de insumos consumidos por una orden.
    
    Características clave:
    1. Transacción atómica integral (ACID).
    2. Bloqueo de fila en Orden para serializar llamadas concurrentes.
    3. Idempotencia: si orden.stock_descontado es True, retorna sin alterar el inventario.
    4. Guardrail para órdenes eliminadas: no permite descontar stock si estado == 'Eliminada'.
    5. Explosión de recetas: resuelve platos individuales y platos pertenecientes a combos (Menu).
    6. Agregación de demanda por insumo evitando movimientos redundantes.
    7. Prevención de Deadlocks: bloquea filas de Insumo con select_for_update().order_by('id').
    8. Continuidad operativa: permite stock negativo registrando la deuda física en bodega.
    9. Registro inmutable en Kardex (MovimientoStock) con tipo 'CONSUMO_ORDEN'.
    10. Detección y retorno de alertas de stock mínimo o quiebre.

    :param orden_id: ID entero de la Orden o instancia del modelo Orden.
    :param restaurante_esperado: Instancia de Restaurante o ID para validación cruzada.
    :return: Diccionario con estado de éxito, conteo de movimientos y alertas de stock mínimo.
    """
    # Normalización del parámetro de entrada
    if hasattr(orden_id, "id"):
        orden_id = orden_id.id

    try:
        with transaction.atomic():
            # 1. Obtener y bloquear la orden para serializar llamadas concurrentes
            try:
                orden = Orden.objects.select_for_update().get(id=orden_id)
            except Orden.DoesNotExist:
                logger.warning("Intento de descuento de stock para orden inexistente: ID %s", orden_id)
                return {
                    "success": False,
                    "error": f"Orden con ID {orden_id} no encontrada.",
                    "message": f"Orden con ID {orden_id} no encontrada.",
                    "alertas_stock_minimo": [],
                    "movimientos": 0,
                }

            # 1.1 Validación de inquilino (Cross-Tenant Guardrail)
            if restaurante_esperado is not None:
                expected_id = getattr(restaurante_esperado, "id", restaurante_esperado)
                if orden.restaurante_id != expected_id:
                    logger.warning(
                        "Violación de aislamiento multi-tenant en orden %s: esperada %s, encontrada %s",
                        orden_id, expected_id, orden.restaurante_id
                    )
                    return {
                        "success": False,
                        "error": f"La orden #{orden_id} no pertenece al restaurante especificado.",
                        "message": f"La orden #{orden_id} no pertenece al restaurante especificado.",
                        "alertas_stock_minimo": [],
                        "movimientos": 0,
                    }

            # 2. Verificar estado de la orden (Órdenes eliminadas no descuentan stock)
            if orden.estado == Orden.ESTADO_ELIMINADA:
                logger.info("Orden %s está en estado 'Eliminada'. Se omite descuento de stock.", orden_id)
                return {
                    "success": False,
                    "error": f"No se puede descontar inventario de una orden eliminada (ID #{orden_id}).",
                    "message": f"No se puede descontar inventario de una orden eliminada (ID #{orden_id}).",
                    "alertas_stock_minimo": [],
                    "movimientos": 0,
                }

            # 3. Verificación de Idempotencia: Si ya fue descontado, salir limpiamente
            if orden.stock_descontado:
                logger.info("Orden %s ya fue descontada previamente. Ejecución idempotente.", orden_id)
                return {
                    "success": True,
                    "idempotente": True,
                    "alertas_stock_minimo": [],
                    "movimientos": 0,
                    "orden_id": orden.id,
                    "mensaje": f"El stock de la orden #{orden_id} ya fue descontado previamente.",
                    "message": f"El stock de la orden #{orden_id} ya fue descontado previamente.",
                }

            # 4. Explosión de recetas: Resolver platos directos y combos (Menu)
            # Mapa acumulador: plato_id -> cantidad total ordenada
            platos_demandados: Dict[int, int] = defaultdict(int)

            # Carga optimizada de items con relaciones plato y menu->platos
            items = (
                orden.items
                .select_related("plato", "menu")
                .prefetch_related("menu__platos")
                .all()
            )

            for item in items:
                if item.cantidad <= 0:
                    continue

                if item.plato_id:
                    platos_demandados[item.plato_id] += item.cantidad

                if item.menu_id and item.menu:
                    # Explota los platos contenidos en el combo
                    for plato_combo in item.menu.platos.all():
                        platos_demandados[plato_combo.id] += item.cantidad

            # Caso borde: Orden vacía o sin platos
            if not platos_demandados:
                logger.info("Orden %s no contiene platos o items para descontar.", orden_id)
                orden.stock_descontado = True
                if not orden.fecha_completada:
                    orden.fecha_completada = timezone.now()
                orden.save(update_fields=["stock_descontado", "fecha_completada"])
                return {
                    "success": True,
                    "alertas_stock_minimo": [],
                    "movimientos": 0,
                    "orden_id": orden.id,
                    "mensaje": "Orden sin platos; marcada como descontada sin movimientos.",
                    "message": "Orden sin platos; marcada como descontada sin movimientos.",
                }

            # 5. Agregación de demanda por Insumo (Escandallo)
            # Mapa acumulador: insumo_id -> cantidad requerida (Decimal)
            insumo_demandas: Dict[int, Decimal] = defaultdict(Decimal)

            recetas = (
                RecetaItem.objects
                .filter(plato_id__in=platos_demandados.keys(), restaurante=orden.restaurante)
                .select_related("insumo")
            )

            for receta in recetas:
                cant_plato = Decimal(str(platos_demandados[receta.plato_id]))
                cant_receta = Decimal(str(receta.cantidad))
                insumo_demandas[receta.insumo_id] += cant_plato * cant_receta

            # Caso borde: Platos ordenados no tienen ingredientes configurados en RecetaItem
            if not insumo_demandas:
                logger.info("Los platos de la orden %s no poseen recetas asociadas.", orden_id)
                orden.stock_descontado = True
                if not orden.fecha_completada:
                    orden.fecha_completada = timezone.now()
                orden.save(update_fields=["stock_descontado", "fecha_completada"])
                return {
                    "success": True,
                    "alertas_stock_minimo": [],
                    "movimientos": 0,
                    "orden_id": orden.id,
                    "mensaje": "Platos sin recetas configuradas; stock marcado como descontado.",
                    "message": "Platos sin recetas configuradas; stock marcado como descontado.",
                }

            # 6. Prevención de Deadlocks: Ordenamiento estricto de IDs al adquirir bloqueos de fila
            insumo_ids_ordenados = sorted(insumo_demandas.keys())
            insumos_bloqueados = (
                Insumo.objects
                .filter(id__in=insumo_ids_ordenados, restaurante=orden.restaurante)
                .select_for_update()
                .order_by("id")
            )

            movimientos_creados: List[MovimientoStock] = []
            alertas_stock: List[Dict[str, Any]] = []

            # 7. Deducción y Auditoría Kardex
            for insumo in insumos_bloqueados:
                cant_a_descontar = insumo_demandas[insumo.id]
                if cant_a_descontar <= Decimal("0.000"):
                    continue

                stock_anterior = Decimal(str(insumo.stock_actual))
                stock_nuevo = stock_anterior - cant_a_descontar

                # Actualiza stock en insumo (permite saldo negativo para continuidad operativa)
                insumo.stock_actual = stock_nuevo
                insumo.save(update_fields=["stock_actual"])

                # Registro inmutable en Kardex
                mov = MovimientoStock(
                    restaurante=orden.restaurante,
                    insumo=insumo,
                    tipo="CONSUMO_ORDEN",
                    cantidad=cant_a_descontar,
                    stock_anterior=stock_anterior,
                    stock_nuevo=stock_nuevo,
                    orden=orden,
                    notas=f"Consumo automático por Orden #{orden.id} ({orden.cliente})"
                )
                movimientos_creados.append(mov)

                # 8. Detección de Alertas de Stock Mínimo o Quiebre
                stock_minimo = Decimal(str(insumo.stock_minimo))
                if (stock_minimo > Decimal("0.000") and stock_nuevo <= stock_minimo) or (stock_nuevo < Decimal("0.000")):
                    alertas_stock.append({
                        "insumo_id": insumo.id,
                        "codigo": insumo.codigo,
                        "nombre": insumo.nombre,
                        "stock_actual": float(stock_nuevo),
                        "stock_minimo": float(stock_minimo),
                        "unidad_medida": insumo.unidad_medida,
                        "es_quiebre_negativo": stock_nuevo < Decimal("0.000"),
                        "mensaje": (
                            f"Alerta: Insumo '{insumo.nombre}' ({insumo.codigo}) quedó en "
                            f"{stock_nuevo} {insumo.unidad_medida} (Mínimo: {stock_minimo} {insumo.unidad_medida})."
                        ),
                    })

            # Inserción masiva de movimientos Kardex para máxima eficiencia
            if movimientos_creados:
                MovimientoStock.objects.bulk_create(movimientos_creados)

            # 9. Marcar la orden como descontada y registrar fecha de completada
            orden.stock_descontado = True
            if not orden.fecha_completada:
                orden.fecha_completada = timezone.now()
            orden.save(update_fields=["stock_descontado", "fecha_completada"])

            return {
                "success": True,
                "alertas_stock_minimo": alertas_stock,
                "movimientos": len(movimientos_creados),
                "orden_id": orden.id,
                "mensaje": f"Stock descontado exitosamente para la orden #{orden.id}.",
                "message": f"Stock descontado exitosamente para la orden #{orden.id}.",
            }

    except Exception as e:
        logger.exception("Error crítico no controlado en descontar_stock_orden(%s): %s", orden_id, e)
        # La transacción atómica ya realizó rollback automático
        return {
            "success": False,
            "error": str(e),
            "message": str(e),
            "alertas_stock_minimo": [],
            "movimientos": 0,
        }
