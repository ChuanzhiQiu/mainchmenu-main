"""
Pruebas de Observabilidad de IA para Previsión de Inventario (Fase 4).

Cubre:
1. Detección dinámica de configuración de LLM (sin/con API key y modelo).
2. Inyección de banderas (ia_activa, ia_modelo, ia_motor_label) en:
   - el endpoint JSON de sugerencias,
   - el contexto del template de inventario.
"""
import os
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, Client
from django.contrib.auth.models import User

from Menu.models import Insumo, Restaurante
from src.ai_forecast.config import detectar_configuracion_ia


_SIN_API = {
    "GEMINI_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "OPENAI_API_KEY": "",
    "LLM_API_KEY": "",
    "IA_MODELO": "",
    "LLM_MODEL": "",
}


class DetectarConfiguracionIATests(TestCase):
    """Unit tests para el detector de configuración de IA."""

    def test_sin_api_key(self):
        with patch.dict(os.environ, _SIN_API, clear=True):
            cfg = detectar_configuracion_ia()
        self.assertFalse(cfg["ia_activa"])
        self.assertIsNone(cfg["ia_modelo"])
        self.assertIn("Heurística ROP Local", cfg["ia_motor_label"])

    def test_gemini_default(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key-123"}, clear=True):
            cfg = detectar_configuracion_ia()
        self.assertTrue(cfg["ia_activa"])
        self.assertEqual(cfg["ia_modelo"], "gemini-1.5-flash")
        self.assertIn("Asistido por LLM", cfg["ia_motor_label"])

    def test_deepseek_default(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True):
            cfg = detectar_configuracion_ia()
        self.assertEqual(cfg["ia_modelo"], "deepseek-chat")

    def test_openai_default(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai"}, clear=True):
            cfg = detectar_configuracion_ia()
        self.assertEqual(cfg["ia_modelo"], "gpt-4o-mini")

    def test_modelo_explicito_prevalece(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-openai", "IA_MODELO": "gpt-4o"},
            clear=True,
        ):
            cfg = detectar_configuracion_ia()
        self.assertTrue(cfg["ia_activa"])
        self.assertEqual(cfg["ia_modelo"], "gpt-4o")

    def test_llm_model_env_prevalece(self):
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-ds", "LLM_MODEL": "deepseek-reasoner"},
            clear=True,
        ):
            cfg = detectar_configuracion_ia()
        self.assertEqual(cfg["ia_modelo"], "deepseek-reasoner")


class ObservabilidadIntegracionTests(TestCase):
    """Verifica la inyección de banderas en endpoint JSON y template."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="obs_admin", password="password123", email="obs@test.cl"
        )
        self.tenant = Restaurante.objects.create(
            nombre="Tenant Obs", slug="tenant-obs"
        )
        Insumo.objects.create(
            restaurante=self.tenant,
            codigo="INS-OBS",
            nombre="Insumo Observabilidad",
            unidad_medida="kg",
            stock_actual=Decimal("5.000"),
            stock_minimo=Decimal("10.000"),
            costo_unitario=Decimal("1000.000"),
            activo=True,
        )

    def test_endpoint_json_sin_api(self):
        with patch.dict(os.environ, _SIN_API):
            resp = self.client.get("/api/sugerencias-compra/?usar_llm=false")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["ia_activa"])
        self.assertIsNone(data["ia_modelo"])
        self.assertIn("Heurística ROP Local", data["ia_motor_label"])

    def test_endpoint_json_con_api(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds", "IA_MODELO": "deepseek-chat"}):
            resp = self.client.get("/api/sugerencias-compra/?usar_llm=false")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ia_activa"])
        self.assertEqual(data["ia_modelo"], "deepseek-chat")
        self.assertIn("Asistido por LLM", data["ia_motor_label"])

    def test_inventario_template_context_sin_api(self):
        self.client.login(username="obs_admin", password="password123")
        with patch.dict(os.environ, _SIN_API):
            resp = self.client.get("/inventario/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["ia_activa"])
        self.assertIsNone(resp.context["ia_modelo"])
        self.assertContains(resp, "Sin API de IA conectada")
        self.assertContains(resp, "Heurística ROP Local")

    def test_inventario_template_context_con_api(self):
        self.client.login(username="obs_admin", password="password123")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai"}):
            resp = self.client.get("/inventario/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["ia_activa"])
        self.assertEqual(resp.context["ia_modelo"], "gpt-4o-mini")
        self.assertContains(resp, "Modelo: gpt-4o-mini")
        self.assertNotContains(resp, "Sin API de IA conectada")
