"""
Pruebas unitarias del servicio de procesamiento de pagos multi-tenant.

Cubre:
- Registro exitoso de pago completo.
- Pago dividido en dos parciales que saldan la orden.
- Rechazo por turno de caja cerrado o de otro tenant.
- Liberación automática de mesa al completar el saldo.
- Casos defensivos: orden cancelada y orden ya totalmente pagada.
"""

from decimal import Decimal

from django.test import TestCase

from Menu.models import Area, Cajero, Mesa, Orden, Restaurante, TurnoCaja
from Menu.services.pago_service import PagoValidationError, registrar_pago


class PagoServiceTests(TestCase):
    def setUp(self):
        self.restaurante = Restaurante.objects.create(
            nombre="Mainch Pago Test",
            slug="mainch-pago-test",
        )
        self.otro_restaurante = Restaurante.objects.create(
            nombre="Otro Tenant Pago",
            slug="otro-tenant-pago",
        )

        self.cajero = Cajero.objects.create(
            restaurante=self.restaurante,
            nombre="Cajero Pago",
            codigo_empleado="CAJ-PAGO-1",
            pin_hash="hash-pin-1234",
        )
        self.turno_abierto = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )
        self.turno_cerrado = TurnoCaja.objects.create(
            restaurante=self.restaurante,
            cajero=self.cajero,
            estado=TurnoCaja.ESTADO_CERRADO,
        )

        self.otro_cajero = Cajero.objects.create(
            restaurante=self.otro_restaurante,
            nombre="Cajero Otro Tenant",
            codigo_empleado="CAJ-PAGO-OTRO",
            pin_hash="hash-pin-otro",
        )
        self.otro_turno = TurnoCaja.objects.create(
            restaurante=self.otro_restaurante,
            cajero=self.otro_cajero,
            estado=TurnoCaja.ESTADO_ABIERTO,
        )

        self.area = Area.objects.create(restaurante=self.restaurante, nombre="Comedor")
        self.mesa = Mesa.objects.create(
            restaurante=self.restaurante,
            area=self.area,
            numero="1",
            estado=Mesa.ESTADO_OCUPADA,
        )

        self.orden = Orden.objects.create(
            restaurante=self.restaurante,
            cliente="Cliente Pago",
            canal_venta="Local",
            monto_total=Decimal("20000.00"),
            mesa=self.mesa,
        )

    def _registrar(self, **kwargs):
        defaults = dict(
            orden=self.orden,
            metodo_pago="Efectivo",
            monto=Decimal("20000.00"),
            cajero=self.cajero,
            turno=self.turno_abierto,
            restaurante=self.restaurante,
        )
        defaults.update(kwargs)
        return registrar_pago(**defaults)

    def test_registro_pago_completo_exitoso(self):
        resultado = self._registrar()

        self.assertEqual(resultado["total_pagado"], Decimal("20000.00"))
        self.assertEqual(resultado["saldo_pendiente"], Decimal("0.00"))
        self.assertTrue(resultado["orden_completada"])
        self.assertEqual(resultado["cambio"], Decimal("0.00"))
        self.assertIsNotNone(resultado["pago"].id)

        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, Orden.ESTADO_COMPLETADA)

        self.mesa.refresh_from_db()
        self.assertEqual(self.mesa.estado, Mesa.ESTADO_LIBRE)

    def test_pago_dividido_dos_pagos_parciales(self):
        primer_pago = self._registrar(monto=Decimal("8000.00"))
        self.assertFalse(primer_pago["orden_completada"])
        self.assertEqual(primer_pago["total_pagado"], Decimal("8000.00"))
        self.assertEqual(primer_pago["saldo_pendiente"], Decimal("12000.00"))

        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, Orden.ESTADO_EN_CURSO)

        segundo_pago = self._registrar(monto=Decimal("12000.00"))
        self.assertTrue(segundo_pago["orden_completada"])
        self.assertEqual(segundo_pago["total_pagado"], Decimal("20000.00"))
        self.assertEqual(segundo_pago["saldo_pendiente"], Decimal("0.00"))

        self.orden.refresh_from_db()
        self.assertEqual(self.orden.estado, Orden.ESTADO_COMPLETADA)

    def test_rechaza_turno_cerrado(self):
        with self.assertRaises(PagoValidationError):
            self._registrar(turno=self.turno_cerrado)

    def test_rechaza_turno_de_otro_tenant(self):
        with self.assertRaises(PagoValidationError):
            self._registrar(turno=self.otro_turno)

    def test_libera_mesa_al_completar_saldo(self):
        self.assertEqual(self.mesa.estado, Mesa.ESTADO_OCUPADA)

        self._registrar(monto=Decimal("20000.00"))

        self.mesa.refresh_from_db()
        self.assertEqual(self.mesa.estado, Mesa.ESTADO_LIBRE)

    def test_rechaza_orden_cancelada(self):
        self.orden.estado = Orden.ESTADO_ELIMINADA
        self.orden.save(update_fields=["estado"])

        with self.assertRaises(PagoValidationError):
            self._registrar()

    def test_rechaza_orden_ya_totalmente_pagada(self):
        self._registrar(monto=Decimal("20000.00"))

        with self.assertRaises(PagoValidationError):
            self._registrar(monto=Decimal("1.00"))

    def test_calcula_cambio_en_pago_efectivo(self):
        resultado = self._registrar(monto=Decimal("25000.00"), metodo_pago="Efectivo")
        self.assertEqual(resultado["cambio"], Decimal("5000.00"))
        self.assertEqual(resultado["total_pagado"], Decimal("25000.00"))
        self.assertTrue(resultado["orden_completada"])
