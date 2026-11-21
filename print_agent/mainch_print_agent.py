#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MainchPrintAgent - Puente local de impresion ESC/POS para Windows.

Expone un micro-servidor HTTP en 127.0.0.1 (puerto 9100 por defecto) que recibe
bytes ESC/POS ya codificados en base64 desde el POS web (MainchDirectPrint) y los
envia en modo RAW al spooler de Windows mediante pywin32.

No depende de frameworks externos: solo la libreria estandar de Python.
"""

import base64
import binascii
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9100
MAX_BODY_BYTES = 1024 * 1024  # 1 MB es mas que suficiente para una comanda.

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    # Chrome Private Network Access: permite llamadas desde HTTPS publico a 127.0.0.1.
    "Access-Control-Allow-Private-Network": "true",
}


def _load_win32print():
    """Importa pywin32 de forma diferida (no existe en macOS/Linux ni en dry-run)."""
    try:
        import win32print  # noqa: F401  (import perezoso, solo en Windows)
        return win32print
    except Exception:
        return None


def is_dry_run():
    """True si se pidio simulacion explicita o si no hay backend de impresion."""
    if os.environ.get("MAINCH_AGENT_DRY_RUN") == "1":
        return True
    return _load_win32print() is None


def list_printers():
    """Lista las impresoras instaladas en el spooler local."""
    win32print = _load_win32print()
    if win32print is None:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    try:
        # Nivel 2 devuelve diccionarios con la clave 'printerName'.
        return [p["printerName"] for p in win32print.EnumPrinters(flags, None, 2)]
    except Exception as exc:  # pragma: no cover - depende del spooler real
        print(f"[MainchPrintAgent] No se pudieron enumerar impresoras: {exc}", file=sys.stderr)
        return []


def get_default_printer():
    """Nombre de la impresora predeterminada de Windows ('' si no hay)."""
    win32print = _load_win32print()
    if win32print is None:
        return ""
    try:
        return win32print.GetDefaultPrinter() or ""
    except Exception:  # pragma: no cover - sin impresora predeterminada
        return ""


def send_raw_to_printer(printer_name, raw_bytes):
    """Envia bytes RAW al spooler de Windows. Devuelve la cantidad escrita."""
    win32print = _load_win32print()
    if win32print is None:
        raise RuntimeError("pywin32 no esta instalado: imposible imprimir fuera de Windows.")

    handle = win32print.OpenPrinter(printer_name)
    try:
        job_id = win32print.StartDocPrinter(handle, 1, ("Mainch Comanda", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            try:
                written = win32print.WritePrinter(handle, raw_bytes)
            finally:
                win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)

    return written if isinstance(written, int) and written >= 0 else len(raw_bytes)


class MainchPrintRequestHandler(BaseHTTPRequestHandler):
    """Router HTTP minimo: /health, /printers y /print."""

    server_version = f"MainchPrintAgent/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ---------- utilidades de respuesta ----------

    def _send_headers(self, status, content_length, content_type=None):
        self.send_response(status)
        for header, value in CORS_HEADERS.items():
            self.send_header(header, value)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_headers(status, len(body), "application/json; charset=utf-8")
        self.wfile.write(body)

    def _send_no_content(self):
        self._send_headers(204, 0)

    def _read_json_body(self):
        """Devuelve (payload, error). error es un string si el cuerpo es invalido."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "Content-Length invalido."

        if length <= 0:
            return None, "El cuerpo de la peticion esta vacio."
        if length > MAX_BODY_BYTES:
            return None, "El cuerpo de la peticion es demasiado grande."

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None, "JSON invalido."

        if not isinstance(payload, dict):
            return None, "El cuerpo JSON debe ser un objeto."
        return payload, None

    # ---------- rutas ----------

    def do_OPTIONS(self):
        # Preflight CORS + Private Network Access.
        self._send_no_content()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == "/health":
            self._send_json(200, {
                "status": "ok",
                "version": VERSION,
                "platform": sys.platform,
                "default_printer": get_default_printer(),
                "dry_run": bool(getattr(self.server, "dry_run", False)),
            })
            return

        if path == "/printers":
            self._send_json(200, {
                "printers": list_printers(),
                "default_printer": get_default_printer(),
            })
            return

        self._send_json(404, {"success": False, "error": "Ruta no encontrada."})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path != "/print":
            self._send_json(404, {"success": False, "error": "Ruta no encontrada."})
            return

        payload, error = self._read_json_body()
        if error:
            self._send_json(400, {"success": False, "error": error})
            return

        encoded = payload.get("raw_base64")
        if not isinstance(encoded, str) or not encoded.strip():
            self._send_json(400, {"success": False, "error": "Falta el campo raw_base64."})
            return

        try:
            raw_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            self._send_json(400, {"success": False, "error": "raw_base64 no es base64 valido."})
            return

        if not raw_bytes:
            self._send_json(400, {"success": False, "error": "raw_base64 decodifica a cero bytes."})
            return

        target_printer = (payload.get("printer") or "").strip() or get_default_printer()

        if getattr(self.server, "dry_run", False):
            # Modo simulacion: registra el trabajo para inspeccion/tests sin spooler.
            self.server.last_print_job = {"printer": target_printer, "bytes": raw_bytes}
            bytes_written = len(raw_bytes)
        else:
            if not target_printer:
                self._send_json(400, {"success": False, "error": "No hay impresora configurada ni predeterminada."})
                return
            try:
                bytes_written = send_raw_to_printer(target_printer, raw_bytes)
            except Exception as exc:
                self._send_json(500, {"success": False, "error": f"Fallo al enviar al spooler: {exc}"})
                return

        self._send_json(200, {
            "success": True,
            "printer": target_printer,
            "bytes_written": bytes_written,
        })

    # ---------- logging ----------

    def log_message(self, fmt, *args):
        if os.environ.get("MAINCH_AGENT_VERBOSE") == "1":
            sys.stderr.write(f"[MainchPrintAgent] {self.address_string()} - {fmt % args}\n")


def create_server(host=DEFAULT_HOST, port=DEFAULT_PORT, dry_run=False):
    """Fabrica un servidor listo para `serve_forever()` (port=0 -> puerto efimero)."""
    server = ThreadingHTTPServer((host, port), MainchPrintRequestHandler)
    server.daemon_threads = True
    server.dry_run = bool(dry_run) or is_dry_run()
    server.last_print_job = None
    return server


def main():
    try:
        port = int(os.environ.get("MAINCH_PRINT_PORT", DEFAULT_PORT))
    except ValueError:
        print(f"[MainchPrintAgent] MAINCH_PRINT_PORT invalido, usando {DEFAULT_PORT}.", file=sys.stderr)
        port = DEFAULT_PORT

    host = os.environ.get("MAINCH_PRINT_HOST", DEFAULT_HOST)
    server = create_server(host=host, port=port)

    mode = "DRY-RUN (sin spooler)" if server.dry_run else "Windows Spooler RAW"
    print(f"[MainchPrintAgent] v{VERSION} escuchando en http://{host}:{port} - modo: {mode}", flush=True)
    print(f"[MainchPrintAgent] Impresora predeterminada: {get_default_printer() or '(ninguna)'}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MainchPrintAgent] Cerrando...", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
