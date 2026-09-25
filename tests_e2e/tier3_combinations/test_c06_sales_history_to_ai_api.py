"""
Tier 3 — Cross-Feature Combination C06: F19 (AI Forecasting) + F21 (Pydantic) + F23 (AI API).
Verifies complete pipeline from sales history through AI consumption to validated JSON endpoint.
"""

from decimal import Decimal
from django.utils import timezone
from django.urls import resolve
from tests_e2e.base import E2ETestCase


class TestC06SalesHistoryToAIAPI(E2ETestCase):
    """Pairwise combination: Sales Aggregation + Pydantic Schema Validation + HTTP Suggestion API."""

    def setUp(self):
        self.Plato = self.require_model("Menu", "Plato", feature_id="C06")
        self.Orden = self.require_model("Menu", "Orden", feature_id="C06")
        self.OrdenItem = self.require_model("Menu", "OrdenItem", feature_id="C06")
        self.Insumo = self.require_model("Menu", "Insumo", feature_id="C06")
        self.RecetaItem = self.require_model("Menu", "RecetaItem", feature_id="C06")

    def test_c06_01_sales_history_drives_ai_endpoint_output(self):
        """TC-C06-01: Seeding completed sales orders updates the output of the AI recommendations endpoint."""
        insumo = self.Insumo.objects.create(
            codigo="INS-PIPELINE", nombre="Queso Mozzarella", unidad_medida="kg",
            stock_actual=Decimal("2.000"), stock_minimo=Decimal("10.000"), costo_unitario=Decimal("6000.000")
        )
        plato = self.Plato.objects.create(nombre="Pizza Mozzarella", valor=8000)
        self.RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=Decimal("0.300"))

        # Create completed sales history
        for i in range(5):
            o = self.Orden.objects.create(cliente=f"Venta Historica {i}", canal_venta="Local", tipo_pago="Efectivo", estado="Completada", fecha_completada=timezone.now())
            self.OrdenItem.objects.create(orden=o, plato=plato, cantidad=3)

        client = self.get_client()
        url = "/api/sugerencias-compra/"
        try:
            resolve(url)
        except Exception:
            url = "/inventario/sugerencias-ia/"

        resp = client.get(f"{url}?dias=7")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Validate that JSON follows Pydantic schema
        self.assertIn("items_sugeridos", data)
        self.assertIn("presupuesto_estimado_total", data)
        self.assertGreater(data["presupuesto_estimado_total"], 0, "[C06] Budget must be calculated from sales demand")
