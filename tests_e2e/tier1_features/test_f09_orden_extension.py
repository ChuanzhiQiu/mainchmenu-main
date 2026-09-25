"""
Tier 1 — Feature F09: Order Model Extension for Idempotent Stock Control.
Verifies stock_descontado boolean and fecha_completada fields on Orden.
"""

from django.utils import timezone
from tests_e2e.base import E2ETestCase


class TestF09OrdenExtension(E2ETestCase):
    """Test suite for Feature F09: Order Model Extension."""

    def setUp(self):
        self.Orden = self.require_model("Menu", "Orden", feature_id="F09")

    def test_f09_01_stock_descontado_field_exists(self):
        """TC-F09-01: Orden model must have stock_descontado boolean field."""
        self.assert_model_has_field(self.Orden, "stock_descontado", expected_type="BooleanField")

    def test_f09_02_fecha_completada_field_exists(self):
        """TC-F09-02: Orden model must have fecha_completada datetime field."""
        self.assert_model_has_field(self.Orden, "fecha_completada", expected_type="DateTimeField")

    def test_f09_03_default_values_on_order_creation(self):
        """TC-F09-03: New orders must initialize stock_descontado=False and fecha_completada=None."""
        orden = self.Orden.objects.create(cliente="Test Idempotency", canal_venta="Local", tipo_pago="Efectivo")
        self.assertFalse(orden.stock_descontado, "[F09] New order stock_descontado should default to False")
        self.assertIsNone(orden.fecha_completada, "[F09] New order fecha_completada should default to None")

    def test_f09_04_updating_order_completion_metadata(self):
        """TC-F09-04: Updating order sets fecha_completada timestamp and stock_descontado=True."""
        orden = self.Orden.objects.create(cliente="Test Complete", canal_venta="Local", tipo_pago="Efectivo")
        now = timezone.now()
        orden.estado = "Completada"
        orden.stock_descontado = True
        orden.fecha_completada = now
        orden.save()

        orden.refresh_from_db()
        self.assertTrue(orden.stock_descontado, "[F09] stock_descontado should be True after completion")
        self.assertIsNotNone(orden.fecha_completada, "[F09] fecha_completada should be recorded")

    def test_f09_05_filtering_pending_deductions(self):
        """TC-F09-05: Querying orders by stock_descontado status partitions completed and pending."""
        o1 = self.Orden.objects.create(cliente="Pendiente 1", canal_venta="Local", tipo_pago="Efectivo")
        o2 = self.Orden.objects.create(cliente="Completado 1", canal_venta="Local", tipo_pago="Efectivo", stock_descontado=True)

        pending = self.Orden.objects.filter(stock_descontado=False)
        self.assertTrue(pending.filter(id=o1.id).exists(), "[F09] Pending query must find order with stock_descontado=False")
        self.assertFalse(pending.filter(id=o2.id).exists(), "[F09] Pending query must exclude order with stock_descontado=True")
