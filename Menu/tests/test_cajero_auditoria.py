"""
Pruebas para el módulo de Cajeros — Auditoría de Cierres de Caja y Métricas.

Cubre:
1. Métricas consolidadas por cajero (total ventas, pedidos, turnos, ticket promedio)
   calculadas con agregaciones directas en BD y aisladas por tenant.
2. Historial de cierres/arqueos (solo turnos CERRADO / FORZADO del tenant).
3. Filtros por cajero y rango de fechas de cierre.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from Menu.models import Cajero, Orden, Restaurante, TurnoCaja


class CajeroMetricasYAuditoriaTests(TestCase):
    def setUp(self):
        self.client = self.client_class()
        self.tenant = Restaurante.objects.create(
            nombre="Tenant Auditoría A", slug="tenant-auditoria-a"
        )
        self.otro = Restaurante.objects.create(
            nombre="Tenant Auditoría B", slug="tenant-auditoria-b"
        )
        self.admin = User.objects.create_superuser(
            username="auditoria_admin", password="password123", email="aud@test.cl"
        )
        self.client.login(username="auditoria_admin", password="password123")

        self.cajero_a = Cajero.objects.create(
            restaurante=self.tenant,
            nombre="Cajero A",
            codigo_empleado="CAJ-A",
            pin_hash="hash-a",
        )
        self.cajero_b = Cajero.objects.create(
            restaurante=self.otro,
            nombre="Cajero B",
            codigo_empleado="CAJ-B",
            pin_hash="hash-b",
        )

        # Turnos: 2 cerrados + 1 abierto para A; 1 cerrado para B (cross-tenant).
        self.turno_cerrado_viejo = TurnoCaja.objects.create(
            restaurante=self.tenant,
            cajero=self.cajero_a,
            estado=TurnoCaja.ESTADO_CERRADO,
            monto_inicial=Decimal("100.00"),
            monto_final_declarado=Decimal("3150.00"),
            diferencia_arqueo=Decimal("50.00"),
            fecha_cierre=timezone.now() - timedelta(days=2),
        )
        self.turno_cerrado_nuevo = TurnoCaja.objects.create(
            restaurante=self.tenant,
            cajero=self.cajero_a,
            estado=TurnoCaja.ESTADO_CERRADO,
            monto_inicial=Decimal("200.00"),
            monto_final_declarado=Decimal("3200.00"),
            diferencia_arqueo=Decimal("0.00"),
            fecha_cierre=timezone.now() - timedelta(days=1),
        )
        TurnoCaja.objects.create(
            restaurante=self.tenant,
            cajero=self.cajero_a,
            estado=TurnoCaja.ESTADO_ABIERTO,
            monto_inicial=Decimal("50.00"),
        )
        TurnoCaja.objects.create(
            restaurante=self.otro,
            cajero=self.cajero_b,
            estado=TurnoCaja.ESTADO_CERRADO,
            monto_inicial=Decimal("10.00"),
            monto_final_declarado=Decimal("20.00"),
            diferencia_arqueo=Decimal("0.00"),
            fecha_cierre=timezone.now(),
        )

        # Órdenes completadas del cajero A vinculadas al turno cerrado nuevo.
        for monto in ("1000.00", "2000.00"):
            Orden.objects.create(
                restaurante=self.tenant,
                cajero=self.cajero_a,
                turno=self.turno_cerrado_nuevo,
                cliente="Cliente Local",
                estado=Orden.ESTADO_COMPLETADA,
                tipo_pago="Efectivo",
                monto_total=Decimal(monto),
            )
        # Órdenes que NO deben contar (en curso / eliminada).
        Orden.objects.create(
            restaurante=self.tenant,
            cajero=self.cajero_a,
            cliente="En Curso",
            estado=Orden.ESTADO_EN_CURSO,
            tipo_pago="Efectivo",
            monto_total=Decimal("500.00"),
        )
        Orden.objects.create(
            restaurante=self.tenant,
            cajero=self.cajero_a,
            cliente="Eliminada",
            estado=Orden.ESTADO_ELIMINADA,
            tipo_pago="Efectivo",
            monto_total=Decimal("999.00"),
        )
        # Orden de otro tenant (no debe filtrarse).
        Orden.objects.create(
            restaurante=self.otro,
            cajero=self.cajero_b,
            cliente="Cross Tenant",
            estado=Orden.ESTADO_COMPLETADA,
            tipo_pago="Efectivo",
            monto_total=Decimal("5000.00"),
        )

    # ---------------------------------------------------------------- Métricas
    def test_metricas_por_cajero(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        self.assertEqual(resp.status_code, 200)

        cajeros = {c.id: c for c in resp.context["cajeros"]}
        a = cajeros[self.cajero_a.id]
        self.assertEqual(a.total_ventas, Decimal("3000.00"))
        self.assertEqual(a.pedidos_count, 2)
        self.assertEqual(a.turnos_count, 2)
        self.assertEqual(a.ticket_promedio, Decimal("1500.00"))

        # El cajero de otro tenant no debe aparecer.
        self.assertNotIn(self.cajero_b.id, cajeros)

    def test_metricas_no_se_filtran_desde_otro_tenant(self):
        resp = self.client.get(f"/r/{self.otro.slug}/cajeros/")
        cajeros = {c.id: c for c in resp.context["cajeros"]}
        self.assertNotIn(self.cajero_a.id, cajeros)
        b = cajeros[self.cajero_b.id]
        self.assertEqual(b.total_ventas, Decimal("5000.00"))
        self.assertEqual(b.pedidos_count, 1)
        self.assertEqual(b.turnos_count, 1)

    # ------------------------------------------------------- Historial / Arqueos
    def test_historial_solo_turnos_cerrados_del_tenant(self):
        resp = self.client.get(f"/r/{self.tenant.slug}/cajeros/")
        turnos = resp.context["turnos"]

        self.assertEqual(len(turnos), 2)
        # Orden descendente por fecha_cierre.
        self.assertEqual(turnos[0].id, self.turno_cerrado_nuevo.id)
        self.assertEqual(turnos[1].id, self.turno_cerrado_viejo.id)

        turno = turnos[0]
        self.assertEqual(turno.cajero_id, self.cajero_a.id)
        self.assertEqual(turno.ventas_efectivo_sistema, Decimal("3000.00"))
        self.assertEqual(turno.monto_esperado_display, Decimal("3200.00"))
        self.assertEqual(turno.diferencia_display, Decimal("0.00"))

    def test_filtro_historial_por_cajero(self):
        resp = self.client.get(
            f"/r/{self.tenant.slug}/cajeros/", {"cajero_id": self.cajero_a.id}
        )
        turnos = resp.context["turnos"]
        self.assertEqual(len(turnos), 2)

        resp_otro = self.client.get(
            f"/r/{self.tenant.slug}/cajeros/", {"cajero_id": self.cajero_b.id}
        )
        self.assertEqual(len(resp_otro.context["turnos"]), 0)

    def test_filtro_historial_por_rango_fechas(self):
        resp = self.client.get(
            f"/r/{self.tenant.slug}/cajeros/",
            {
                "fecha_desde": (timezone.now() - timedelta(days=1, hours=12)).date().isoformat(),
            },
        )
        turnos = resp.context["turnos"]
        self.assertEqual(len(turnos), 1)
        self.assertEqual(turnos[0].id, self.turno_cerrado_nuevo.id)
