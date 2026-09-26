"""
Deterministic Heuristic Fallback Engine for Reorder Point (ROP) Supply Chain Calculation.
Feature F22 — MBAn UAI 2026-B Track A.

Formula:
    Demanda_Lead_Time = Consumo_Diario * Lead_Time
    ROP = Demanda_Lead_Time + Stock_Minimo
    Deficit = ROP - Stock_Actual
    Sugerido = max(0, Deficit)
"""

from decimal import Decimal
from typing import Dict, List, Optional, Any
from datetime import timedelta
from django.utils import timezone

from src.ai_forecast.schemas import SugerenciaOrdenCompra, InsumoSugerido


def calcular_consumo_diario_historico(
    insumo_ids: List[int],
    dias_historial: int = 30,
    restaurante: Optional[Any] = None,
) -> Dict[int, float]:
    """
    Calcula el consumo diario promedio de cada insumo analizando órdenes completadas
    en los últimos `dias_historial` días, desglosando platos y combos (Menu).
    """
    from Menu.models import Orden, OrdenItem, RecetaItem
    if restaurante is None:
        from Menu.tenant_context import get_current_tenant
        restaurante = get_current_tenant()

    consumos: Dict[int, float] = {i_id: 0.0 for i_id in insumo_ids}
    if not insumo_ids:
        return consumos

    dias_efectivos = max(1, int(dias_historial))
    cutoff = timezone.now() - timedelta(days=dias_efectivos)

    # Filtrar órdenes completadas
    qs_ordenes = Orden.objects.filter(estado=Orden.ESTADO_COMPLETADA)
    if restaurante is not None:
        qs_ordenes = qs_ordenes.filter(restaurante=restaurante)
    # Intentar por fecha_completada si existe
    if qs_ordenes.filter(fecha_completada__isnull=False).exists():
        qs_ordenes = qs_ordenes.filter(fecha_completada__gte=cutoff)
    else:
        qs_ordenes = qs_ordenes.filter(fecha__gte=cutoff.date())

    if not qs_ordenes.exists():
        return consumos

    platos_totales: Dict[int, int] = {}
    items = (
        OrdenItem.objects
        .filter(orden__in=qs_ordenes)
        .select_related("plato", "menu")
        .prefetch_related("menu__platos")
    )

    for item in items:
        cant = item.cantidad or 1
        if cant <= 0:
            continue
        if item.plato_id:
            platos_totales[item.plato_id] = platos_totales.get(item.plato_id, 0) + cant
        if item.menu_id and item.menu:
            for plato_combo in item.menu.platos.all():
                platos_totales[plato_combo.id] = platos_totales.get(plato_combo.id, 0) + cant

    if not platos_totales:
        return consumos

    recetas = (
        RecetaItem.objects
        .filter(plato_id__in=platos_totales.keys(), insumo_id__in=insumo_ids)
    )

    total_consumido_por_insumo: Dict[int, Decimal] = {i_id: Decimal("0.000") for i_id in insumo_ids}
    for rec in recetas:
        cant_platos = Decimal(str(platos_totales[rec.plato_id]))
        cant_receta = Decimal(str(rec.cantidad))
        total_consumido_por_insumo[rec.insumo_id] += cant_platos * cant_receta

    for i_id, total in total_consumido_por_insumo.items():
        consumos[i_id] = float(round(total / Decimal(str(dias_efectivos)), 3))

    return consumos


def calcular_reorden_heuristico(
    insumos: Optional[List[Any]] = None,
    dias_lead_time: int = 2,
    dias_proyeccion: int = 7,
    dias_historial: int = 30,
    consumos_diarios: Optional[Dict[int, float]] = None,
    consumos_diarios_dict: Optional[Dict[int, float]] = None,
    solo_reorden: bool = False,
    restaurante: Optional[Any] = None,
) -> SugerenciaOrdenCompra:
    """
    Motor heurístico determinista de reorden (ROP).
    Calcula la necesidad de reposición de insumos aplicando la fórmula:
        sugerido = max(0, (consumo_diario * lead_time) + stock_minimo - stock_actual)
    
    Retorna un objeto SugerenciaOrdenCompra validado con metodo="HEURISTIC_FALLBACK".
    """
    from Menu.models import Insumo
    if restaurante is None:
        from Menu.tenant_context import get_current_tenant
        restaurante = get_current_tenant()

    # 1. Normalización de parámetros operativos
    lead_time = max(0, int(dias_lead_time))
    horizonte = max(1, int(dias_proyeccion))

    # 2. Obtención de insumos si no se suministran
    if insumos is None:
        if restaurante is not None:
            lista_insumos = list(Insumo.objects.filter(restaurante=restaurante, activo=True).order_by("id"))
        else:
            lista_insumos = list(Insumo.objects.filter(activo=True).order_by("id"))
    else:
        lista_insumos = list(insumos)

    if not lista_insumos:
        return SugerenciaOrdenCompra(
            items_sugeridos=[],
            presupuesto_estimado_total=0.0,
            periodo_dias=horizonte,
            metodo="HEURISTIC_FALLBACK"
        )

    # 3. Consumos diarios
    consumos_dict = consumos_diarios if consumos_diarios is not None else consumos_diarios_dict
    insumo_ids = [ins.id for ins in lista_insumos if getattr(ins, "id", None)]

    if consumos_dict is None:
        consumos = calcular_consumo_diario_historico(insumo_ids, dias_historial=dias_historial, restaurante=restaurante)
    else:
        consumos = consumos_dict

    items_sugeridos: List[InsumoSugerido] = []

    # 4. Cálculo determinista de ROP para cada insumo
    for insumo in lista_insumos:
        stock_act = float(insumo.stock_actual if insumo.stock_actual is not None else 0.0)
        stock_min = float(insumo.stock_minimo if insumo.stock_minimo is not None else 0.0)
        costo_unit = float(insumo.costo_unitario if insumo.costo_unitario is not None else 0.0)
        diario = float(consumos.get(insumo.id, 0.0))

        # Fórmula ROP: (consumo_diario * lead_time) + stock_minimo
        demanda_lead_time = diario * lead_time
        punto_reorden = demanda_lead_time + stock_min

        # Déficit = punto_reorden - stock_actual
        deficit = punto_reorden - stock_act
        cantidad_sugerida = max(0.0, round(deficit, 3))

        # Costo subtotal
        costo_subtotal = round(cantidad_sugerida * costo_unit, 2)

        unidad = insumo.unidad_medida or "un"
        if cantidad_sugerida > 0:
            justificacion = (
                f"Reabastecimiento heurístico ROP: Stock actual ({stock_act:.2f} {unidad}) "
                f"inferior al punto de reorden ({punto_reorden:.2f} {unidad}) considerando "
                f"lead time de {lead_time} días y stock de seguridad de {stock_min:.2f} {unidad}."
            )
        else:
            justificacion = (
                f"Stock suficiente: Stock actual ({stock_act:.2f} {unidad}) "
                f"cubre el punto de reorden ({punto_reorden:.2f} {unidad}). No se requiere compra."
            )

        if solo_reorden and cantidad_sugerida <= 0:
            continue

        item = InsumoSugerido(
            insumo_id=insumo.id,
            codigo=insumo.codigo,
            nombre=insumo.nombre,
            unidad_medida=unidad,
            stock_actual=stock_act,
            stock_minimo=stock_min,
            consumo_diario_estimado=round(diario, 3),
            cantidad_sugerida=cantidad_sugerida,
            costo_unitario=costo_unit,
            costo_subtotal=costo_subtotal,
            justificacion=justificacion
        )
        items_sugeridos.append(item)

    presupuesto_total = round(sum(it.costo_subtotal for it in items_sugeridos), 2)

    return SugerenciaOrdenCompra(
        items_sugeridos=items_sugeridos,
        presupuesto_estimado_total=presupuesto_total,
        periodo_dias=horizonte,
        metodo="HEURISTIC_FALLBACK"
    )
