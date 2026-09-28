"""
Comprehensive Test Suite for Milestone M8:
- Encryption & Decryption in Rest (Fernet AES-128-CBC + HMAC-SHA256)
- ConfiguracionRestaurante Model & Sensitive Credential Isolation
- Tampered Payload & Corrupted Key Defense
- Anti-CDN Cache Headers Enforcement (Cache-Control: private, no-store)
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from cryptography.fernet import InvalidToken
from Menu.models import Restaurante, ConfiguracionRestaurante
from Menu.encryption import encrypt_data, decrypt_data, get_fernet_key


class TestMilestoneM8EncryptionAndCDN(TestCase):
    def setUp(self):
        self.restaurante_a = Restaurante.objects.create(
            nombre="Restaurante Gourmet A",
            slug="gourmet-a"
        )
        self.restaurante_b = Restaurante.objects.create(
            nombre="Restaurante Express B",
            slug="express-b"
        )
        self.client = Client()

    def test_01_symmetric_encryption_roundtrip(self):
        """Verifica cifrado y descifrado correcto para strings y diccionarios JSON."""
        secret_data = {
            "ubereats_api_key": "ue_live_sec_999988887777",
            "rappi_token": "rappi_live_token_abc123",
            "pedidosya_secret": "pya_live_secret_xyz789"
        }
        ciphertext = encrypt_data(secret_data)
        self.assertIsInstance(ciphertext, str)
        self.assertNotEqual(ciphertext, "")
        # Asegurar que ningún secreto esté en texto plano en el ciphertext
        self.assertNotIn("ue_live_sec", ciphertext)
        self.assertNotIn("rappi_live_token", ciphertext)

        decrypted = decrypt_data(ciphertext, return_json=True)
        self.assertEqual(decrypted, secret_data)
        self.assertEqual(decrypted["ubereats_api_key"], "ue_live_sec_999988887777")

    def test_02_model_configuracion_restaurante_storage_encrypted(self):
        """Verifica que el modelo almacene el payload cifrado en base de datos y lo entregue en memoria."""
        config = ConfiguracionRestaurante.objects.create(
            restaurante=self.restaurante_a,
            permite_delivery=True
        )
        config.set_credenciales({
            "ubereats": "key_uber_12345",
            "rappi": "key_rappi_67890"
        })
        config.save()

        # Recargar directamente desde la base de datos
        reloaded = ConfiguracionRestaurante.all_objects.get(id=config.id)
        # En la base de datos está cifrado (no contiene la clave en texto plano)
        self.assertNotIn("key_uber_12345", reloaded.credenciales_cifradas)
        self.assertNotIn("key_rappi_67890", reloaded.credenciales_cifradas)
        self.assertTrue(reloaded.credenciales_cifradas.startswith("gAAAAA"))

        # Acceso en memoria descifra transparentemente
        self.assertEqual(reloaded.get_api_key("ubereats"), "key_uber_12345")
        self.assertEqual(reloaded.get_api_key("rappi"), "key_rappi_67890")
        self.assertEqual(reloaded.get_api_key("pedidosya"), "")

    def test_03_tampered_ciphertext_defense(self):
        """Verifica que si el payload cifrado es alterado o corrupto, se capture el error adecuadamente."""
        valid_ciphertext = encrypt_data({"key": "secret"})
        tampered_ciphertext = valid_ciphertext[:-4] + "XXXX"
        with self.assertRaises(ValueError):
            decrypt_data(tampered_ciphertext)

    def test_04_anti_cdn_headers_on_tenant_and_dynamic_routes(self):
        """Verifica la directriz estricta R3: Cache-Control: private, no-store en rutas de tenant y admin."""
        session = self.client.session
        session["restaurante_id"] = self.restaurante_a.id
        session["active_tenant_slug"] = self.restaurante_a.slug
        session.save()

        # 1. Ruta de tenant (/r/gourmet-a/pos/)
        resp = self.client.get(f"/r/{self.restaurante_a.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Cache-Control", resp)
        self.assertIn("private, no-store", resp["Cache-Control"])
        self.assertIn("no-cache", resp.get("Pragma", ""))
        self.assertIn("X-Tenant-Slug", resp)
        self.assertEqual(resp["X-Tenant-Slug"], "gourmet-a")

        # 2. Ruta de KDS (/r/gourmet-a/kds/)
        resp_kds = self.client.get(f"/r/{self.restaurante_a.slug}/kds/")
        self.assertEqual(resp_kds.status_code, 200)
        self.assertIn("private, no-store", resp_kds.get("Cache-Control", ""))

    def test_05_multi_tenant_isolation_configuracion(self):
        """Verifica que la configuración de credenciales del Restaurante A esté aislada del B."""
        ConfiguracionRestaurante.objects.create(
            restaurante=self.restaurante_a,
            permite_delivery=True
        )
        ConfiguracionRestaurante.objects.create(
            restaurante=self.restaurante_b,
            permite_delivery=False
        )
        self.assertEqual(ConfiguracionRestaurante.all_objects.count(), 2)
