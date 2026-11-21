"""
Menu/module_gates.py: Puertas de acceso a los módulos opcionales por restaurante.

Cada restaurante (`Restaurante`) expone flags booleanos que habilitan o deshabilitan
verticales completas del POS (inventario, salones/mesas, delivery, cajeros).

Este módulo centraliza:
- `MODULE_REGISTRY`: mapa `module_key -> (campo_del_modelo, etiqueta_legible)`.
- `is_module_enabled()`: consulta tolerante a `None` (sin tenant => habilitado).
- `@module_required`: decorador para vistas HTML y JSON que responde 403 cuando el
  módulo está deshabilitado.
- `ModuloSalonesMesasPermission`: permiso DRF equivalente para los ViewSets.
"""
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.permissions import BasePermission

# module_key -> (nombre del campo en Restaurante, etiqueta para el usuario final)
MODULE_REGISTRY = {
    'inventario': ('modulo_inventario', 'Inventario, Recetas e IA'),
    'salones_mesas': ('modulo_salones_mesas', 'Salones y Mesas'),
    'delivery': ('modulo_delivery', 'Delivery, Webhooks y Precios por Canal'),
    'cajeros': ('modulo_cajeros', 'Cajeros, PIN y Turnos'),
}


def _module_meta(module_key):
    """Retorna (campo, etiqueta) para `module_key` o levanta ValueError."""
    try:
        return MODULE_REGISTRY[module_key]
    except KeyError:
        raise ValueError(
            f"Módulo desconocido: '{module_key}'. Válidos: {sorted(MODULE_REGISTRY)}"
        )


def is_module_enabled(restaurante, module_key) -> bool:
    """
    Indica si el módulo `module_key` está habilitado para `restaurante`.

    Sin restaurante en contexto se asume habilitado (compatibilidad legacy y
    rutas públicas que aún no resuelven tenant).
    """
    campo, _label = _module_meta(module_key)
    if restaurante is None:
        return True
    return bool(getattr(restaurante, campo, True))


def _resolver_restaurante(request):
    """Resuelve el restaurante activo sin acoplarse al import de Menu.views."""
    restaurante = getattr(request, 'restaurante', None)
    if restaurante is not None:
        return restaurante

    from Menu.views import get_current_restaurante
    return get_current_restaurante(request)


def _es_peticion_json(request, json_response):
    """Determina si la respuesta de bloqueo debe ser JSON (API/AJAX) o HTML."""
    if json_response is not None:
        return bool(json_response)

    if '/api/' in (getattr(request, 'path', '') or ''):
        return True
    if 'application/json' in (request.headers.get('Accept', '') or ''):
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    return False


def module_required(module_key, json_response=None):
    """
    Decorador que bloquea el acceso a una vista cuando el módulo no está habilitado
    para el restaurante activo.

    - `json_response=True`  -> siempre JSON 403.
    - `json_response=False` -> siempre HTML 403 (`Menu/modulo_desactivado.html`).
    - `json_response=None`  -> autodetecta según path/Accept/X-Requested-With.
    """
    campo, label = _module_meta(module_key)
    _ = campo  # el campo vive en el modelo; el gate delega en is_module_enabled()

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            restaurante = _resolver_restaurante(request)

            if restaurante is not None and not is_module_enabled(restaurante, module_key):
                if _es_peticion_json(request, json_response):
                    return JsonResponse(
                        {
                            'error': f'El módulo {label} no está habilitado para este restaurante.',
                            'modulo': module_key,
                        },
                        status=403,
                    )
                return render(
                    request,
                    'Menu/modulo_desactivado.html',
                    {'modulo_key': module_key, 'modulo_label': label},
                    status=403,
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


class ModuloSalonesMesasPermission(BasePermission):
    """Deniega el acceso a los endpoints de salones/mesas si el módulo está apagado."""

    message = 'El módulo Salones y Mesas no está habilitado para este restaurante.'

    def has_permission(self, request, view):
        from Menu.views import get_current_restaurante

        restaurante = getattr(request, 'restaurante', None) or get_current_restaurante(request)
        if restaurante is None:
            return True
        return bool(getattr(restaurante, 'modulo_salones_mesas', True))
