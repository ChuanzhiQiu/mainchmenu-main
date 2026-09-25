"""
Service for Delivery Platform Integrations (Uber Eats & Pedidos Ya).
Handles Webhook ingestion, SKU Mapping resolution, Price Tier application,
and atomic order injection into the Kitchen Display System (KDS).
"""

import logging
from typing import Dict, Any, Tuple, Optional
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from Menu.models import Orden, OrdenItem, Plato, PlatoPrecioCanal, Restaurante

logger = logging.getLogger(__name__)


def procesar_orden_delivery_externa(
    restaurante: Restaurante,
    canal: str,
    payload: Dict[str, Any]
) -> Tuple[bool, Optional[Orden], str, bool]:
    """
    Ingesta atómica de pedidos provenientes de APIs de Delivery (Uber Eats, Pedidos Ya).
    
    1. Valida el canal (UberEats, PedidosYa).
    2. Idempotencia: evita duplicar una orden externa con el mismo 'order_id_externo'.
    3. Resuelve el SKU Mapping: asocia cada ítem del payload con un Plato local usando
       el SKU externo o ID del catálogo de la plataforma.
    4. Aplica el Price Tier respectivo configurado para dicho canal.
    5. Inyecta la orden en estado 'En curso' para que aparezca inmediatamente en el KDS de cocina.
    """
    canal_normalizado = canal.strip()
    if canal_normalizado.lower() in ["ubereats", "uber_eats", "uber"]:
        canal_db = Orden.CANAL_UBER_EATS
    elif canal_normalizado.lower() in ["pedidosya", "pedidos_ya", "peya"]:
        canal_db = Orden.CANAL_PEDIDOS_YA
    else:
        canal_db = Orden.CANAL_DELIVERY

    order_id_ext = str(payload.get("id") or payload.get("order_id") or payload.get("orderId") or "").strip()
    if not order_id_ext:
        return False, None, "Payload sin identificador de orden externa ('id' u 'order_id').", False

    # Verificación de idempotencia
    orden_existente = Orden.objects.filter(
        restaurante=restaurante,
        canal_venta=canal_db,
        order_id_externo=order_id_ext
    ).first()
    if orden_existente:
        return True, orden_existente, f"Orden #{orden_existente.id} ya había sido procesada previamente.", True

    cliente_info = payload.get("cliente") or payload.get("customer") or {}
    if isinstance(cliente_info, dict):
        nombre_cliente = cliente_info.get("nombre") or cliente_info.get("name") or f"Cliente {canal_db}"
        telefono_cliente = cliente_info.get("telefono") or cliente_info.get("phone") or ""
    else:
        nombre_cliente = str(cliente_info)
        telefono_cliente = ""

    repartidor_info = payload.get("repartidor") or payload.get("delivery_partner") or payload.get("courier") or {}
    detalles_raw = payload.get("detalles_entrega") or ""
    if isinstance(detalles_raw, dict):
        detalles_parts = [f"{k}: {v}" for k, v in detalles_raw.items() if v]
        detalles_entrega = ", ".join(detalles_parts)
    else:
        detalles_entrega = str(detalles_raw)

    if isinstance(repartidor_info, dict) and repartidor_info.get("nombre"):
        detalles_entrega = f"Repartidor: {repartidor_info.get('nombre')} ({repartidor_info.get('vehiculo', 'Moto/Bici')}) - {detalles_entrega}".strip(" - ")

    items_raw = payload.get("items") or payload.get("cart") or payload.get("order_items") or []
    if not items_raw:
        return False, None, "La orden de delivery no contiene ítems.", False

    try:
        with transaction.atomic():
            # Crear Orden en curso asociada al canal y restaurante
            orden = Orden.objects.create(
                restaurante=restaurante,
                cliente=f"{nombre_cliente[:35]} ({canal_db})",
                estado=Orden.ESTADO_EN_CURSO,
                canal_venta=canal_db,
                tipo_pago=payload.get("tipo_pago") or "Delivery",
                order_id_externo=order_id_ext,
                detalles_entrega=detalles_entrega[:250],
                descuento=float(payload.get("descuento") or 0.0)
            )

            total_calculado = 0.0

            for it in items_raw:
                sku_externo = str(it.get("sku") or it.get("external_id") or it.get("id") or "").strip()
                nombre_item = str(it.get("nombre") or it.get("title") or it.get("name") or "").strip()
                cantidad = int(it.get("cantidad") or it.get("quantity") or 1)
                precio_unitario_payload = it.get("precio_unitario") or it.get("price")

                # SKU Mapping:
                # 1. Buscar en PlatoPrecioCanal por sku_externo y canal
                plato = None
                precio_tier_obj = PlatoPrecioCanal.objects.filter(
                    plato__restaurante=restaurante,
                    canal=canal_db,
                    sku_externo=sku_externo
                ).select_related('plato').first()

                if precio_tier_obj:
                    plato = precio_tier_obj.plato
                    precio_aplicado = float(precio_tier_obj.precio)
                else:
                    # 2. Fallback por ID de plato o por Nombre exacto
                    if sku_externo.isdigit():
                        plato = Plato.objects.filter(restaurante=restaurante, id=int(sku_externo)).first()
                    if not plato and nombre_item:
                        plato = Plato.objects.filter(restaurante=restaurante, nombre__iexact=nombre_item).first()

                    if plato:
                        precio_aplicado = plato.get_precio_para_canal(canal_db)
                    else:
                        # Si no existe en catálogo, usar precio que viene en el payload
                        precio_aplicado = float(precio_unitario_payload or 0.0)

                # Si el payload traía un precio explícito, respetarlo si no hubo tier
                if precio_unitario_payload is not None and (not plato or precio_aplicado == 0):
                    precio_aplicado = float(precio_unitario_payload)

                OrdenItem.objects.create(
                    orden=orden,
                    plato=plato,
                    cantidad=cantidad,
                    precio_unitario=precio_aplicado
                )
                total_calculado += (precio_aplicado * cantidad)

            orden.monto_total = max(round(total_calculado - orden.descuento, 2), 0.0)
            orden.save(update_fields=["monto_total"])

            logger.info("Orden de delivery #%s (%s) creada exitosamente por API. ExtID: %s", orden.id, canal_db, order_id_ext)
            return True, orden, f"Orden de delivery #{orden.id} creada e inyectada al KDS exitosamente.", False

    except Exception as e:
        logger.exception("Error al procesar orden de delivery externa: %s", e)
        return False, None, f"Error al procesar orden de delivery: {str(e)}", False
