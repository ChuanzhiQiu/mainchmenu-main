"""
Pruebas de control de turno en creación de comandas y tenancy estricta.

Directiva de corrección:
- Un cajero (sesión operativa) sin turno ABIERTO no puede crear comandas.
- El Administrador del restaurante puede crear comandas sin turno, quedando la
  orden auditada como emitida directamente por él.
- Un administrador vinculado a un restaurante no puede acceder a slugs ajenos.
"""

import json
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from Menu.models import (
    Cajero,
    Orden,
    PerfilAdministrador,
    Plato,
    Restaurante,
    TurnoCaja,
)


class TurnoRequeridoEnCrearOrdenTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(
            nombre="Tenant Turno", slug="tenant-turno"
        )
        self.plato = Plato.objects.create(
            restaurante=self.tenant, nombre="Plato Turno", valor=Decimal("5000.00")
        )
        self.admin = User.objects.create_superuser(
            username="turno_admin", password="password123", email="turno@test.cl"
        )
        PerfilAdministrador.objects.create(user=self.admin, restaurante=self.tenant)

        session = self.client.session
        session["restaurante_id"] = self.tenant.id
        session["active_tenant_slug"] = self.tenant.slug
        session.save()

    def _payload(self):
        return {
            "cliente": "Cliente Turno",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}],
        }

    def test_cajero_sin_turno_no_puede_crear_orden(self):
        resp = self.client.post(
            f"/r/{self.tenant.slug}/pedidos/crear/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Debe abrir un turno de caja para generar comandas", resp.json()["message"])
        self.assertFalse(Orden.all_objects.filter(cliente="Cliente Turno").exists())

    def test_admin_sin_turno_puede_crear_orden_y_se_audita(self):
        self.client.login(username="turno_admin", password="password123")
        resp = self.client.post(
            f"/r/{self.tenant.slug}/pedidos/crear/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        orden = Orden.all_objects.get(id=resp.json()["orden_id"])
        self.assertTrue(orden.emitida_por_admin)
        self.assertEqual(orden.admin_emisor_id, self.admin.id)
        self.assertIsNone(orden.turno_id)
        self.assertIsNone(orden.cajero_id)

    def test_cajero_con_turno_abierto_puede_crear_orden(self):
        cajero = Cajero.objects.create(
            restaurante=self.tenant,
            nombre="Cajero Turno",
            codigo_empleado="CAJ-TURNO",
            pin_hash="hash",
        )
        turno = TurnoCaja.objects.create(
            restaurante=self.tenant,
            cajero=cajero,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )

        session = self.client.session
        session["cajero_id"] = cajero.id
        session["turno_id"] = turno.id
        session.save()

        resp = self.client.post(
            f"/r/{self.tenant.slug}/pedidos/crear/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        orden = Orden.all_objects.get(id=resp.json()["orden_id"])
        self.assertEqual(orden.cajero_id, cajero.id)
        self.assertEqual(orden.turno_id, turno.id)
        self.assertFalse(orden.emitida_por_admin)
        self.assertIsNone(orden.admin_emisor_id)


class TenancyEstrictaOrdenTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant_a = Restaurante.objects.create(
            nombre="Tenant Estricto A", slug="tenant-estricto-a"
        )
        self.tenant_b = Restaurante.objects.create(
            nombre="Tenant Estricto B", slug="tenant-estricto-b"
        )
        self.admin = User.objects.create_superuser(
            username="estricto_admin", password="password123", email="estricto@test.cl"
        )
        PerfilAdministrador.objects.create(user=self.admin, restaurante=self.tenant_a)
        self.client.login(username="estricto_admin", password="password123")

        session = self.client.session
        session["restaurante_id"] = self.tenant_a.id
        session["active_tenant_slug"] = self.tenant_a.slug
        session.save()

        self.plato_b = Plato.objects.create(
            restaurante=self.tenant_b, nombre="Plato B Estricto", valor=Decimal("4000.00")
        )

    def test_admin_a_no_puede_crear_orden_en_slug_b(self):
        resp = self.client.post(
            f"/r/{self.tenant_b.slug}/pedidos/crear/",
            data=json.dumps({
                "cliente": "Orden Cross Slug",
                "items": [{"tipo": "plato", "id": self.plato_b.id, "cantidad": 1}],
            }),
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (403, 404))
        self.assertFalse(
            Orden.all_objects.filter(cliente="Orden Cross Slug").exists()
        )
