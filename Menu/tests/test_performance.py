"""
Regresiones de rendimiento y "GPU-free UI" (Fase 4).

Cubre:
- KDS: firma barata del tablero con respuesta 204 (sin re-render ni prefetch).
- POS: creación de orden con costo de consultas constante O(1).
- Orden.save(update_fields=['estado']) sin recálculo de ítems.
- /api/ordenes/: listado plano, filtro server-side y paginación DRF opt-in.
- api_cajero_estado: aislamiento multi-tenant estricto.
- GZip activo y assets 100% self-hosted (sin CDNs externos).
- Higiene de plantillas activas (sin backdrop-blur ni animate-pulse infinito).
"""

import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.test import APIClient

from Menu.models import Cajero, Orden, OrdenItem, Plato, Restaurante, TurnoCaja


class PerformanceBaseTestCase(TestCase):
    """Helpers compartidos: tenant activo, sesión de restaurante y datos mínimos."""

    def setUp(self):
        self.client = Client()
        self.api = APIClient()
        self.tenant = Restaurante.objects.create(
            nombre="Tenant Perf A",
            slug="tenant-perf-a",
            activo=True,
        )
        self.otro_tenant = Restaurante.objects.create(
            nombre="Tenant Perf B",
            slug="tenant-perf-b",
            activo=True,
        )
        self.admin = User.objects.create_superuser(
            username="admin_perf", password="perf-12345", email="perf@example.com"
        )
        self._activar_sesion(self.tenant)

    def _activar_sesion(self, restaurante):
        self.client.force_login(self.admin)
        session = self.client.session
        session["restaurante_id"] = restaurante.id
        session["active_tenant_slug"] = restaurante.slug
        session.save()

    def _crear_orden(self, cliente="Cliente Perf", estado=Orden.ESTADO_EN_CURSO):
        return Orden.objects.create(
            restaurante=self.tenant,
            cliente=cliente,
            canal_venta=Orden.CANAL_LOCAL,
            estado=estado,
            monto_total=Decimal("1000.00"),
        )


class KdsSignaturePollingTests(PerformanceBaseTestCase):
    """El polling KDS debe costar 204 + firma barata cuando nada cambió."""

    def test_01_inicio_expone_kds_sig_en_render_inicial(self):
        self._crear_orden()
        response = self.client.get(f"/r/{self.tenant.slug}/kds/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('hx-vals=\'{"sig": "', content)
        self.assertIn('kds-board', content)
        self.assertIn("document.visibilityState === 'visible'", content)

    def test_02_poll_htmx_con_sig_vigente_devuelve_204_y_consultas_minimas(self):
        self._crear_orden()
        inicial = self.client.get(f"/r/{self.tenant.slug}/kds/")
        self.assertEqual(inicial.status_code, 200)

        # La firma vigente viaja en el atributo hx-vals del tablero.
        contenido = inicial.content.decode("utf-8")
        marca = 'hx-vals=\'{"sig": "'
        inicio = contenido.index(marca) + len(marca)
        sig = contenido[inicio:contenido.index('"', inicio)]
        self.assertTrue(sig)

        with CaptureQueriesContext(connection) as ctx:
            poll = self.client.get(
                f"/r/{self.tenant.slug}/kds/?sig={sig}",
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(poll.status_code, 204)
        self.assertEqual(poll.content, b"")
        # Coste verificado: exactamente 3 consultas (sesión + tenant + firma del tablero).
        self.assertLessEqual(len(ctx.captured_queries), 3)

    def test_03_poll_htmx_con_sig_obsoleta_devuelve_200_con_tablero(self):
        orden = self._crear_orden(cliente="Cliente KDS")
        poll = self.client.get(
            f"/r/{self.tenant.slug}/kds/?sig=stale",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(poll.status_code, 200)
        contenido = poll.content.decode("utf-8")
        self.assertIn("kds-board", contenido)
        self.assertIn("Cliente KDS", contenido)
        self.assertEqual(orden.estado, Orden.ESTADO_EN_CURSO)


class OrdenCreationQueryCostTests(PerformanceBaseTestCase):
    """La creación de una orden no debe escalar en consultas con la cantidad de ítems."""

    def _crear_platos(self, cantidad):
        # SKU único por tenant: el nombre lleva el conteo para evitar colisiones entre llamadas.
        return [
            Plato.objects.create(
                restaurante=self.tenant,
                nombre=f"Plato Perf {cantidad}-{i}",
                valor=Decimal("1500.00"),
            )
            for i in range(cantidad)
        ]

    def _post_orden(self, platos):
        # El seeding de catálogo se hace fuera del contexto medido: aquí solo se mide el POST.
        payload = {
            "cliente": "Cliente O(1)",
            "canal_venta": "Local",
            "items": [
                {"tipo": "plato", "id": plato.id, "cantidad": 1}
                for plato in platos
            ],
        }
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.post(
                f"/r/{self.tenant.slug}/pedidos/crear/",
                data=json.dumps(payload),
                content_type="application/json",
            )
        return resp, len(ctx.captured_queries)

    def test_04_crear_orden_costo_de_consultas_constante(self):
        platos_2 = self._crear_platos(2)
        platos_5 = self._crear_platos(5)

        resp_2, queries_2 = self._post_orden(platos_2)
        self.assertIn(resp_2.status_code, (200, 201), resp_2.content)
        self.assertTrue(resp_2.json().get("success"))

        resp_5, queries_5 = self._post_orden(platos_5)
        self.assertIn(resp_5.status_code, (200, 201), resp_5.content)
        self.assertTrue(resp_5.json().get("success"))

        self.assertEqual(
            queries_2,
            queries_5,
            "La creación de orden debe ser O(1) en consultas (2 vs 5 ítems)",
        )


class OrdenSaveFastPathTests(PerformanceBaseTestCase):
    """Actualizar solo el estado no debe recalcular el total ni leer los ítems."""

    def test_05_save_update_fields_estado_una_sola_query(self):
        orden = self._crear_orden()
        plato = Plato.objects.create(
            restaurante=self.tenant, nombre="Plato FastPath", valor=Decimal("2000.00")
        )
        OrdenItem.objects.create(
            restaurante=self.tenant,
            orden=orden,
            plato=plato,
            cantidad=2,
            precio_unitario=Decimal("2000.00"),
        )

        with CaptureQueriesContext(connection) as ctx:
            orden.estado = Orden.ESTADO_COMPLETADA
            orden.save(update_fields=["estado"])

        self.assertEqual(len(ctx.captured_queries), 1)

    def test_06_calcular_total_retorna_decimal(self):
        orden = self._crear_orden()
        plato = Plato.objects.create(
            restaurante=self.tenant, nombre="Plato Decimal", valor=Decimal("1990.00")
        )
        OrdenItem.objects.create(
            restaurante=self.tenant,
            orden=orden,
            plato=plato,
            cantidad=1,
            precio_unitario=Decimal("1990.00"),
        )
        total = orden.calcular_total()
        self.assertIsInstance(total, Decimal)
        self.assertEqual(total, Decimal("1990.00"))


class OrdenesApiContractTests(PerformanceBaseTestCase):
    """Contrato del endpoint /api/ordenes/ usado por el POS."""

    def setUp(self):
        super().setUp()
        self.api.force_authenticate(user=self.admin)
        self.orden_a = self._crear_orden(cliente="Orden A")
        self.orden_b = Orden.objects.create(
            restaurante=self.tenant,
            cliente="Orden B",
            canal_venta=Orden.CANAL_LOCAL,
            estado=Orden.ESTADO_COMPLETADA,
            monto_total=Decimal("500.00"),
        )

    def _url(self, path=""):
        return f"/r/{self.tenant.slug}/api/ordenes/{path}"

    def test_07_listado_plano_por_defecto_y_tope_limit(self):
        resp = self.api.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

        resp_limit = self.api.get(self._url("?limit=1"))
        self.assertEqual(resp_limit.status_code, 200)
        self.assertEqual(len(resp_limit.json()), 1)

    def test_08_filtro_estado_server_side(self):
        resp = self.api.get(self._url("?estado=En%20curso&limit=100"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual({o["estado"] for o in data}, {Orden.ESTADO_EN_CURSO})
        self.assertIn(self.orden_a.id, [o["id"] for o in data])
        self.assertNotIn(self.orden_b.id, [o["id"] for o in data])

    def test_09_paginacion_drf_opt_in(self):
        resp = self.api.get(self._url("?page=1&page_size=2"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(set(data.keys()), {"count", "next", "previous", "results"})
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["results"]), 2)


class CajeroEstadoTenantIsolationTests(PerformanceBaseTestCase):
    """api_cajero_estado nunca debe resolver cajero/turno de otro restaurante."""

    def test_10_estado_cajero_no_filtra_datos_de_otro_tenant(self):
        ajeno = Cajero.objects.create(
            restaurante=self.otro_tenant,
            nombre="Cajero Ajeno",
            codigo_empleado="AJENO-1",
            pin_hash="hash-ajeno",
        )
        turno_ajeno = TurnoCaja.objects.create(
            restaurante=self.otro_tenant,
            cajero=ajeno,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )

        session = self.client.session
        session["restaurante_id"] = self.tenant.id
        session["active_tenant_slug"] = self.tenant.slug
        session["cajero_id"] = ajeno.id
        session["turno_id"] = turno_ajeno.id
        session.save()

        resp = self.client.get(f"/r/{self.tenant.slug}/api/cajero/estado/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIsNone(data["cajero"])
        self.assertIsNone(data["turno"])


class DeliveryAndAssetHygieneTests(PerformanceBaseTestCase):
    """GZip en respuestas grandes + assets self-hosted en plantillas clave."""

    CDN_MARKS = ("cdn.tailwindcss.com", "unpkg.com", "jsdelivr")

    def test_11_respuesta_grande_usa_gzip(self):
        # Fuerza un cuerpo > 200 bytes (umbral de GZipMiddleware).
        for i in range(5):
            self._crear_orden(cliente=f"Cliente GZip {i}")

        resp = self.client.get(
            f"/r/{self.tenant.slug}/kds/",
            HTTP_ACCEPT_ENCODING="gzip",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Content-Encoding"), "gzip")

    def test_12_plantillas_sin_cdn_y_con_assets_locales(self):
        rutas = [
            "/login/",
            f"/r/{self.tenant.slug}/kds/",
            f"/r/{self.tenant.slug}/pos/",
            f"/r/{self.tenant.slug}/analisis/",
        ]
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                # /login/ redirige si ya hay sesión: se consulta con un cliente limpio.
                cliente = Client() if ruta == "/login/" else self.client
                resp = cliente.get(ruta)
                self.assertEqual(resp.status_code, 200)
                html = resp.content.decode("utf-8")
                for cdn in self.CDN_MARKS:
                    self.assertNotIn(cdn, html, f"CDN externo en {ruta}: {cdn}")
                self.assertIn("app.v1.min.css", html)
                self.assertIn("defer", html)

    def test_13_higiene_de_plantillas_activas(self):
        from pathlib import Path

        base = Path(__file__).resolve().parents[1] / "templates" / "Menu"
        for plantilla in base.rglob("*.html"):
            if plantilla.name == "crear_orden.html":
                continue
            texto = plantilla.read_text(encoding="utf-8")
            self.assertNotIn(
                "backdrop-blur", texto, f"backdrop-blur remanente en {plantilla.name}"
            )

        inicio = (base / "inicio.html").read_text(encoding="utf-8")
        self.assertNotIn("animate-pulse", inicio)
