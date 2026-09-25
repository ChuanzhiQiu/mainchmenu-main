"""
AI Demand Forecasting and Historical Consumption Aggregation Service.
Coordinates recipe explosion, prompt synthesis, LLM invocation, guardrails validation, and heuristic fallback.
Feature F19 — MBAn UAI 2026-B Track A.
"""

from collections import defaultdict
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from Menu.models import Insumo, Orden, OrdenItem, RecetaItem
from src.ai_forecast.fallback import calcular_reorden_heuristico
from src.ai_forecast.schemas import InsumoSugerido, SugerenciaOrdenCompra, parse_and_validate_forecast_json

logger = logging.getLogger(__name__)


def calcular_consumo_diario_insumos(
    dias_historia: int = 30,
    factor_demanda: float = 1.0,
) -> Dict[int, float]:
    """
    Agrega el consumo histórico de ventas de órdenes 'Completada',
    explotando platos directos y platos de combos (Menu), dividido por los días observados.
    """
    # 1. Filtrar órdenes válidas (excluir Eliminadas y considerar Completadas)
    qs_ordenes = Orden.objects.filter(estado=Orden.ESTADO_COMPLETADA)
    if dias_historia and dias_historia > 0:
        fecha_corte = timezone.now().date() - timezone.timedelta(days=dias_historia)
        qs_ordenes = qs_ordenes.filter(fecha__gte=fecha_corte)

    if not qs_ordenes.exists():
        return {}

    # 2. Determinar ventana de días activos para promediar
    fechas = list(qs_ordenes.values_list("fecha", flat=True).distinct())
    num_dias = max(1, len(fechas))

    # 3. Explotar items de orden a platos (directos + combos)
    platos_totales: Dict[int, int] = defaultdict(int)
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
            platos_totales[item.plato_id] += cant
        if item.menu_id and item.menu:
            for plato_combo in item.menu.platos.all():
                platos_totales[plato_combo.id] += cant

    if not platos_totales:
        return {}

    # 4. Multiplicar por recetas de escandallo (RecetaItem)
    insumo_demandas: Dict[int, Decimal] = defaultdict(Decimal)
    recetas = (
        RecetaItem.objects
        .filter(plato_id__in=platos_totales.keys())
        .select_related("insumo")
    )

    for rec in recetas:
        cant_platos = Decimal(str(platos_totales[rec.plato_id]))
        cant_receta = Decimal(str(rec.cantidad))
        insumo_demandas[rec.insumo_id] += cant_platos * cant_receta

    # 5. Calcular consumo diario promedio ponderado por factor de demanda
    factor = Decimal(str(max(0.1, factor_demanda)))
    consumo_diario: Dict[int, float] = {}

    for insumo_id, total_consumo in insumo_demandas.items():
        promedio = (total_consumo / Decimal(str(num_dias))) * factor
        consumo_diario[insumo_id] = round(float(promedio), 3)

    return consumo_diario


def llm_call(prompt: str, system_prompt: str = "") -> str:
    """
    Función de llamada a la API de Google Gemini (v1beta/models/gemini-1.5-flash:generateContent).
    Usa la librería estándar `urllib.request` para no requerir SDKs externos adicionales.
    Si no hay API key o la llamada falla/timeout, lanza RuntimeError para activar el fallback heurístico (ROP).
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("No LLM API key configured in environment. Triggering graceful fallback.")

    import urllib.request
    import urllib.error

    # URL del endpoint de la API de Google AI Studio (Gemini 1.5 Flash)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

    headers = {
        "Content-Type": "application/json"
    }

    # Estructura de payload oficial de la API de Gemini
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_prompt}\n\n{prompt}" if system_prompt else prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            # Extraer texto generado por Gemini
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            raise ValueError("Respuesta vacía o malformada de la API de Gemini.")
    except Exception as err:
        logger.warning(f"Error al invocar la API de Gemini: {err}")
        raise RuntimeError(f"Fallo en API de Gemini: {err}") from err



def _extraer_json_limpio(texto_raw: str) -> Dict[str, Any]:
    """Limpia wrappers de Markdown ```json ... ``` y extrae diccionario JSON válido."""
    texto = texto_raw.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)
    return json.loads(texto.strip())


def generar_sugerencias_compra(
    dias_proyeccion: int = 7,
    usar_llm: bool = True,
    dias_historia: int = 30,
    factor_demanda: float = 1.0,
    insumos: Optional[List[Insumo]] = None,
    dias_lead_time: int = 2,
) -> SugerenciaOrdenCompra:
    """
    Servicio principal de previsión de demanda y generación de sugerencias de compra.
    """
    # 1. Normalización de parámetros
    try:
        dias_proyeccion = int(dias_proyeccion)
        if dias_proyeccion <= 0:
            dias_proyeccion = 7
    except (ValueError, TypeError):
        dias_proyeccion = 7

    # 2. Obtención de insumos activos
    if insumos is None:
        insumos = list(Insumo.objects.filter(activo=True).order_by("id"))

    if not insumos:
        return SugerenciaOrdenCompra(
            items_sugeridos=[],
            presupuesto_estimado_total=0.0,
            periodo_dias=dias_proyeccion,
            metodo="HEURISTIC_FALLBACK",
        )

    # 3. Agregación de consumo histórico
    consumos_diarios = calcular_consumo_diario_insumos(
        dias_historia=dias_historia,
        factor_demanda=factor_demanda,
    )

    # 4. Decisión de ejecución: Heurístico vs LLM
    if not usar_llm:
        return calcular_reorden_heuristico(
            insumos=insumos,
            consumos_diarios=consumos_diarios,
            dias_lead_time=dias_lead_time,
            dias_proyeccion=dias_proyeccion,
        )

    # 5. Flujo LLM con Guardrails
    try:
        # Cargar prompt del sistema versionado
        prompt_path = Path(settings.BASE_DIR) / "src" / "prompts" / "demand_forecaster_system.md"
        system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

        # Construir payload de insumos
        datos_insumos = []
        for ins in insumos:
            datos_insumos.append({
                "insumo_id": ins.id,
                "codigo": ins.codigo,
                "nombre": ins.nombre,
                "unidad_medida": ins.unidad_medida,
                "stock_actual": float(ins.stock_actual),
                "stock_minimo": float(ins.stock_minimo),
                "consumo_diario_estimado": consumos_diarios.get(ins.id, 0.0),
                "costo_unitario": float(ins.costo_unitario),
            })

        user_message = (
            f"Por favor calcula la orden de compra sugerida para los siguientes insumos "
            f"para un horizonte de proyección de {dias_proyeccion} días:\n\n"
            f"{json.dumps(datos_insumos, ensure_ascii=False, indent=2)}"
        )

        # Invocación con captura de timeout / errores
        llm_response = llm_call(prompt=user_message, system_prompt=system_prompt)
        resultado = parse_and_validate_forecast_json(llm_response)
        return resultado

    except Exception as e:
        logger.warning(
            "Fallo o timeout en LLM / Guardrails (%s). Activando recuperación con Fallback Heurístico (ROP).",
            e,
        )
        return calcular_reorden_heuristico(
            insumos=insumos,
            consumos_diarios=consumos_diarios,
            dias_lead_time=dias_lead_time,
            dias_proyeccion=dias_proyeccion,
        )
