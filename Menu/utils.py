"""
Menu/utils.py - Adaptador Polimórfico de Impresión Térmica y Utilitarios de Comanda
Desacopla drivers de hardware físicos de entornos Cloud (Vercel, Render, macOS, Linux).
"""

import sys
import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from django.conf import settings

logger = logging.getLogger(__name__)

# Intento seguro de importar win32print solo en Windows
try:
    if sys.platform == 'win32':
        import win32print
    else:
        win32print = None
except ImportError:
    win32print = None


def format_ticket_text(orden, width=32) -> str:
    """
    Formatea una orden como texto plano optimizado para impresoras térmicas de 58mm (32 cols)
    u 80mm (42-48 cols).
    Soporta Platos individuales y Combos/Menús (con desglose de platos componentes).
    """
    lines = []
    lines.append("=" * width)
    lines.append("MAINCH RESTAURANTE".center(width))
    lines.append("Comanda de Cocina & Caja".center(width))
    lines.append("=" * width)
    lines.append(f"ORDEN #{getattr(orden, 'id', '')}".center(width))
    lines.append("-" * width)

    fecha_val = getattr(orden, 'fecha', None)
    hora_val = getattr(orden, 'hora', None)
    fecha_str = fecha_val.strftime("%d/%m/%Y") if hasattr(fecha_val, 'strftime') else "N/A"
    hora_str = hora_val.strftime("%H:%M") if hasattr(hora_val, 'strftime') else "N/A"
    lines.append(f"Fecha: {fecha_str}  Hora: {hora_str}")
    cliente_val = str(getattr(orden, 'cliente', ''))
    lines.append(f"Cliente: {cliente_val[:max(1, width - 9)]}")
    lines.append(f"Canal  : {getattr(orden, 'canal_venta', 'Local')}")
    if getattr(orden, 'tipo_pago', None):
        lines.append(f"Pago   : {orden.tipo_pago}")
    lines.append("-" * width)
    lines.append("DETALLE DE ITEMS".center(width))
    lines.append("-" * width)

    items_rel = getattr(orden, 'items', None)
    has_items = False
    if items_rel is not None and hasattr(items_rel, 'all'):
        try:
            items_qs = items_rel.all()
            if hasattr(items_qs, 'select_related'):
                items = list(items_qs.select_related('plato', 'menu'))
            else:
                items = list(items_qs)
        except Exception:
            items = []

        for item in items:
            has_items = True
            subtotal_val = getattr(item, 'subtotal', 0)
            try:
                subtotal_str = f"${int(subtotal_val):,}".replace(",", ".")
            except (ValueError, TypeError):
                subtotal_str = f"${subtotal_val}"

            plato_obj = getattr(item, 'plato', None)
            menu_obj = getattr(item, 'menu', None)
            cantidad = getattr(item, 'cantidad', 1)

            if plato_obj:
                cant_str = f"{cantidad}x "
                nombre = getattr(plato_obj, 'nombre', None) or "Plato"
                lines.append(f"{cant_str}{nombre}"[:width])
                lines.append(f"{subtotal_str}".rjust(width))
            elif menu_obj:
                cant_str = f"{cantidad}x [COMBO] "
                nombre = getattr(menu_obj, 'nombre', None) or "Combo"
                lines.append(f"{cant_str}{nombre}"[:width])
                if hasattr(menu_obj, 'platos') and hasattr(menu_obj.platos, 'all'):
                    try:
                        platos = list(menu_obj.platos.all())
                    except Exception:
                        platos = []
                    if platos:
                        platos_str = ", ".join([getattr(p, 'nombre', None) or "Plato" for p in platos])
                        lines.append(f"  ↳ Inc: {platos_str}"[:width])
                lines.append(f"{subtotal_str}".rjust(width))
            else:
                lines.append(f"{cantidad}x Item no especificado"[:width])
                lines.append(f"{subtotal_str}".rjust(width))

    if not has_items:
        lines.append("  (Sin items registrados)".center(width))

    lines.append("-" * width)
    descuento_val = getattr(orden, 'descuento', 0)
    if isinstance(descuento_val, (int, float, Decimal)) and descuento_val > 0:
        lines.append(f"Descuento: {descuento_val}%".rjust(width))

    total_val = getattr(orden, 'monto_total', 0)
    if isinstance(total_val, (int, float, Decimal)):
        try:
            total_str = f"${int(total_val):,}".replace(",", ".")
        except (ValueError, TypeError):
            total_str = f"${total_val}"
    else:
        total_str = f"${total_val}"
    lines.append(f"TOTAL: {total_str}".rjust(width))
    lines.append("=" * width)
    lines.append("¡Gracias por su compra!".center(width))
    lines.append("\n\n\n")  # Avance de papel para corte térmico
    return "\n".join(lines)


class BasePrinterAdapter(ABC):
    """Interfaz base para adaptadores de impresión de comandas."""

    @abstractmethod
    def print_ticket(self, orden) -> dict:
        """
        Ejecuta la impresión o generación del ticket.
        Retorna:
            dict con {'success': bool, 'mode': str, 'message': str}
        """
        pass


class NoOpPrinterAdapter(BasePrinterAdapter):
    """
    Adaptador seguro por defecto para entornos Cloud (Vercel, Render) o sin hardware.
    Nunca lanza excepciones y valida que el ticket esté listo para impresión web.
    """

    def print_ticket(self, orden) -> dict:
        if orden is None:
            return {
                "success": False,
                "mode": "error",
                "message": "Orden no especificada o nula."
            }
        texto = format_ticket_text(orden)
        logger.info(f"[PRINTER-MOCK] Orden #{getattr(orden, 'id', 'N/A')} procesada para ticket web:\n{texto}")
        return {
            "success": True,
            "mode": "web_fallback",
            "message": "Impresora física no conectada o desactivada. Ticket web disponible en navegador."
        }


class WindowsSpoolerPrinterAdapter(BasePrinterAdapter):
    """
    Adaptador para impresora térmica conectada a Windows Spooler vía win32print (RAW).
    """

    def __init__(self, printer_name="POS58"):
        self.printer_name = printer_name

    def print_ticket(self, orden) -> dict:
        if orden is None:
            return {
                "success": False,
                "mode": "error",
                "message": "Orden no especificada o nula."
            }
        mod_win32print = globals().get('win32print', None)
        if mod_win32print is None:
            return {
                "success": False,
                "mode": "windows_raw",
                "message": "win32print no está disponible en este sistema operativo."
            }

        try:
            texto = format_ticket_text(orden, width=32)
            printer = mod_win32print.OpenPrinter(self.printer_name)
            try:
                job = mod_win32print.StartDocPrinter(printer, 1, (f"Comanda_{getattr(orden, 'id', '')}", None, "RAW"))
                mod_win32print.StartPagePrinter(printer)
                mod_win32print.WritePrinter(printer, texto.encode("latin-1", errors="replace"))
                mod_win32print.EndPagePrinter(printer)
                mod_win32print.EndDocPrinter(printer)
            finally:
                if hasattr(mod_win32print, 'ClosePrinter'):
                    try:
                        mod_win32print.ClosePrinter(printer)
                    except Exception:
                        pass

            return {
                "success": True,
                "mode": "windows_raw",
                "message": f"Comanda enviada exitosamente a la impresora '{self.printer_name}'."
            }
        except Exception as e:
            logger.warning(f"Fallo al imprimir en Windows Spooler: {e}")
            return {
                "success": False,
                "mode": "windows_raw",
                "message": f"Error de comunicación con impresora '{self.printer_name}': {str(e)}"
            }


class EscposNetworkPrinterAdapter(BasePrinterAdapter):
    """
    Adaptador para impresoras térmicas de red (Ethernet / WiFi / Port 9100).
    """

    def __init__(self, host="192.168.1.200", port=9100, timeout=3):
        self.host = host
        self.port = port
        self.timeout = timeout

    def print_ticket(self, orden) -> dict:
        if orden is None:
            return {
                "success": False,
                "mode": "error",
                "message": "Orden no especificada o nula."
            }
        try:
            from escpos.printer import Network
            printer = Network(self.host, port=self.port, timeout=self.timeout)
            texto = format_ticket_text(orden, width=42)
            printer.text(texto)
            printer.cut()
            printer.close()
            return {
                "success": True,
                "mode": "escpos_network",
                "message": f"Comanda enviada exitosamente a impresora de red {self.host}:{self.port}."
            }
        except Exception as e:
            logger.warning(f"Fallo de impresión ESC/POS de red: {e}")
            return {
                "success": False,
                "mode": "escpos_network",
                "message": f"Error conectando con impresora de red {self.host}: {str(e)}"
            }


def get_printer_adapter() -> BasePrinterAdapter:
    """
    Fábrica que resuelve el adaptador de impresión según settings y entorno de ejecución.
    """
    backend = getattr(settings, 'PRINTER_BACKEND', None)
    if backend == 'noop':
        return NoOpPrinterAdapter()

    if backend == 'network':
        host = getattr(settings, 'PRINTER_HOST', '192.168.1.200')
        port = getattr(settings, 'PRINTER_PORT', 9100)
        return EscposNetworkPrinterAdapter(host=host, port=port)

    # Hardware isolation toggle: honor PRINTER_ENABLED setting
    if not getattr(settings, 'PRINTER_ENABLED', False):
        return NoOpPrinterAdapter()

    mod_win32print = globals().get('win32print', None)
    if mod_win32print is not None:
        printer_name = getattr(settings, 'PRINTER_NAME', 'POS58')
        return WindowsSpoolerPrinterAdapter(printer_name=printer_name)

    return NoOpPrinterAdapter()


def imprimir_comanda(orden) -> dict:
    """
    Fachada pública para impresión de comandas.
    Garantiza retornar un diccionario de estado y NUNCA lanza excepciones no capturadas.
    """
    if orden is None:
        return {
            "success": False,
            "mode": "error",
            "message": "Orden no especificada o nula."
        }
    try:
        adapter = get_printer_adapter()
        return adapter.print_ticket(orden)
    except Exception as e:
        logger.exception(f"Error inesperado en fachada de impresión para orden {getattr(orden, 'id', 'N/A')}: {e}")
        return {
            "success": False,
            "mode": "error",
            "message": f"Error no controlado en servicio de impresión: {str(e)}"
        }
