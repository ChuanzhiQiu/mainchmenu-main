"""
AI Demand Forecasting & Inventory Optimization Package.
Provides Pydantic guardrails, consumption aggregation, LLM forecasting, and deterministic fallback.
"""

from .schemas import SugerenciaOrdenCompra, InsumoSugerido
from .fallback import calcular_reorden_heuristico

__all__ = [
    "SugerenciaOrdenCompra",
    "InsumoSugerido",
    "calcular_reorden_heuristico",
]
