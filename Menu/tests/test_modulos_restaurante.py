"""
Pruebas de los flags de módulos opcionales por restaurante (Fase 2).

Cubre:
1. Aislamiento multi-tenant: apagar un módulo en el Restaurante A no afecta al B.
2. Inventario apagado: vistas HTML/JSON bloqueadas y sin deducción de stock.
3. Salones/Mesas apagado: API bloqueada y órdenes forzadas a mostrador sin mesa.
4. Delivery apagado: webhooks, price tiers y canales de venta bloqueados.
5. Cajeros apagado: gestión/estado/turnos bloqueados y "modo caja simple".
6. Renderizado de UI: navegación y modales ocultos según los flags del tenant.
"""
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from Menu.models import (
    Area,
    Insumo,
    Mesa,
    MovimientoStock,
    Orden,
    OrdenItem,
    Plato,
    RecetaItem,
    Restaurante,
)
from Menu.module_gates import is_module_enabled

MODULOS = ("inventario", "salones_mesas", "delivery", "cajeros")
CAMPOS_MODULO = {
    "inventario": "modulo_inventario",
    "salones_mesas": "modulo_salones_mesas",
    "delivery": "modulo_delivery",
    "cajeros": "modulo_cajeros",
}


class ModulosBaseTestCase(TestCase):
    """Base: dos tenants, un admin staff y un plato listo para órdenes."""

    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(
            nombre="Módulos Tenant A", slug="modulos-tenant-a"
        )
        self.otro = Restaurante.objects.create(
            nombre="Módulos Tenant B", slug="modulos-tenant-b"
        )
        self.admin = User.objects.create_superuser(
            username="modulos_admin", password="password123", email="modulos@test.cl"
        )
        self.client.login(username="modulos_admin", password="password123")
        self.plato = Plato.objects.create(
            restaurante=self.tenant, nombre="Plato Módulos", valor=Decimal("7000.00")
        )
        self.activar_sesion(self.tenant)

    # ------------------------------------------------------------------ helpers
    def activar_sesion(self, tenant):
        """Fija el tenant activo en la sesión (única fuente de tenancy operativa)."""
        session = self.client.session
        session["restaurante_id"] = tenant.id
        session["active_tenant_slug"] = tenant.slug
        for key in ("cajero_id", "turno_id", "cajero_bloqueado"):
            session.pop(key, None)
        session.save()

    def apagar(self, *modulos, tenant=None):
        """Deshabilita los módulos indicados en `tenant` (por defecto el tenant A)."""
        tenant = tenant or self.tenant
        for modulo in modulos:
            setattr(tenant, CAMPOS_MODULO[modulo], False)
        tenant.save(update_fields=[CAMPOS_MODULO[m] for m in modulos])
        return tenant

    def payload_orden(self, **extra):
        data = {
            "cliente": "Cliente Módulos",
            "items": [{"tipo": "plato", "id": self.plato.id, "cantidad": 1}],
        }
        data.update(extra)
        return data

    def crear_orden_http(self, **extra):
        return self.client.post(
            f"/r/{self.tenant.slug}/pedidos/crear/",
            data=json.dumps(self.payload_orden(**extra)),
            content_type="application/json",
        )


class AdminRestauranteSuperuserGateTests(TestCase):
    """El control de módulos por tenant es exclusivo del superusuario desarrollador."""

    URL = "/admin/Menu/restaurante/"

    def test_admin_staff_sin_superuser_recibe_403(self):
        User.objects.create_user(
            username="admin_staff",
            password="password123",
            is_staff=True,
            is_superuser=False,
        )
        self.assertTrue(self.client.login(username="admin_staff", password="password123"))

        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 403)

    def test_superusuario_desarrollador_recibe_200(self):
        User.objects.create_superuser(
            username="dev_superuser",
            password="password123",
            email="dev@test.cl",
        )
        self.assertTrue(self.client.login(username="dev_superuser", password="password123"))

        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200, resp.content)


class AislamientoModulosTests(ModulosBaseTestCase):
    """Los flags son estrictamente por restaurante."""

    def test_apagar_inventario_en_a_no_afecta_a_b(self):
        self.apagar("inventario")

        self.assertFalse(is_module_enabled(self.tenant, "inventario"))
        self.assertTrue(is_module_enabled(self.otro, "inventario"))

        resp_a = self.client.get(f"/r/{self.tenant.slug}/inventario/")
        self.assertEqual(resp_a.status_code, 403)

        # El tenant B conserva el módulo: la misma vista responde 200.
        self.activar_sesion(self.otro)
        resp_b = self.client.get(f"/r/{self.otro.slug}/inventario/")
        self.assertEqual(resp_b.status_code, 200, resp_b.content)

    def test_apagar_todos_los_modulos_en_a_no_afecta_a_b(self):
        self.apagar(*MODULOS)
        for modulo in MODULOS:
            self.assertFalse(is_module_enabled(self.tenant, modulo))
            self.assertTrue(is_module_enabled(self.otro, modulo))


class InventarioApagadoTests(ModulosBaseTestCase):
    def setUp(self):
        super().setUp()
        self.apagar("inventario")

    def test_vista_inventario_devuelve_403_html(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/inventario/")
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "no está habilitado para este restaurante", status_code=403)

    def test_sugerencias_ia_devuelve_403_json(self):
        resp = self.client.get(
            f"/r/{self.tenant.slug}/inventario/sugerencias-ia/",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "inventario")

    def test_confirmar_orden_no_descuenta_stock(self):
        insumo = Insumo.objects.create(
            restaurante=self.tenant,
            codigo="INS-MOD-01",
            nombre="Insumo Módulos",
            stock_actual=Decimal("10.000"),
            stock_minimo=Decimal("1.000"),
        )
        RecetaItem.objects.create(
            restaurante=self.tenant,
            plato=self.plato,
            insumo=insumo,
            cantidad=Decimal("2.000"),
        )
        orden = Orden.objects.create(
            restaurante=self.tenant, cliente="Orden Sin Inventario"
        )
        OrdenItem.objects.create(
            restaurante=self.tenant,
            orden=orden,
            plato=self.plato,
            cantidad=1,
            precio_unitario=self.plato.valor,
        )

        resp = self.client.post(
            f"/r/{self.tenant.slug}/pedidos/{orden.id}/confirmar/",
            data=json.dumps({"tipo_pago": "Efectivo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        insumo.refresh_from_db()
        self.assertEqual(insumo.stock_actual, Decimal("10.000"))
        self.assertFalse(MovimientoStock.objects.filter(restaurante=self.tenant).exists())
        orden.refresh_from_db()
        self.assertFalse(orden.stock_descontado)


class SalonesMesasApagadoTests(ModulosBaseTestCase):
    def setUp(self):
        super().setUp()
        self.apagar("salones_mesas")

    def test_guardar_salon_devuelve_403_json(self):
        resp = self.client.post(
            f"/r/{self.tenant.slug}/api/salon/guardar/",
            data=json.dumps({"nombre": "Salón Bloqueado"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "salones_mesas")
        self.assertFalse(
            Area.all_objects.filter(restaurante=self.tenant, nombre="Salón Bloqueado").exists()
        )

    def test_api_areas_devuelve_403_json(self):
        resp = self.client.get(
            f"/r/{self.tenant.slug}/api/areas/", HTTP_ACCEPT="application/json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_crear_orden_ignora_mesa_y_usa_mostrador(self):
        salon = Area.all_objects.create(restaurante=self.tenant, nombre="Salón A", orden=1)
        mesa = Mesa.all_objects.create(
            restaurante=self.tenant, area=salon, numero="M-100", capacidad=4
        )

        resp = self.crear_orden_http(mesa_id=mesa.id, tipo_servicio="salon")
        self.assertEqual(resp.status_code, 201, resp.content)

        orden = Orden.all_objects.get(id=resp.json()["orden_id"])
        self.assertIsNone(orden.mesa_id)
        self.assertEqual(orden.tipo_servicio, "mostrador")


class DeliveryApagadoTests(ModulosBaseTestCase):
    def setUp(self):
        super().setUp()
        self.apagar("delivery")

    def test_webhook_ubereats_devuelve_403_json(self):
        resp = self.client.post(
            f"/r/{self.tenant.slug}/api/delivery/webhook/ubereats/",
            data=json.dumps({"order_id": "EXT-1"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "delivery")

    def test_price_tiers_devuelve_403_json(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/plato/{self.plato.id}/price-tiers/")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "delivery")

    def test_crear_orden_rechaza_canal_ubereats(self):
        resp = self.crear_orden_http(canal_venta="UberEats")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "delivery")
        self.assertFalse(
            Orden.all_objects.filter(restaurante=self.tenant, canal_venta="UberEats").exists()
        )


class CajerosApagadoTests(ModulosBaseTestCase):
    def setUp(self):
        super().setUp()
        self.apagar("cajeros")

    def test_gestion_cajeros_devuelve_403_html(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "no está habilitado para este restaurante", status_code=403)

    def test_api_estado_cajero_devuelve_403_json(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/api/cajero/estado/")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "cajeros")

    def test_api_turno_abrir_devuelve_403_json(self):
        resp = self.client.post(
            f"/r/{self.tenant.slug}/api/turno/abrir/",
            data=json.dumps({"monto_inicial": 10000}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["modulo"], "cajeros")

    def test_crear_orden_sin_turno_ni_cajero_modo_caja_simple(self):
        # Sin TurnoCaja abierto y sin cajero_id en sesión.
        self.assertFalse(self.client.session.get("cajero_id"))
        self.assertFalse(self.client.session.get("turno_id"))

        resp = self.crear_orden_http()
        self.assertEqual(resp.status_code, 201, resp.content)

        orden = Orden.all_objects.get(id=resp.json()["orden_id"])
        self.assertIsNone(orden.cajero_id)
        self.assertIsNone(orden.turno_id)
        self.assertFalse(orden.emitida_por_admin)


class UIRenderizadoModulosTests(ModulosBaseTestCase):
    def test_base_oculta_navegacion_de_modulos_apagados(self):
        self.apagar("inventario", "cajeros")

        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, f"/r/{self.tenant.slug}/inventario/")
        self.assertNotContains(resp, f"/r/{self.tenant.slug}/cajeros/")

    def test_base_muestra_navegacion_con_modulos_activos(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"/r/{self.tenant.slug}/inventario/")
        self.assertContains(resp, f"/r/{self.tenant.slug}/cajeros/")

    def test_pos_omite_modal_turno_y_selectores_de_salon(self):
        self.apagar("cajeros", "salones_mesas")

        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="modal-apertura-turno"')
        self.assertNotContains(resp, 'id="select-area"')
        self.assertNotContains(resp, 'id="select-mesa"')
        self.assertContains(resp, "const MODULO_SALONES_MESAS = false;")

    def test_pos_muestra_selectores_de_salon_si_modulo_activo(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/pos/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="select-area"')
        self.assertContains(resp, 'id="select-mesa"')
        self.assertContains(resp, "const MODULO_SALONES_MESAS = true;")

    def test_crud_oculta_pestana_mesas_y_acciones_de_modulos(self):
        self.apagar("salones_mesas", "inventario", "delivery")

        resp = self.client.get(f"/r/{self.tenant.slug}/crud/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "?tab=mesas")
        self.assertNotContains(resp, 'id="modal-receta"')
        self.assertNotContains(resp, 'id="modal-price-tiers"')
        self.assertNotContains(resp, 'id="modal-salon"')
        self.assertNotContains(resp, 'id="modal-mesa"')
