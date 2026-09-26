"""
Menu/services/auth_service.py: Layer 2 Authentication, PIN Security & Shift Operations.
Provides PIN validation with 5-attempt rate-limiting and 60-second database/session lockout,
session lock management, and supervisor override.
"""
from decimal import Decimal
import logging
from typing import Optional, Tuple, Dict, Any

from django.utils import timezone
from django.core import signing
from django.conf import settings
from Menu.models import Cajero, TurnoCaja, Restaurante, Orden

logger = logging.getLogger(__name__)
CASHIER_PIN_SALT = getattr(settings, 'CASHIER_PIN_SALT', 'mainch.cashier.pin')


def verify_and_unlock_cashier(
    request,
    restaurante: Restaurante,
    cajero_id: int,
    pin: str
) -> Tuple[bool, Dict[str, Any], int]:
    """
    Verifica el PIN de un cajero con rate limiting (5 intentos max / 60s lockout)
    y actualiza la sesión de backend.
    Retorna (éxito: bool, respuesta: dict, http_status: int).
    """
    # 1. Verificar bloqueo previo de la terminal en sesión
    bloqueo_sesion = request.session.get('terminal_bloqueado_hasta')
    if bloqueo_sesion:
        try:
            expira = timezone.datetime.fromisoformat(bloqueo_sesion)
            if timezone.is_naive(expira):
                expira = timezone.make_aware(expira)
            now = timezone.now()
            if now < expira:
                segundos = max(1, int((expira - now).total_seconds()))
                return False, {
                    "success": False,
                    "error": f"Terminal bloqueada por seguridad. Intente nuevamente en {segundos} segundos.",
                    "bloqueado": True,
                    "segundos_restantes": segundos
                }, 423
            else:
                request.session.pop('terminal_bloqueado_hasta', None)
        except Exception:
            request.session.pop('terminal_bloqueado_hasta', None)

    # 2. Obtener cajero (verificando estricto aislamiento por restaurante)
    cajero = Cajero.all_objects.filter(id=cajero_id, restaurante=restaurante, activo=True).first()
    if not cajero:
        return False, {"success": False, "error": "Cajero no encontrado o inactivo para este restaurante."}, 404

    # 3. Verificar si el cajero en base de datos está bloqueado
    if cajero.is_locked():
        segundos = cajero.segundos_bloqueo_restantes()
        request.session['terminal_bloqueado_hasta'] = cajero.bloqueado_hasta.isoformat()
        return False, {
            "success": False,
            "error": f"Cajero bloqueado por seguridad. Intente nuevamente en {segundos} segundos.",
            "bloqueado": True,
            "segundos_restantes": segundos
        }, 423

    # 4. Validar PIN
    if not cajero.check_pin(pin):
        bloqueado, intentos, segundos = cajero.registrar_intento_fallido()
        if bloqueado:
            request.session['terminal_bloqueado_hasta'] = cajero.bloqueado_hasta.isoformat()
            logger.warning(f"Cajero {cajero.id} ({cajero.nombre}) bloqueado tras {intentos} intentos fallidos.")
            return False, {
                "success": False,
                "error": "Has superado el límite de 5 intentos fallidos. Terminal bloqueada por 60 segundos.",
                "bloqueado": True,
                "segundos_restantes": 60,
                "intentos_restantes": 0
            }, 423
        else:
            intentos_restantes = max(0, 5 - intentos)
            return False, {
                "success": False,
                "error": "PIN incorrecto.",
                "bloqueado": False,
                "intentos_restantes": intentos_restantes
            }, 401

    # 5. PIN Correcto: Limpiar intentos y sincronizar sesión
    cajero.limpiar_intentos_fallidos()
    request.session.pop('terminal_bloqueado_hasta', None)
    request.session['cajero_id'] = cajero.id
    request.session['cajero_nombre'] = cajero.nombre
    request.session['cajero_bloqueado'] = False
    request.session['active_tenant_slug'] = restaurante.slug

    # Buscar turno abierto existente para este cajero en este restaurante
    turno = TurnoCaja.all_objects.filter(
        restaurante=restaurante, cajero=cajero, estado=TurnoCaja.ESTADO_ABIERTO
    ).first()
    if turno:
        request.session['turno_id'] = turno.id
    else:
        request.session.pop('turno_id', None)

    # Generar token firmado
    token_payload = {
        'restaurante_id': restaurante.id,
        'cajero_id': cajero.id,
        'turno_id': turno.id if turno else None,
        'timestamp': timezone.now().isoformat()
    }
    token = signing.dumps(token_payload, salt=CASHIER_PIN_SALT, compress=True)

    return True, {
        "success": True,
        "message": f"Sesión iniciada exitosamente para {cajero.nombre}.",
        "token": token,
        "cajero": {
            "id": cajero.id,
            "nombre": cajero.nombre,
            "codigo": cajero.codigo_empleado
        },
        "turno": {
            "id": turno.id if turno else None,
            "abierto": turno is not None,
            "monto_inicial": float(turno.monto_inicial) if turno else None
        }
    }, 200


def lock_terminal_session(request) -> Dict[str, Any]:
    """Bloquea la terminal en la sesión de backend."""
    request.session['cajero_bloqueado'] = True
    return {"success": True, "bloqueado": True, "message": "Terminal bloqueada exitosamente."}


def supervisor_reset_lockout(
    request,
    restaurante: Restaurante,
    cajero_id: int,
    admin_user
) -> Tuple[bool, str]:
    """Supervisor con credenciales de administrador desbloquea inmediatamente un cajero."""
    cajero = Cajero.all_objects.filter(id=cajero_id, restaurante=restaurante).first()
    if not cajero:
        return False, "Cajero no encontrado en este restaurante."
    cajero.limpiar_intentos_fallidos()
    if hasattr(request, "session"):
        request.session.pop('terminal_bloqueado_hasta', None)
    logger.info(f"Supervisor {admin_user.username} reseteó el bloqueo del cajero {cajero.id} ({cajero.nombre}).")
    return True, f"Bloqueo del cajero '{cajero.nombre}' reseteado exitosamente."
