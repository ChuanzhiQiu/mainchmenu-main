"""
Observabilidad de configuración de IA para el motor de previsión de inventario.

Determina dinámicamente, sin efectuar llamadas externas, si existe una API de LLM
configurada en el entorno y con qué modelo. Estos valores se inyectan en el contexto
del template y en la respuesta JSON del endpoint de sugerencias para reflejar el
estado real del motor (LLM asistido vs. Heurística ROP local).
"""
import os
from typing import Dict, Optional

# Orden de prioridad para detectar la API key activa y el modelo por defecto.
_PROVIDERS = (
    ("GEMINI_API_KEY", "gemini-1.5-flash"),
    ("DEEPSEEK_API_KEY", "deepseek-chat"),
    ("OPENAI_API_KEY", "gpt-4o-mini"),
    ("LLM_API_KEY", "gemini-1.5-flash"),
)

_MODEL_ENV_VARS = ("IA_MODELO", "LLM_MODEL")


def _leer_variable(nombre: str) -> Optional[str]:
    valor = (os.environ.get(nombre) or "").strip()
    return valor or None


def detectar_configuracion_ia() -> Dict[str, object]:
    """
    Retorna:
        ia_activa: bool  -> True si existe una API key configurada y no vacía.
        ia_modelo: str|None -> modelo configurado (env IA_MODELO/LLM_MODEL) o
                               el default del proveedor detectado; None si no hay API.
        ia_motor_label: str -> etiqueta legible del motor activo.
    """
    api_key: Optional[str] = None
    modelo_default: Optional[str] = None

    for env_name, default_model in _PROVIDERS:
        key = _leer_variable(env_name)
        if key:
            api_key = key
            modelo_default = default_model
            break

    ia_activa = api_key is not None

    modelo_explicito = None
    for env_name in _MODEL_ENV_VARS:
        modelo_explicito = _leer_variable(env_name)
        if modelo_explicito:
            break

    ia_modelo = modelo_explicito or (modelo_default if ia_activa else None)

    if ia_activa:
        ia_motor_label = f"Asistido por LLM ({ia_modelo}) + Heurística ROP"
    else:
        ia_motor_label = "Heurística ROP Local (Cálculo determinista sin IA externa)"

    return {
        "ia_activa": ia_activa,
        "ia_modelo": ia_modelo,
        "ia_motor_label": ia_motor_label,
    }
