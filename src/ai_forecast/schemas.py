"""
Pydantic v2 guardrail schemas for AI demand forecasting and purchase order suggestions.
Feature F21 — MBAn UAI 2026-B Track A.
"""

import json
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict, ValidationError


class InsumoSugerido(BaseModel):
    """
    Representa la recomendación de reabastecimiento para una materia prima individual.
    Asegura cantidades no negativas, costos coherentes y justificación operativa auditada.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    insumo_id: int = Field(..., gt=0, description="ID único del insumo en la base de datos")
    codigo: str = Field(..., min_length=1, description="Código SKU o nemotécnico del insumo")
    nombre: str = Field(..., min_length=1, description="Nombre descriptivo de la materia prima")
    unidad_medida: str = Field(..., min_length=1, description="Unidad de medida culinaria (kg, lt, un, etc.)")
    stock_actual: float = Field(..., description="Stock físico actual (puede ser negativo por desfases operativos de cocina)")
    stock_minimo: float = Field(..., ge=0.0, description="Nivel mínimo de stock de seguridad")
    consumo_diario_estimado: float = Field(..., ge=0.0, description="Consumo diario proyectado")
    cantidad_sugerida: float = Field(..., ge=0.0, description="Cantidad a ordenar recomendada (debe ser >= 0.0)")
    costo_unitario: float = Field(..., ge=0.0, description="Costo unitario en pesos chilenos ($)")
    costo_subtotal: float = Field(..., ge=0.0, description="Costo total estimado para esta línea (cantidad * costo_unitario)")
    justificacion: str = Field(..., min_length=1, description="Fundamento técnico de la sugerencia (no puede ser vacía)")

    @field_validator("codigo", "nombre", "unidad_medida", "justificacion", mode="before")
    @classmethod
    def validar_strings_no_vacios(cls, v: Any, info: Any = None) -> Any:
        if v is None:
            raise ValueError("El campo no puede estar vacío ni contener solo espacios en blanco.")
        if isinstance(v, str):
            v_stripped = v.strip()
            if not v_stripped:
                raise ValueError("El campo no puede estar vacío ni contener solo espacios en blanco.")
            return v_stripped
        return v

    @model_validator(mode="after")
    def validar_costo_subtotal_coherente(self) -> "InsumoSugerido":
        calculado = round(self.cantidad_sugerida * self.costo_unitario, 2)
        if abs(self.costo_subtotal - calculado) > 0.05:
            object.__setattr__(self, "costo_subtotal", calculado)
        return self


class SugerenciaOrdenCompra(BaseModel):
    """
    Contenedor estructurado de la orden de compra sugerida completa.
    Validado estrictamente antes de responder a la API o al dashboard de inventario.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    items_sugeridos: List[InsumoSugerido] = Field(..., description="Lista de insumos analizados para compra")
    presupuesto_estimado_total: float = Field(..., ge=0.0, description="Monto total estimado para la orden de compra")
    periodo_dias: int = Field(default=7, ge=1, description="Horizonte de días de la proyección")
    metodo: str = Field(default="LLM_GENERATED", description="Método generador: 'LLM_GENERATED' o 'HEURISTIC_FALLBACK'")
    fecha_generacion: Optional[str] = Field(default=None, description="Timestamp ISO de generación de la recomendación")

    @field_validator("items_sugeridos", mode="before")
    @classmethod
    def validar_items_lista(cls, v: Any, info: Any = None) -> Any:
        if not isinstance(v, list):
            raise ValueError("El campo 'items_sugeridos' debe ser una lista de ítems sugeridos.")
        return v

    @model_validator(mode="after")
    def reconciliar_presupuesto_total(self) -> "SugerenciaOrdenCompra":
        suma_items = round(sum(it.costo_subtotal for it in self.items_sugeridos), 2)
        if abs(self.presupuesto_estimado_total - suma_items) > 1.0:
            object.__setattr__(self, "presupuesto_estimado_total", suma_items)
        return self


def parse_and_validate_forecast_json(raw_text: str) -> SugerenciaOrdenCompra:
    """
    Extrae, sanea y valida el JSON devuelto por el LLM contra el esquema Pydantic v2.
    Limpia bloques de código markdown (```json ... ```) y preámbulos conversacionales.
    Lanza ValidationError o json.JSONDecodeError si no es recuperable.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("La respuesta del LLM está vacía.")

    texto = raw_text.strip()

    # 1. Extraer bloque markdown ```json ... ``` si existe
    match_codeblock = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if match_codeblock:
        texto = match_codeblock.group(1).strip()
    else:
        # 2. Extraer el primer objeto JSON balanceado mediante delimitadores { ... }
        match_brace = re.search(r"(\{.*\})", texto, re.DOTALL)
        if match_brace:
            texto = match_brace.group(1).strip()

    datos = json.loads(texto)
    return SugerenciaOrdenCompra.model_validate(datos)
