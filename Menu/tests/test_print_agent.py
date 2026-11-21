"""Tests del Puente Local de Impresión (MainchPrintAgent).

Levanta el agente real en un puerto efímero en modo dry-run y verifica contratos
HTTP: CORS/PNA, health, listado de impresoras y envío de bytes ESC/POS.
"""

import base64
import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

AGENT_PATH = Path(settings.BASE_DIR) / "print_agent" / "mainch_print_agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("mainch_print_agent", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = _load_agent_module()


class MainchPrintAgentTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = agent.create_server(host="127.0.0.1", port=0, dry_run=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        super().tearDownClass()

    def _request(self, path, method="GET", body=None):
        """Devuelve (status, headers, payload) tolerando respuestas 4xx."""
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                raw = res.read()
                return res.status, dict(res.headers), raw
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_options_print_returns_204_with_cors_and_pna_headers(self):
        status, headers, raw = self._request("/print", method="OPTIONS")

        self.assertEqual(status, 204)
        self.assertEqual(raw, b"")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(headers.get("Access-Control-Allow-Private-Network"), "true")
        self.assertEqual(headers.get("Access-Control-Allow-Methods"), "GET, POST, OPTIONS")
        self.assertEqual(headers.get("Access-Control-Allow-Headers"), "Content-Type, Authorization")

    def test_health_reports_ok(self):
        status, headers, raw = self._request("/health")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        payload = json.loads(raw)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["version"], agent.VERSION)
        self.assertEqual(payload["platform"], agent.sys.platform)
        self.assertIs(payload["dry_run"], True)
        self.assertIn("default_printer", payload)

    def test_printers_returns_list(self):
        status, _, raw = self._request("/printers")

        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertIsInstance(payload["printers"], list)
        self.assertIn("default_printer", payload)

    def test_print_sends_exact_raw_bytes_in_dry_run(self):
        self.server.last_print_job = None
        raw_bytes = b"\x1b@\x1b\x61\x01COMANDA #77\n\x1dV\x42\x00"
        payload = {
            "printer": "EPSON TM-T20III",
            "raw_base64": base64.b64encode(raw_bytes).decode("ascii"),
        }

        status, _, raw = self._request("/print", method="POST", body=payload)

        self.assertEqual(status, 200)
        respuesta = json.loads(raw)
        self.assertIs(respuesta["success"], True)
        self.assertEqual(respuesta["printer"], "EPSON TM-T20III")
        self.assertEqual(respuesta["bytes_written"], len(raw_bytes))
        self.assertIsNotNone(self.server.last_print_job)
        self.assertEqual(self.server.last_print_job["bytes"], raw_bytes)
        self.assertEqual(self.server.last_print_job["printer"], "EPSON TM-T20III")

    def test_print_rejects_missing_base64(self):
        status, _, raw = self._request("/print", method="POST", body={"printer": "X"})

        self.assertEqual(status, 400)
        self.assertIs(json.loads(raw)["success"], False)

    def test_print_rejects_invalid_base64(self):
        payload = {"raw_base64": "esto no es base64 !!!"}

        status, _, raw = self._request("/print", method="POST", body=payload)

        self.assertEqual(status, 400)
        self.assertIs(json.loads(raw)["success"], False)

    def test_print_rejects_invalid_json(self):
        req = urllib.request.Request(
            f"{self.base_url}/print",
            data=b"{no-es-json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                status = res.status
                body = res.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read()

        self.assertEqual(status, 400)
        self.assertIs(json.loads(body)["success"], False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
