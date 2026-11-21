"""
Service for Delivery Platform Integrations (Uber Eats & Pedidos Ya).
Handles Webhook ingestion, SKU Mapping resolution, Price Tier application,
and atomic order injection into the Kitchen Display System (KDS).
"""

import logging
from typing import Dict, Any, Tuple, Optional
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
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
                descuento=Decimal(str(payload.get("descuento") or 0))
            )

            total_calculado = Decimal('0.00')

            # 1. Normalizar payload de ítems (sin tocar aún la BD por ítem).
            items_normalizados = []
            for it in items_raw:
                items_normalizados.append({
                    "sku": str(it.get("sku") or it.get("external_id") or it.get("id") or "").strip(),
                    "nombre": str(it.get("nombre") or it.get("title") or it.get("name") or "").strip(),
                    "cantidad": int(it.get("cantidad") or it.get("quantity") or 1),
                    "precio_payload": it.get("precio_unitario") or it.get("price"),
                })

            skus = {i["sku"] for i in items_normalizados if i["sku"]}

            # 2. Resolución en bloque del SKU Mapping (Price Tier por canal).
            tiers_por_sku = {}
            if skus:
                for tier in (
                    PlatoPrecioCanal.objects.filter(
                        plato__restaurante=restaurante,
                        canal=canal_db,
                        sku_externo__in=skus,
                    ).select_related('plato').order_by('id')
                ):
                    tiers_por_sku.setdefault(tier.sku_externo, tier)

            # 3. Fallback en bloque por ID de plato o por nombre exacto.
            ids_fallback = {
                int(i["sku"]) for i in items_normalizados
                if i["sku"].isdigit() and i["sku"] not in tiers_por_sku
            }
            platos_por_id = {
                p.id: p for p in Plato.objects.filter(restaurante=restaurante, id__in=ids_fallback)
            }

            nombres_pendientes = {
                i["nombre"] for i in items_normalizados
                if i["nombre"] and i["sku"] not in tiers_por_sku and not (
                    i["sku"].isdigit() and int(i["sku"]) in platos_por_id
                )
            }
            platos_por_nombre = {}
            if nombres_pendientes:
                filtro_nombres = Q()
                for nombre in nombres_pendientes:
                    filtro_nombres |= Q(nombre__iexact=nombre)
                for plato in Plato.objects.filter(filtro_nombres, restaurante=restaurante):
                    platos_por_nombre.setdefault(plato.nombre.lower(), plato)

            # 4. Price tiers de los platos resueltos por fallback (una sola consulta).
            platos_resueltos = list(platos_por_id.values()) + list(platos_por_nombre.values())
            plato_ids_resueltos = {p.id for p in platos_resueltos}
            tiers_por_plato = {}
            if plato_ids_resueltos:
                for tier in PlatoPrecioCanal.objects.filter(
                    restaurante=restaurante,
                    canal=canal_db,
                    disponible=True,
                    plato_id__in=plato_ids_resueltos,
                ).order_by('id'):
                    tiers_por_plato.setdefault(tier.plato_id, tier)

            orden_items = []
            for item in items_normalizados:
                sku_externo = item["sku"]
                nombre_item = item["nombre"]
                cantidad = item["cantidad"]
                precio_unitario_payload = item["precio_payload"]

                tier = tiers_por_sku.get(sku_externo) if sku_externo else None
                plato = tier.plato if tier else None
                precio_aplicado = tier.precio if tier else None

                if plato is None:
                    if sku_externo.isdigit():
                        plato = platos_por_id.get(int(sku_externo))
                    if plato is None and nombre_item:
                        plato = platos_por_nombre.get(nombre_item.lower())

                    if plato is not None:
                        precio_tier = tiers_por_plato.get(plato.id)
                        if precio_tier is not None and precio_tier.precio > 0:
                            precio_aplicado = precio_tier.precio
                        else:
                            precio_aplicado = Decimal(str(plato.valor))
                    else:
                        # Si no existe en catálogo, usar precio que viene en el payload
                        precio_aplicado = Decimal(str(precio_unitario_payload or 0))

                # Si el payload traía un precio explícito, respetarlo si no hubo tier
                if precio_unitario_payload is not None and (not plato or precio_aplicado == 0):
                    precio_aplicado = Decimal(str(precio_unitario_payload))

                orden_items.append(OrdenItem(
                    restaurante=restaurante,
                    orden=orden,
                    plato=plato,
                    cantidad=cantidad,
                    precio_unitario=precio_aplicado,
                ))
                total_calculado += (precio_aplicado * cantidad)

            OrdenItem.objects.bulk_create(orden_items)

            orden.monto_total = max(
                (total_calculado - orden.descuento).quantize(Decimal('0.01')),
                Decimal('0.00')
            )
            orden._skip_total_recalc = True
            orden.save(update_fields=["monto_total"])

            logger.info("Orden de delivery #%s (%s) creada exitosamente por API. ExtID: %s", orden.id, canal_db, order_id_ext)
            return True, orden, f"Orden de delivery #{orden.id} creada e inyectada al KDS exitosamente.", False

    except Exception as e:
        logger.exception("Error al procesar orden de delivery externa: %s", e)
        return False, None, f"Error al procesar orden de delivery: {str(e)}", False
