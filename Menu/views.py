import logging
from django.shortcuts import render, redirect, get_object_or_404
from .models import Plato, Orden, Menu, OrdenItem, Insumo, RecetaItem, MovimientoStock
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.utils import timezone
import json
import math
from .utils import imprimir_comanda
from django.core.exceptions import ValidationError
from django.db import transaction
from itertools import groupby
from decimal import Decimal, InvalidOperation
from django.db.models import Sum, Count, Q, F
from datetime import datetime, timedelta
from django.db.models.functions import TruncDay
from collections import Counter
from Menu.services.inventory_service import descontar_stock_orden

from django.contrib.auth import authenticate, login, logout
from functools import wraps
from django.db.models.functions import ExtractHour

logger = logging.getLogger(__name__)


def admin_required(view_func):
    """
    Decorador estricto para proteger operaciones de administración e inventario.
    Limita al usuario de caja:
    - Si el usuario no está autenticado o no es staff/superuser:
      - Si es petición JSON/AJAX: responde con 403 Forbidden.
      - Si es petición web estándar: redirige a la pantalla de login (/login/) con mensaje.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        is_ajax = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or request.headers.get("HX-Request") == "true"
            or request.content_type == "application/json"
            or "application/json" in request.headers.get("Accept", "")
        )
        if not request.user.is_authenticated or not request.user.is_staff:
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "error": "Acceso restringido. Solo el administrador puede realizar esta acción.",
                    "message": "Acceso restringido. Solo el administrador puede realizar esta acción."
                }, status=403)
            messages.warning(request, "Acceso restringido a administradores. Inicia sesión para continuar.")
            return redirect(f"/login/?next={request.path}")
        return view_func(request, *args, **kwargs)
    return _wrapped_view


# --------------------------------- AUTENTICACIÓN ADMIN ---------------------------------

def login_view(request):
    """Vista visual de loggeo para el dueño / administrador."""
    next_url = request.GET.get('next') or request.POST.get('next') or 'Menu:crud'
    error = None

    if request.user.is_authenticated and request.user.is_staff:
        return redirect(next_url)

    if request.method == "POST":
        usuario = request.POST.get("username", "").strip()
        clave = request.POST.get("password", "").strip()

        user = authenticate(request, username=usuario, password=clave)
        if user is not None:
            if user.is_staff or user.is_superuser:
                login(request, user)
                messages.success(request, f"¡Bienvenido(a) Administrador(a) {user.username}!")
                return redirect(next_url)
            else:
                error = "El usuario ingresado no cuenta con privilegios de Administrador."
        else:
            error = "Usuario o contraseña incorrectos. Verifica tus credenciales."

    return render(request, "Menu/login.html", {
        "error": error,
        "next_url": next_url,
        "username": request.POST.get("username", "")
    })


def logout_view(request):
    """Cierra la sesión de administrador y vuelve a la terminal de caja."""
    if request.user.is_authenticated:
        logout(request)
        messages.info(request, "Sesión de administrador cerrada. Terminal operando en Modo Caja.")
    return redirect("Menu:inicio")



# ---------------------------------   INICIO  -----------------------------------------------
def inicio(request):
    pedidos_en_curso = (
        Orden.objects.filter(estado=Orden.ESTADO_EN_CURSO)
        .prefetch_related('items__plato', 'items__menu__platos')
        .order_by('fecha', 'hora', 'id')
    )

    is_htmx = (
        request.headers.get("HX-Request") == "true"
        or request.META.get("HTTP_HX_REQUEST") == "true"
    )

    if is_htmx:
        return render(request, "Menu/partials/kds_board.html", {
            "pedidos_en_curso": pedidos_en_curso,
        })

    # Filtros para el historial
    historial = Orden.objects.filter(estado__in=[Orden.ESTADO_COMPLETADA, Orden.ESTADO_ELIMINADA])

    # Obtener parámetros de filtro
    filtro_estado = request.GET.get("estado")
    filtro_canal = request.GET.get("canal")
    filtro_orden = request.GET.get("orden")  # 'asc' o 'desc'
    filtro_id = request.GET.get("id")

    # Aplicar filtros
    if filtro_estado:
        historial = historial.filter(estado=filtro_estado)
    if filtro_canal:
        historial = historial.filter(canal_venta=filtro_canal)
    if filtro_id:
        historial = historial.filter(id=filtro_id)
    if filtro_orden == "asc":
        historial = historial.order_by("fecha", "hora", "id")
    else:  # Descendente por defecto
        historial = historial.order_by("-fecha", "-hora", "-id")

    historial = historial[:50]  # Limitar a los últimos 50 resultados

    tipos_pago = Orden.PAGO_CHOICES
    estados = Orden.ESTADO_CHOICES
    canales = Orden.CANAL_CHOICES

    return render(request, "Menu/inicio.html", {
        "pedidos_en_curso": pedidos_en_curso,
        "historial_reciente": historial,
        "tipos_pago": tipos_pago,
        "estados": estados,
        "canales": canales,
    })

# Confirmar orden
def confirmar_orden(request, id):
    """
    Hook de confirmación de orden (F11):
    - Transiciona la orden a estado 'Completada'.
    - Valida y actualiza tipo_pago y descuento de forma segura (sin permitir descuentos negativos).
    - Preserva de forma idempotente el timestamp 'fecha_completada'.
    - Invoca atómicamente el servicio de inventario `descontar_stock_orden`.
    - Retorna detalles de alertas de stock mínimo en la respuesta JSON y las propaga a messages framework.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Método no permitido."}, status=405)

    # 1. Obtención segura del objeto Orden sin enmascarar Http404 (TC-B02-29)
    try:
        orden = Orden.objects.get(id=id)
    except Orden.DoesNotExist:
        return JsonResponse({"success": False, "error": "Orden no encontrada.", "message": "Orden no encontrada."}, status=404)

    # 1.1 Guardrail operativo: Rechazar órdenes en estado Eliminada
    if orden.estado == Orden.ESTADO_ELIMINADA:
        logger.warning(
            "Intento de confirmar la orden eliminada #%s (%s). Solicitud rechazada.",
            orden.id, orden.cliente
        )
        return JsonResponse({
            "success": False,
            "error": f"No se puede confirmar la orden #{orden.id} porque está en estado 'Eliminada'.",
            "message": f"No se puede confirmar la orden #{orden.id} porque está en estado 'Eliminada'."
        }, status=400)

    # 2. Parseo seguro del cuerpo JSON o datos de formulario (TC-B02-26, TC-B02-28)
    data = {}
    if request.body:
        try:
            data = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if request.content_type == "application/json":
                return JsonResponse({"success": False, "error": "JSON malformado o inválido.", "message": "JSON malformado o inválido."}, status=400)
            data = request.POST.dict()
    elif request.POST:
        data = request.POST.dict()

    try:
        # 3. Actualizar tipo de pago y estado
        orden.estado = Orden.ESTADO_COMPLETADA
        if data.get("tipo_pago"):
            orden.tipo_pago = data.get("tipo_pago")

        # 4. Manejo de descuento: Respetar montos fijos CLP (no porcentajes)
        # Prevenir inflación por descuentos negativos o corrupción por valores no-finitos (inf/nan)
        if isinstance(data, dict) and "descuento" in data and data.get("descuento") is not None:
            raw_descuento = data.get("descuento")
        else:
            raw_descuento = orden.descuento if orden.descuento is not None else 0.0

        try:
            descuento_num = float(raw_descuento)
            if math.isnan(descuento_num) or math.isinf(descuento_num) or descuento_num < 0.0:
                descuento_num = 0.0
        except (ValueError, TypeError):
            descuento_num = float(orden.descuento or 0.0)

        orden.descuento = round(max(0.0, descuento_num), 2)

        # 5. Idempotencia de timestamp: no sobreescribir si ya estaba completada (TC-C04-02)
        if not orden.fecha_completada:
            orden.fecha_completada = timezone.now()

        # 6. Recalcular total con descuento en CLP
        subtotal = sum(item.subtotal for item in orden.items.all())
        orden.monto_total = max(0.0, round(subtotal - orden.descuento, 2))

        # Guardar estado de la orden
        orden.save(update_fields=["estado", "tipo_pago", "descuento", "monto_total", "fecha_completada"])

        # 7. Ejecutar hook de deducción de stock atómica (F10 / F11)
        alertas_stock = []
        movimientos_count = 0
        error_deduccion = None
        try:
            res_deduccion = descontar_stock_orden(orden.id)
            if res_deduccion and res_deduccion.get("success"):
                alertas_stock = res_deduccion.get("alertas_stock_minimo", [])
                movimientos_count = res_deduccion.get("movimientos", 0)
                orden.refresh_from_db(fields=["stock_descontado"])
            else:
                error_deduccion = (
                    res_deduccion.get("error") if res_deduccion
                    else "Error desconocido en el servicio de deducción de inventario."
                )
                logger.error(
                    "Fallo en la deducción de inventario para orden #%s: %s",
                    orden.id, error_deduccion
                )
        except Exception as e:
            error_deduccion = str(e)
            logger.exception(
                "Excepción no controlada durante la deducción de stock para orden #%s: %s",
                orden.id, e
            )

        # 8. Propagación de alertas y mensajes a Django Messages Framework (para vistas HTML/KDS)
        if error_deduccion:
            messages.error(
                request,
                f"Advertencia: La orden #{orden.id} fue confirmada pero no se pudo descontar el inventario: {error_deduccion}"
            )

        if alertas_stock:
            for alerta in alertas_stock:
                nombre = alerta.get("nombre", "Insumo")
                stock = alerta.get("stock_actual", "")
                um = alerta.get("unidad_medida", "")
                minimo = alerta.get("stock_minimo", "")
                messages.warning(
                    request,
                    f"Alerta de Stock Crítico: '{nombre}' ha bajado a {stock} {um} (Mínimo: {minimo} {um})."
                )

        # 9. Respuesta JSON estructurada (TC-F11-04, TC-B02-30)
        response_payload = {
            "success": True,
            "message": "Orden confirmada exitosamente." if not error_deduccion else f"Orden confirmada, pero falló la deducción de stock: {error_deduccion}",
            "orden_id": orden.id,
            "stock_descontado": orden.stock_descontado,
            "alertas_stock_minimo": alertas_stock,
            "alertas": alertas_stock,
            "movimientos": movimientos_count,
        }
        if error_deduccion:
            response_payload["error_deduccion"] = error_deduccion

        return JsonResponse(response_payload, status=200)

    except Exception as e:
        logger.exception("Error al procesar confirmación de orden #%s: %s", id, e)
        return JsonResponse({"success": False, "message": f"Error al procesar confirmación: {str(e)}"}, status=500)

#ELIMINAR ORDEN
def eliminar_orden(request, id):
    if request.method == "POST":
        try:
            with transaction.atomic():
                orden = get_object_or_404(Orden, id=id)
                orden.estado = Orden.ESTADO_ELIMINADA
                orden.descuento = 0
                orden.tipo_pago = 'No especificado'  # Valor predeterminado válido
                orden.save(update_fields=["estado", "descuento", "tipo_pago"])
                return JsonResponse({"success": True, "message": f"La orden {orden.id} fue eliminada exitosamente."})
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "Método no permitido."}, status=405)

# -------------------------- GENERAR ORDENES Y VERLAS --------------------

# Crear una Orden
def crear_orden(request):
    if request.method == "POST":
        is_json = request.content_type == "application/json"
        is_ajax = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or is_json
            or bool(request.headers.get("HX-Request"))
        )

        data = {}
        if is_json and request.body:
            try:
                data = json.loads(request.body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JsonResponse({"success": False, "message": "JSON malformado."}, status=400)
        else:
            data = request.POST.dict()
            if not data and request.body:
                try:
                    data = json.loads(request.body.decode("utf-8"))
                    is_json = True
                except Exception:
                    pass

        # 1. Validar que el cuerpo raíz sea un diccionario (objeto JSON o datos de formulario)
        if not isinstance(data, dict):
            msg = "Datos de orden inválidos. Se esperaba un objeto JSON o formulario."
            if is_json or is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("Menu:crear_orden")

        cliente = data.get("cliente")
        canal_venta = str(data.get("canal_venta") or "Local").strip() or "Local"
        tipo_pago = str(data.get("tipo_pago") or "Efectivo").strip() or "Efectivo"

        # 2. Validar que cliente sea obligatorio y no vacío
        if not cliente or not str(cliente).strip():
            msg = "El cliente es obligatorio."
            if is_json or is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("Menu:crear_orden")

        cliente = str(cliente).strip()

        # 3. Validar descuento (no negativo, numérico finito)
        raw_descuento = data.get("descuento")
        if raw_descuento is None or str(raw_descuento).strip() == "":
            descuento = 0.0
        else:
            try:
                descuento = float(raw_descuento)
                if math.isnan(descuento) or math.isinf(descuento) or descuento < 0:
                    msg = "El descuento no puede ser negativo ni infinito."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")
            except (ValueError, TypeError):
                msg = "El descuento debe ser un valor numérico válido."
                if is_json or is_ajax:
                    return JsonResponse({"success": False, "message": msg}, status=400)
                messages.error(request, msg)
                return redirect("Menu:crear_orden")

        # 4. Parse y validación de items
        items_payload = data.get("items")
        platos_raw = data.get("platos")
        menus_raw = data.get("menus")

        items_to_create = []

        if items_payload is not None:
            if not isinstance(items_payload, list):
                msg = "El campo 'items' debe ser una lista."
                if is_json or is_ajax:
                    return JsonResponse({"success": False, "message": msg}, status=400)
                messages.error(request, msg)
                return redirect("Menu:crear_orden")

            for it in items_payload:
                if not isinstance(it, dict):
                    msg = "Cada ítem debe ser un objeto válido."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")

                raw_cantidad = it.get("cantidad", 1)
                try:
                    cantidad = int(raw_cantidad)
                except (ValueError, TypeError):
                    msg = "Cantidad no válida."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")

                # Enforce cantidad > 0 (rechaza cantidad <= 0 con HTTP 400)
                if cantidad <= 0:
                    msg = "La cantidad debe ser mayor a cero."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")

                tipo = it.get("tipo")
                raw_id = it.get("id") if it.get("id") is not None else (it.get("plato_id") or it.get("menu_id"))
                if not tipo:
                    if "plato_id" in it:
                        tipo = "plato"
                    elif "menu_id" in it:
                        tipo = "menu"
                    else:
                        tipo = "plato"

                # Enforce non-empty numeric ID
                if raw_id is None or str(raw_id).strip() == "":
                    msg = "ID de ítem obligatorio."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")

                try:
                    obj_id = int(raw_id)
                except (ValueError, TypeError):
                    msg = f"ID de ítem inválido: '{raw_id}'. Se esperaba un número entero."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")

                if tipo == "plato":
                    try:
                        plato = Plato.objects.get(id=obj_id)
                        items_to_create.append(("plato", plato, cantidad))
                    except (Plato.DoesNotExist, ValueError, TypeError):
                        msg = f"Plato con ID {obj_id} no existe."
                        if is_json or is_ajax:
                            return JsonResponse({"success": False, "message": msg}, status=400)
                        messages.error(request, msg)
                        return redirect("Menu:crear_orden")
                elif tipo == "menu":
                    try:
                        menu = Menu.objects.get(id=obj_id)
                        items_to_create.append(("menu", menu, cantidad))
                    except (Menu.DoesNotExist, ValueError, TypeError):
                        msg = f"Menú con ID {obj_id} no existe."
                        if is_json or is_ajax:
                            return JsonResponse({"success": False, "message": msg}, status=400)
                        messages.error(request, msg)
                        return redirect("Menu:crear_orden")
                else:
                    msg = f"Tipo de ítem no válido: '{tipo}'."
                    if is_json or is_ajax:
                        return JsonResponse({"success": False, "message": msg}, status=400)
                    messages.error(request, msg)
                    return redirect("Menu:crear_orden")

        if platos_raw:
            try:
                platos_dict = json.loads(platos_raw) if isinstance(platos_raw, str) else platos_raw
                if isinstance(platos_dict, dict):
                    for plato_id, info in platos_dict.items():
                        cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                        try:
                            cant = int(cant)
                        except (ValueError, TypeError):
                            cant = 0
                        if cant <= 0:
                            msg = "Cantidad no puede ser menor o igual a cero."
                            if is_json or is_ajax:
                                return JsonResponse({"success": False, "message": msg}, status=400)
                            messages.error(request, msg)
                            return redirect("Menu:crear_orden")
                        try:
                            plato_id_int = int(plato_id)
                            plato = Plato.objects.get(id=plato_id_int)
                            items_to_create.append(("plato", plato, cant))
                        except (Plato.DoesNotExist, ValueError, TypeError):
                            if is_json or is_ajax:
                                return JsonResponse({"success": False, "message": "Plato no encontrado."}, status=400)
                            messages.error(request, "Plato no encontrado.")
                            return redirect("Menu:crear_orden")
            except Exception as e:
                logger.warning(f"Error parseando platos legacy: {e}")

        if menus_raw:
            try:
                menus_dict = json.loads(menus_raw) if isinstance(menus_raw, str) else menus_raw
                if isinstance(menus_dict, dict):
                    for menu_id, info in menus_dict.items():
                        cant = info.get("cantidad", 1) if isinstance(info, dict) else info
                        try:
                            cant = int(cant)
                        except (ValueError, TypeError):
                            cant = 0
                        if cant <= 0:
                            msg = "Cantidad no puede ser menor o igual a cero."
                            if is_json or is_ajax:
                                return JsonResponse({"success": False, "message": msg}, status=400)
                            messages.error(request, msg)
                            return redirect("Menu:crear_orden")
                        try:
                            menu_id_int = int(menu_id)
                            menu = Menu.objects.get(id=menu_id_int)
                            items_to_create.append(("menu", menu, cant))
                        except (Menu.DoesNotExist, ValueError, TypeError):
                            if is_json or is_ajax:
                                return JsonResponse({"success": False, "message": "Menú no encontrado."}, status=400)
                            messages.error(request, "Menú no encontrado.")
                            return redirect("Menu:crear_orden")
            except Exception as e:
                logger.warning(f"Error parseando menus legacy: {e}")

        # 5. Validar que la orden contenga al menos un ítem (rechaza orden vacía con HTTP 400)
        if not items_to_create:
            msg = "La orden debe contener al menos un ítem."
            if is_json or is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("Menu:crear_orden")

        # 6. Creación atómica de la orden
        try:
            with transaction.atomic():
                nueva_orden = Orden(
                    cliente=cliente,
                    canal_venta=canal_venta,
                    tipo_pago=tipo_pago,
                    descuento=descuento,
                    estado=Orden.ESTADO_EN_CURSO
                )
                nueva_orden.save()

                for item_type, obj, cant in items_to_create:
                    if item_type == "plato":
                        OrdenItem.objects.create(orden=nueva_orden, plato=obj, cantidad=cant)
                    elif item_type == "menu":
                        OrdenItem.objects.create(orden=nueva_orden, menu=obj, cantidad=cant)

                nueva_orden.monto_total = nueva_orden.calcular_total()
                nueva_orden.save(update_fields=["monto_total"])

                try:
                    res_print = imprimir_comanda(nueva_orden)
                except Exception as print_err:
                    res_print = {"success": False, "message": str(print_err)}

                if is_json or is_ajax:
                    return JsonResponse({
                        "success": True,
                        "orden_id": nueva_orden.id,
                        "total": nueva_orden.monto_total,
                        "mensaje": "Orden creada con éxito"
                    }, status=201)

                messages.success(request, f"Orden #{nueva_orden.id} creada con éxito.")
                return redirect("Menu:inicio")

        except Exception as e:
            logger.exception("Error durante la creación de la orden: %s", e)
            if is_json or is_ajax:
                return JsonResponse({"success": False, "message": str(e)}, status=400)
            messages.error(request, f"Ocurrió un error al crear la orden: {e}")
            return redirect("Menu:crear_orden")

    # Manejo de solicitudes GET
    platos = Plato.objects.all().order_by('nombre')
    menus = Menu.objects.all().order_by('nombre')
    
    platos_por_letra = {letra: list(grupo) for letra, grupo in groupby(platos, key=lambda x: x.nombre[0].upper())}
    menus_por_letra = {letra: list(grupo) for letra, grupo in groupby(menus, key=lambda x: x.nombre[0].upper())}
    
    canales_ventas = Orden.CANAL_CHOICES
    tipos_pago = Orden.PAGO_CHOICES
    return render(request, "Menu/crear_orden.html", {
        "platos_por_letra": platos_por_letra,
        "menus_por_letra": menus_por_letra,
        "canales_ventas": canales_ventas,
        "tipos_pago": tipos_pago,
    })

# -------------------- GESTIÓN DE RECETAS / ESCANDALLO (DUEÑO) --------------------

@admin_required
def obtener_receta_plato(request, plato_id):
    """Devuelve los insumos asociados al plato (escandallo), su costo unitario y subtotal."""
    plato = get_object_or_404(Plato, id=plato_id)
    items = RecetaItem.objects.filter(plato=plato).select_related('insumo')
    
    costo_total = Decimal("0.0")
    items_data = []
    for item in items:
        costo_item = item.cantidad * (item.insumo.costo_unitario or Decimal("0.0"))
        costo_total += costo_item
        items_data.append({
            "id": item.id,
            "insumo_id": item.insumo.id,
            "insumo_nombre": item.insumo.nombre,
            "insumo_codigo": item.insumo.codigo,
            "unidad": item.insumo.unidad_medida,
            "cantidad": float(item.cantidad),
            "costo_unitario": float(item.insumo.costo_unitario),
            "costo_subtotal": float(costo_item)
        })

    todos_insumos = Insumo.objects.filter(activo=True).order_by('nombre')
    insumos_disponibles = [
        {"id": ins.id, "nombre": ins.nombre, "codigo": ins.codigo, "unidad": ins.unidad_medida, "costo": float(ins.costo_unitario)}
        for ins in todos_insumos
    ]

    margen_bruto = float(plato.valor) - float(costo_total)
    porcentaje_margen = (margen_bruto / float(plato.valor) * 100) if plato.valor > 0 else 0.0

    return JsonResponse({
        "success": True,
        "plato": {
            "id": plato.id,
            "nombre": plato.nombre,
            "valor": float(plato.valor),
            "costo_total": float(costo_total),
            "margen_bruto": round(margen_bruto, 2),
            "porcentaje_margen": round(porcentaje_margen, 1)
        },
        "items": items_data,
        "insumos_disponibles": insumos_disponibles
    })


@admin_required
def guardar_ingrediente_receta(request, plato_id):
    """Crea o actualiza la cantidad de un insumo dentro de la receta de un plato."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Método no permitido. Use POST."}, status=405)

    try:
        data = json.loads(request.body) if request.body and request.content_type == "application/json" else request.POST
        plato = get_object_or_404(Plato, id=plato_id)
        insumo_id = data.get("insumo_id")
        cantidad_raw = data.get("cantidad")

        if not insumo_id or cantidad_raw is None:
            return JsonResponse({"success": False, "message": "Insumo y cantidad son requeridos."}, status=400)

        cantidad = Decimal(str(cantidad_raw))
        if cantidad <= Decimal("0.0"):
            return JsonResponse({"success": False, "message": "La cantidad requerida debe ser estrictamente mayor a 0."}, status=400)

        insumo = get_object_or_404(Insumo, id=insumo_id)

        item, created = RecetaItem.objects.update_or_create(
            plato=plato,
            insumo=insumo,
            defaults={"cantidad": cantidad}
        )

        return JsonResponse({
            "success": True,
            "message": f"Ingrediente '{insumo.nombre}' {'agregado' if created else 'actualizado'} en la receta."
        })
    except (ValueError, InvalidOperation):
        return JsonResponse({"success": False, "message": "Cantidad inválida. Ingrese un número válido."}, status=400)
    except Exception as e:
        logger.exception("Error guardando ingrediente en receta: %s", e)
        return JsonResponse({"success": False, "message": f"Error: {str(e)}"}, status=500)


@admin_required
def eliminar_ingrediente_receta(request, item_id):
    """Elimina un insumo del escandallo / receta de un plato."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Método no permitido. Use POST."}, status=405)

    item = get_object_or_404(RecetaItem, id=item_id)
    insumo_nombre = item.insumo.nombre
    item.delete()
    return JsonResponse({
        "success": True,
        "message": f"Ingrediente '{insumo_nombre}' eliminado de la receta."
    })


# -------------------- EDICION CRUD ----------------------------

@admin_required
def crud(request):
    platos = Plato.objects.all().order_by('id')
    menus = Menu.objects.all().order_by('id')
    insumos = Insumo.objects.filter(activo=True).order_by('nombre')
    return render(request, "Menu/crud.html", {
        "platos": platos, 
        "menus": menus,
        "insumos": insumos
    })

@admin_required
def guardar_plato(request):
    if request.method == 'POST':
        plato_id = request.POST.get('id')
        nombre = request.POST.get('nombre')
        valor = request.POST.get('valor')

        if not nombre or not valor:
            return JsonResponse({'success': False, 'message': 'Nombre y valor son obligatorios.'})

        if plato_id:
            # Editar plato existente
            plato = get_object_or_404(Plato, id=plato_id)
            plato.nombre = nombre
            plato.valor = valor
            plato.save()
            return JsonResponse({'success': True, 'message': 'Plato editado exitosamente.'})
        else:
            # Crear nuevo plato
            Plato.objects.create(nombre=nombre, valor=valor)
            return JsonResponse({'success': True, 'message': 'Plato creado exitosamente.'})

    return JsonResponse({'success': False, 'message': 'Método no permitido.'})

@admin_required
def eliminar_plato(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        plato_id = data.get('id')

        if plato_id:
            plato = get_object_or_404(Plato, id=plato_id)
            plato.delete()
            return JsonResponse({'success': True, 'message': 'Plato eliminado exitosamente.'})

        return JsonResponse({'success': False, 'message': 'ID de plato no proporcionado.'})

    return JsonResponse({'success': False, 'message': 'Método no permitido.'})

@admin_required
def guardar_menu(request):
    if request.method == 'POST':
        menu_id = request.POST.get('id')
        nombre = request.POST.get('nombre')
        precio = request.POST.get('precio_menus')
        platos_ids = request.POST.getlist('platos')

        if not nombre or not precio:
            return JsonResponse({'success': False, 'message': 'Nombre y precio son obligatorios.'})

        try:
            platos_ids = [int(plato_id) for plato_id in platos_ids if plato_id.isdigit()]

            if not platos_ids:
                return JsonResponse({'success': False, 'message': 'Debe seleccionar al menos un plato.'})

            if menu_id:
                menu = get_object_or_404(Menu, id=menu_id)
                menu.nombre = nombre
                menu.precio_menus = precio
                menu.save()

                menu.platos.clear()
                for plato_id in platos_ids:
                    plato = get_object_or_404(Plato, id=plato_id)
                    menu.platos.add(plato)

                menu.full_clean()
                return JsonResponse({'success': True, 'message': 'Menú editado exitosamente.'})
            else:
                nuevo_menu = Menu.objects.create(nombre=nombre, precio_menus=precio)
                for plato_id in platos_ids:
                    plato = get_object_or_404(Plato, id=plato_id)
                    nuevo_menu.platos.add(plato)

                nuevo_menu.full_clean()
                return JsonResponse({'success': True, 'message': 'Menú creado exitosamente.'})

        except ValidationError as e:
            return JsonResponse({'success': False, 'message': str(e)})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error inesperado: {str(e)}'})

    return JsonResponse({'success': False, 'message': 'Método no permitido.'})

@admin_required
def eliminar_menu(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            menu_id = data.get('id')

            if not menu_id:
                return JsonResponse({'success': False, 'message': 'ID de menú no proporcionado.'})

            menu = get_object_or_404(Menu, id=menu_id)
            menu.delete()

            return JsonResponse({'success': True, 'message': 'Menú eliminado exitosamente.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error al eliminar el menú: {str(e)}'})

    return JsonResponse({'success': False, 'message': 'Método no permitido.'})

def detalles_menu(request, id):
    if request.method == 'GET':
        menu = get_object_or_404(Menu, id=id)
        platos = [{"id": plato.id, "nombre": plato.nombre} for plato in menu.platos.all()]
        return JsonResponse({"success": True, "platos": platos})
    return JsonResponse({"success": False, "message": "Método no permitido."})

# -------------------- ANALISIS (DUEÑO / ADMIN) -----------------

@admin_required
def data_analisis(request):
    """
    Panel Ejecutivo y Analítica Avanzada de Restaurante:
    - Restringido a Administradores (RBAC).
    - Métricas clave: Ticket Promedio, Total Facturado, Total Pedidos, Descuentos Totales.
    - Desglose por Horas Punta (Peak Hours), Top Platos por Ventas y Margen.
    - Distribución por Métodos de Pago y Canales de Venta.
    - Tendencias temporales de facturación e ingresos.
    """
    fecha_inicio_param = request.GET.get('fecha_inicio')
    fecha_fin_param = request.GET.get('fecha_fin')

    try:
        if not fecha_inicio_param or not fecha_fin_param:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = datetime.strptime(fecha_inicio_param, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin_param, '%Y-%m-%d').date()

        if fecha_inicio > fecha_fin:
            messages.error(request, "La fecha de inicio no puede ser posterior a la fecha final.")
            fecha_inicio = fecha_fin - timedelta(days=30)

        # Filtro de órdenes en rango (Para ventas reales consideramos órdenes no eliminadas)
        ordenes_base = Orden.objects.filter(fecha__range=[fecha_inicio, fecha_fin])
        ordenes_completadas = ordenes_base.filter(estado=Orden.ESTADO_COMPLETADA)

        # 1. KPIs Principales
        total_ordenes_completadas = ordenes_completadas.count()
        total_ordenes_todas = ordenes_base.count()
        ordenes_canceladas_count = ordenes_base.filter(estado=Orden.ESTADO_ELIMINADA).count()

        facturacion_agg = ordenes_completadas.aggregate(
            total_facturado=Sum("monto_total"),
            total_descuentos=Sum("descuento")
        )
        total_facturado = float(facturacion_agg["total_facturado"] or 0.0)
        total_descuentos = float(facturacion_agg["total_descuentos"] or 0.0)

        # Ticket Promedio
        ticket_promedio = (total_facturado / total_ordenes_completadas) if total_ordenes_completadas > 0 else 0.0

        # 2. Ventas e Ingresos vs Tiempo (Diario)
        ventas_tiempo = (
            ordenes_completadas.annotate(fecha_dia=TruncDay('fecha'))
            .values("fecha_dia")
            .annotate(
                total_ventas=Count("id"),
                total_ingresos=Sum("monto_total")
            )
            .order_by("fecha_dia")
        )

        ventas_tiempo_labels = [dato['fecha_dia'].strftime('%Y-%m-%d') if hasattr(dato['fecha_dia'], 'strftime') else str(dato['fecha_dia']) for dato in ventas_tiempo]
        ventas_tiempo_data = [dato['total_ventas'] for dato in ventas_tiempo]
        ingresos_tiempo_labels = ventas_tiempo_labels
        ingresos_tiempo_data = [float(dato['total_ingresos'] or 0.0) for dato in ventas_tiempo]

        # 3. Top Platos Vendidos
        platos_vendidos_qs = (
            OrdenItem.objects.filter(
                orden__in=ordenes_completadas,
                plato__isnull=False
            )
            .values("plato__nombre", "plato__valor")
            .annotate(
                cantidad=Sum("cantidad")
            )
            .order_by("-cantidad")[:10]
        )
        platos_labels = [p['plato__nombre'] for p in platos_vendidos_qs]
        platos_data = [p['cantidad'] for p in platos_vendidos_qs]
        plato_estrella = platos_labels[0] if platos_labels else "Sin ventas aún"

        # 4. Horas Punta de Venta (Peak Hours: 00:00 a 23:00)
        horas_dict = {h: 0 for h in range(24)}
        for hora_val in ordenes_completadas.values_list("hora", flat=True):
            if hora_val:
                horas_dict[hora_val.hour] += 1
        
        horas_labels = [f"{h:02d}:00" for h in range(24)]
        horas_data = [horas_dict[h] for h in range(24)]
        hora_pico_val = max(horas_dict, key=horas_dict.get) if any(horas_dict.values()) else None
        hora_pico = f"{hora_pico_val:02d}:00 - {hora_pico_val+1:02d}:00" if hora_pico_val is not None and horas_dict[hora_pico_val] > 0 else "N/A"

        # 5. Métodos de Pago
        tipos_pago = (
            ordenes_completadas.values("tipo_pago")
            .annotate(cantidad=Count("id"), total=Sum("monto_total"))
            .order_by("-cantidad")
        )
        tipos_pago_labels = [dato['tipo_pago'] or 'No especificado' for dato in tipos_pago]
        tipos_pago_data = [dato['cantidad'] for dato in tipos_pago]

        # 6. Canales de Venta
        canales_venta = (
            ordenes_completadas.values("canal_venta")
            .annotate(cantidad=Count("id"), total=Sum("monto_total"))
            .order_by("-cantidad")
        )
        canales_venta_labels = [dato['canal_venta'] or 'Local' for dato in canales_venta]
        canales_venta_data = [dato['cantidad'] for dato in canales_venta]

    except Exception as e:
        logger.exception("Error procesando datos para data_analisis: %s", e)
        total_facturado = 0.0
        total_ordenes_completadas = 0
        total_ordenes_todas = 0
        ordenes_canceladas_count = 0
        ticket_promedio = 0.0
        total_descuentos = 0.0
        plato_estrella = "N/A"
        hora_pico = "N/A"
        ventas_tiempo_labels = []
        ventas_tiempo_data = []
        ingresos_tiempo_labels = []
        ingresos_tiempo_data = []
        platos_labels = []
        platos_data = []
        horas_labels = []
        horas_data = []
        tipos_pago_labels = []
        tipos_pago_data = []
        canales_venta_labels = []
        canales_venta_data = []

    context = {
        # KPIs Numéricos
        "total_facturado": total_facturado,
        "total_ordenes_completadas": total_ordenes_completadas,
        "total_ordenes_todas": total_ordenes_todas,
        "ordenes_canceladas_count": ordenes_canceladas_count,
        "ticket_promedio": round(ticket_promedio, 2),
        "total_descuentos": total_descuentos,
        "plato_estrella": plato_estrella,
        "hora_pico": hora_pico,

        # Gráficos JSON
        "ventas_tiempo_labels": json.dumps(ventas_tiempo_labels),
        "ventas_tiempo_data": json.dumps(ventas_tiempo_data),
        "ingresos_tiempo_labels": json.dumps(ingresos_tiempo_labels),
        "ingresos_tiempo_data": json.dumps(ingresos_tiempo_data),
        "platos_labels": json.dumps(platos_labels),
        "platos_data": json.dumps(platos_data),
        "horas_labels": json.dumps(horas_labels),
        "horas_data": json.dumps(horas_data),
        "tipos_pago_labels": json.dumps(tipos_pago_labels),
        "tipos_pago_data": json.dumps(tipos_pago_data),
        "canales_venta_labels": json.dumps(canales_venta_labels),
        "canales_venta_data": json.dumps(canales_venta_data),

        # Rango
        "fecha_inicio": fecha_inicio.strftime('%Y-%m-%d') if fecha_inicio else '',
        "fecha_fin": fecha_fin.strftime('%Y-%m-%d') if fecha_fin else '',
    }

    return render(request, "Menu/data_analisis.html", context)


def ticket_orden(request, id):
    """
    Genera la comanda web térmica (58mm / 80mm) con soporte para auto-impresión window.print().
    Ruta canónica: /pedidos/<id>/ticket/ (con alias /orden/<id>/ticket/).
    """
    orden = get_object_or_404(
        Orden.objects.prefetch_related('items__plato', 'items__menu__platos'),
        id=id
    )

    formato = request.GET.get('format', '80mm')
    if formato not in ('58mm', '80mm'):
        formato = '80mm'

    autoprint = request.GET.get('autoprint', '1') == '1'

    items_detalle = []
    for item in orden.items.all():
        if item.plato:
            items_detalle.append({
                'tipo': 'plato',
                'cantidad': item.cantidad,
                'nombre': item.plato.nombre,
                'precio_unitario': item.plato.valor,
                'subtotal': item.subtotal,
                'detalles': []
            })
        elif item.menu:
            items_detalle.append({
                'tipo': 'menu',
                'cantidad': item.cantidad,
                'nombre': f"[COMBO] {item.menu.nombre}",
                'precio_unitario': item.menu.precio_menus,
                'subtotal': item.subtotal,
                'detalles': [p.nombre for p in item.menu.platos.all()]
            })
        else:
            items_detalle.append({
                'tipo': 'desconocido',
                'cantidad': item.cantidad,
                'nombre': 'Item no especificado',
                'precio_unitario': 0,
                'subtotal': item.subtotal,
                'detalles': []
            })

    context = {
        'orden': orden,
        'items_detalle': items_detalle,
        'formato': formato,
        'autoprint': autoprint,
    }
    return render(request, "Menu/ticket.html", context)


ticket_comanda = ticket_orden


# -------------------- INVENTARIO Y ALERTAS (F18) ----------------------------

@admin_required
def inventario_view(request):
    """
    Panel de Control de Inventario y Alertas (F18):
    - Muestra la tabla completa de materias primas e insumos con indicadores y badges.
    - Soporta búsqueda por código o nombre (?q=...).
    - Soporta filtro por estado (?estado=quiebre|bajo|normal).
    - Soporta visualización de inactivos (?inactivos=1).
    - Métricas KPIs de cabecera.
    """
    if request.method == "POST":
        return ajustar_stock_view(request)

    mostrar_inactivos = request.GET.get("inactivos") == "1"
    if mostrar_inactivos:
        insumos_qs = Insumo.objects.all()
    else:
        insumos_qs = Insumo.objects.filter(activo=True)

    query = request.GET.get("q", "").strip()
    if query:
        insumos_qs = insumos_qs.filter(
            Q(nombre__icontains=query) | Q(codigo__icontains=query)
        )

    todos_activos = Insumo.objects.filter(activo=True)
    total_insumos = todos_activos.count()
    quiebre_count = todos_activos.filter(stock_actual__lte=Decimal("0.000")).count()
    bajo_minimo_count = todos_activos.filter(
        stock_actual__gt=Decimal("0.000"),
        stock_actual__lt=F("stock_minimo")
    ).count()
    normal_count = todos_activos.filter(
        stock_actual__gte=F("stock_minimo")
    ).count()

    valor_total_inventario = Decimal("0.000")
    for ins in todos_activos:
        if ins.stock_actual > Decimal("0.000") and ins.costo_unitario > Decimal("0.000"):
            valor_total_inventario += (ins.stock_actual * ins.costo_unitario)

    filtro_estado = request.GET.get("estado", "").lower()
    if filtro_estado == "quiebre":
        insumos_qs = insumos_qs.filter(stock_actual__lte=Decimal("0.000"))
    elif filtro_estado == "bajo":
        insumos_qs = insumos_qs.filter(
            stock_actual__gt=Decimal("0.000"),
            stock_actual__lt=F("stock_minimo")
        )
    elif filtro_estado == "normal":
        insumos_qs = insumos_qs.filter(stock_actual__gte=F("stock_minimo"))

    insumos = insumos_qs.order_by("nombre")
    todos_insumos_ajuste = Insumo.objects.filter(activo=True).order_by("nombre")

    context = {
        "insumos": insumos,
        "todos_insumos_ajuste": todos_insumos_ajuste,
        "query": query,
        "filtro_estado": filtro_estado,
        "mostrar_inactivos": mostrar_inactivos,
        "total_insumos": total_insumos,
        "quiebre_count": quiebre_count,
        "bajo_minimo_count": bajo_minimo_count,
        "normal_count": normal_count,
        "valor_total_inventario": valor_total_inventario,
        "tipos_movimiento": [
            ("AJUSTE_MANUAL", "Ajuste Manual / Conteo Físico"),
            ("INGRESO_COMPRA", "Ingreso por Compra / Reabastecimiento"),
            ("MERMA", "Merma / Desperdicio"),
        ]
    }
    return render(request, "Menu/inventario.html", context)


@admin_required
def ajustar_stock_view(request):
    """
    Endpoint transaccional para procesar ajustes manuales de stock (F18):
    - Bloqueo pesimista con select_for_update() en transaction.atomic().
    - Soporta conteo físico nuevo (nuevo_stock) o delta (cantidad + tipo).
    - Crea registro inmutable en MovimientoStock(tipo=tipo, orden=None).
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Método no permitido. Use POST."}, status=405)

    data = {}
    if request.content_type == "application/json" and request.body:
        try:
            data = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"success": False, "message": "JSON malformado."}, status=400)
    else:
        data = request.POST.dict()

    insumo_id = data.get("insumo_id")
    if not insumo_id:
        msg = "Parámetro obligatorio 'insumo_id' no especificado."
        messages.error(request, msg)
        return JsonResponse({"success": False, "message": msg}, status=400)

    try:
        with transaction.atomic():
            insumo = Insumo.objects.select_for_update().get(id=insumo_id)
            stock_anterior = Decimal(str(insumo.stock_actual))

            tipo_mov = data.get("tipo", MovimientoStock.TIPO_AJUSTE_MANUAL)
            tipos_validos = [
                MovimientoStock.TIPO_AJUSTE_MANUAL,
                MovimientoStock.TIPO_INGRESO_COMPRA,
                MovimientoStock.TIPO_MERMA,
                MovimientoStock.TIPO_MERMA_DESPERDICIO,
            ]
            if tipo_mov not in tipos_validos:
                tipo_mov = MovimientoStock.TIPO_AJUSTE_MANUAL

            notas = (data.get("notas") or "").strip()

            nuevo_stock_raw = data.get("nuevo_stock")
            cantidad_raw = data.get("cantidad")

            if nuevo_stock_raw is not None and str(nuevo_stock_raw).strip() != "":
                nuevo_stock = Decimal(str(nuevo_stock_raw).strip())
                cantidad_afectada = abs(nuevo_stock - stock_anterior)
            elif cantidad_raw is not None and str(cantidad_raw).strip() != "":
                delta = Decimal(str(cantidad_raw).strip())
                cantidad_afectada = abs(delta)
                if tipo_mov in [MovimientoStock.TIPO_MERMA, MovimientoStock.TIPO_MERMA_DESPERDICIO]:
                    nuevo_stock = stock_anterior - cantidad_afectada
                elif tipo_mov == MovimientoStock.TIPO_INGRESO_COMPRA:
                    nuevo_stock = stock_anterior + cantidad_afectada
                else:
                    nuevo_stock = stock_anterior + delta
            else:
                msg = "Debe indicar el 'nuevo_stock' o la 'cantidad' a ajustar."
                messages.error(request, msg)
                return JsonResponse({"success": False, "message": msg}, status=400)

            insumo.stock_actual = nuevo_stock
            insumo.save(update_fields=["stock_actual"])

            movimiento = MovimientoStock.objects.create(
                insumo=insumo,
                tipo=tipo_mov,
                cantidad=cantidad_afectada,
                stock_anterior=stock_anterior,
                stock_nuevo=nuevo_stock,
                orden=None,
                notas=notas or "Ajuste manual de inventario registrado desde el panel."
            )

            msg_exito = (
                f"Ajuste registrado para '{insumo.nombre}': "
                f"Stock: {stock_anterior} {insumo.unidad_medida} → {nuevo_stock} {insumo.unidad_medida}."
            )
            messages.success(request, msg_exito)

            is_ajax = (
                request.headers.get("x-requested-with") == "XMLHttpRequest"
                or request.content_type == "application/json"
                or bool(request.headers.get("HX-Request"))
            )
            if is_ajax:
                return JsonResponse({
                    "success": True,
                    "message": msg_exito,
                    "insumo_id": insumo.id,
                    "codigo": insumo.codigo,
                    "nombre": insumo.nombre,
                    "stock_anterior": float(stock_anterior),
                    "stock_nuevo": float(nuevo_stock),
                    "cantidad": float(cantidad_afectada),
                    "unidad_medida": insumo.unidad_medida,
                    "tipo": tipo_mov,
                    "movimiento_id": movimiento.id,
                }, status=200)

            return redirect("Menu:inventario")

    except Insumo.DoesNotExist:
        msg = f"Insumo con ID #{insumo_id} no existe."
        messages.error(request, msg)
        return JsonResponse({"success": False, "message": msg}, status=404)
    except (InvalidOperation, ValueError) as val_err:
        msg = f"Valor numérico no válido: {val_err}"
        messages.error(request, msg)
        return JsonResponse({"success": False, "message": msg}, status=400)
    except Exception as e:
        logger.exception("Error crítico en ajuste de stock: %s", e)
        messages.error(request, f"Ocurrió un error al guardar el ajuste: {e}")
        return JsonResponse({"success": False, "message": str(e)}, status=500)


# =============================================================================
# PREVISIÓN IA Y ÓRDENES DE COMPRA (F23 & C05)
# =============================================================================

def sugerencias_compra_api_view(request):
    """
    Endpoint HTTP REST / JSON (F23) para generación de órdenes de compra inteligentes:
    - Rutas expuestas: /api/sugerencias-compra/ y /inventario/sugerencias-ia/.
    - Métodos soportados: GET (consulta) y OPTIONS (preflight/inspección).
    - Parámetros de consulta: ?dias=N (horizonte de proyección, default: 7).
    - Resiliencia y Fallback: ante falla de red LLM o error de validación, invoca
      automáticamente el motor determinista de Reorder Point (ROP).
    - Salida garantizada: formato JSON con 'items_sugeridos' y 'presupuesto_estimado_total'.
    """
    # Soporte explícito de métodos HTTP (TC-B04-25)
    if request.method == "OPTIONS":
        response = JsonResponse({"success": True, "allowed_methods": ["GET", "OPTIONS"]})
        response["Allow"] = "GET, OPTIONS"
        return response

    if request.method != "GET":
        return JsonResponse(
            {"success": False, "message": "Método no permitido. Use GET u OPTIONS."},
            status=405
        )

    # 1. Parsing y sanitización robusta del parámetro 'dias' (TC-B04-21, TC-B04-22)
    dias_raw = request.GET.get("dias", "7")
    try:
        dias = int(dias_raw)
        if dias <= 0:
            dias = 7
    except (ValueError, TypeError):
        dias = 7

    # Control de uso de LLM (permite desactivación vía query param para pruebas/offline)
    usar_llm_param = request.GET.get("usar_llm", "true").lower()
    usar_llm = usar_llm_param not in ["0", "false", "no", "off"]

    # 2. Invocación del servicio de forecasting con fallback determinista transparente
    resultado = None
    try:
        from src.ai_forecast.forecaster import generar_sugerencias_compra
        resultado = generar_sugerencias_compra(dias_proyeccion=dias, usar_llm=usar_llm)
    except Exception as exc:
        logger.warning(
            "Fallo al invocar motor principal de forecasting (%s). Activando fallback ROP.", exc
        )
        try:
            from src.ai_forecast.fallback import calcular_reorden_heuristico
            resultado = calcular_reorden_heuristico(dias_proyeccion=dias)
        except Exception as inner_exc:
            logger.exception("Fallo crítico en motor de fallback: %s", inner_exc)
            resultado = {
                "items_sugeridos": [],
                "presupuesto_estimado_total": 0.0,
                "periodo_dias": dias,
                "metodo": "FALLBACK_EMPTY",
                "alertas": ["Error interno al generar sugerencias. Mostrando estado seguro."]
            }

    # 3. Normalización y serialización segura a diccionario Python
    if hasattr(resultado, "model_dump"):
        data = resultado.model_dump()
    elif hasattr(resultado, "dict"):
        data = resultado.dict()
    elif isinstance(resultado, dict):
        data = dict(resultado)
    else:
        data = {
            "items_sugeridos": [],
            "presupuesto_estimado_total": 0.0,
            "periodo_dias": dias,
            "metodo": "FALLBACK_UNKNOWN"
        }

    # 4. Enriquecimiento bidireccional (Defensa activa contra inconsistencias de nombres de campos)
    items = data.get("items_sugeridos", [])
    for item in items:
        if isinstance(item, dict):
            # Aliases para compatibilidad con template inventario.html
            if "nombre" in item and "insumo_nombre" not in item:
                item["insumo_nombre"] = item["nombre"]
            if "codigo" in item and "insumo_codigo" not in item:
                item["insumo_codigo"] = item["codigo"]
            if "costo_subtotal" in item and "costo_estimado" not in item:
                item["costo_estimado"] = float(item["costo_subtotal"])

            # Conversión de tipos Decimal/Numéricos a float para compatibilidad JSON pura
            for k in [
                "stock_actual", "stock_minimo", "consumo_diario_estimado",
                "cantidad_sugerida", "costo_unitario", "costo_subtotal", "costo_estimado"
            ]:
                if k in item and item[k] is not None:
                    try:
                        item[k] = float(item[k])
                    except (ValueError, TypeError):
                        pass

    if "presupuesto_estimado_total" in data and data["presupuesto_estimado_total"] is not None:
        try:
            data["presupuesto_estimado_total"] = float(data["presupuesto_estimado_total"])
        except (ValueError, TypeError):
            data["presupuesto_estimado_total"] = 0.0

    return JsonResponse(
        data,
        status=200,
        safe=False,
        json_dumps_params={"ensure_ascii": False}
    )



