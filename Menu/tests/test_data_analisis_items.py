"""
Pruebas de Data Análisis — KPI de Ítems Promedio por Compra y
Desglose Navegable de Ventas por Ítem (Fase 5).

Cubre:
1. Cálculo de items_promedio (incluido el caso sin pedidos).
2. Consolidado de ventas por plato con recaudación y participación.
3. Aislamiento multi-tenant (no filtra datos de otros restaurantes).
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone

from Menu.models import (
    Categoria,
    Orden,
    OrdenItem,
    Plato,
    Restaurante,
)


class DataAnalisisItemsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tenant = Restaurante.objects.create(
            nombre="Tenant Data A", slug="tenant-data-a"
        )
        self.otro = Restaurante.objects.create(
            nombre="Tenant Data B", slug="tenant-data-b"
        )
        self.admin = User.objects.create_superuser(
            username="data_admin", password="password123", email="data@test.cl"
        )
        self.client.login(username="data_admin", password="password123")

        self.categoria_entradas = Categoria.objects.create(
            restaurante=self.tenant, nombre="Entradas"
        )
        self.categoria_bebidas = Categoria.objects.create(
            restaurante=self.tenant, nombre="Bebidas"
        )

        self.hamburguesa = Plato.objects.create(
            restaurante=self.tenant, nombre="Hamburguesa", valor=Decimal("5000.00"),
            categoria=self.categoria_entradas,
        )
        self.bebida = Plato.objects.create(
            restaurante=self.tenant, nombre="Bebida", valor=Decimal("2000.00"),
            categoria=self.categoria_bebidas,
        )

        self.plato_otro = Plato.objects.create(
            restaurante=self.otro, nombre="Plato Otro Tenant", valor=Decimal("9999.00"),
        )

    def _crear_orden(self, restaurante, estado, items):
        orden = Orden.objects.create(
            restaurante=restaurante,
            cliente="Cliente Test",
            estado=estado,
            fecha=timezone.now().date(),
        )
        for plato, cantidad, precio in items:
            OrdenItem.objects.create(
                restaurante=restaurante,
                orden=orden,
                plato=plato,
                cantidad=cantidad,
                precio_unitario=Decimal(str(precio)),
            )
        return orden

    def _url(self, restaurante):
        return f"/r/{restaurante.slug}/analisis/"

    def test_items_promedio_con_pedido_de_5_items(self):
        # 1 pedido: 3 hamburguesas + 2 bebidas = 5 unidades.
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [
                (self.hamburguesa, 3, "5000.00"),
                (self.bebida, 2, "2000.00"),
            ],
        )
        resp = self.client.get(self._url(self.tenant))
        self.assertEqual(resp.status_code, 200)

        ctx = resp.context
        self.assertEqual(ctx["total_unidades_vendidas"], 5)
        self.assertEqual(ctx["total_ordenes_completadas"], 1)
        self.assertEqual(ctx["items_promedio"], 5.0)

    def test_items_promedio_con_multiples_pedidos(self):
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [(self.hamburguesa, 3, "5000.00"), (self.bebida, 2, "2000.00")],
        )
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [(self.bebida, 2, "2000.00")],
        )
        resp = self.client.get(self._url(self.tenant))
        ctx = resp.context
        self.assertEqual(ctx["total_unidades_vendidas"], 7)
        self.assertEqual(ctx["total_ordenes_completadas"], 2)
        self.assertEqual(ctx["items_promedio"], 3.5)

    def test_items_promedio_sin_pedidos(self):
        resp = self.client.get(self._url(self.tenant))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertEqual(ctx["total_unidades_vendidas"], 0)
        self.assertEqual(ctx["items_promedio"], 0.0)
        self.assertEqual(ctx["ventas_por_item"], [])

    def test_desglose_ventas_por_item_consolidado(self):
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [
                (self.hamburguesa, 3, "5000.00"),
                (self.bebida, 2, "2000.00"),
            ],
        )
        resp = self.client.get(self._url(self.tenant))
        ventas = {v["nombre"]: v for v in resp.context["ventas_por_item"]}

        self.assertEqual(len(ventas), 2)
        hamburguesa = ventas["Hamburguesa"]
        self.assertEqual(hamburguesa["unidades_vendidas"], 3)
        self.assertEqual(hamburguesa["recaudacion_total"], 15000.0)
        self.assertEqual(hamburguesa["categoria"], "Entradas")

        bebida = ventas["Bebida"]
        self.assertEqual(bebida["unidades_vendidas"], 2)
        self.assertEqual(bebida["recaudacion_total"], 4000.0)

        # Participación: 15000 / 19000 * 100 = 78.95% (redondeado a 2 decimales)
        self.assertEqual(hamburguesa["participacion_ventas"], 78.95)
        self.assertEqual(bebida["participacion_ventas"], 21.05)

    def test_desglose_ordenado_por_recaudacion_desc(self):
        # Bebida recauda más que hamburguesa en este escenario para verificar orden.
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [(self.hamburguesa, 1, "5000.00"), (self.bebida, 5, "2000.00")],
        )
        resp = self.client.get(self._url(self.tenant))
        nombres = [v["nombre"] for v in resp.context["ventas_por_item"]]
        self.assertEqual(nombres, ["Bebida", "Hamburguesa"])

    def test_desglose_multi_tenant_no_filtra_datos_ajenos(self):
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [(self.hamburguesa, 1, "5000.00")],
        )
        self._crear_orden(
            self.otro,
            Orden.ESTADO_COMPLETADA,
            [(self.plato_otro, 10, "9999.00")],
        )

        resp = self.client.get(self._url(self.tenant))
        nombres = [v["nombre"] for v in resp.context["ventas_por_item"]]
        self.assertIn("Hamburguesa", nombres)
        self.assertNotIn("Plato Otro Tenant", nombres)
        self.assertEqual(resp.context["total_unidades_vendidas"], 1)

    def test_ordenes_canceladas_no_se_consolidan(self):
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_COMPLETADA,
            [(self.hamburguesa, 3, "5000.00")],
        )
        self._crear_orden(
            self.tenant,
            Orden.ESTADO_ELIMINADA,
            [(self.bebida, 2, "2000.00")],
        )
        resp = self.client.get(self._url(self.tenant))
        ctx = resp.context
        self.assertEqual(ctx["total_unidades_vendidas"], 3)
        nombres = [v["nombre"] for v in ctx["ventas_por_item"]]
        self.assertIn("Hamburguesa", nombres)
        self.assertNotIn("Bebida", nombres)
