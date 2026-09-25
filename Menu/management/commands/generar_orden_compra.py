"""
Django Management Command: generar_orden_compra (F23 CLI & Automation Tool).
Generates structured purchase orders from demand forecasting & ROP heuristics.
Supports JSON stdout for piping/cron jobs or rich ANSI table for CLI operators.
"""

import json
import sys
from decimal import Decimal
from django.core.management.base import BaseCommand
from src.ai_forecast.forecaster import generar_sugerencias_compra


class Command(BaseCommand):
    help = "Genera sugerencias automatizadas de órdenes de compra con IA y heurística ROP."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dias",
            type=int,
            default=7,
            help="Horizonte de proyección en días (default: 7)."
        )
        parser.add_argument(
            "--no-llm",
            action="store_true",
            help="Forzar cálculo determinista heurístico ROP sin invocar API de LLM."
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Formato de salida JSON directamente a stdout."
        )
        parser.add_argument(
            "--format",
            choices=["table", "json"],
            default="table",
            help="Formato de salida en consola ('table' o 'json')."
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Ruta de archivo para guardar el resultado JSON (opcional)."
        )

    def handle(self, *args, **options):
        dias = options["dias"]
        usar_llm = not options["no_llm"]
        output_file = options.get("output")
        formato = "json" if options.get("json") else options.get("format", "table")

        self.stderr.write(f"Iniciando cálculo de previsión de compras (Horizonte: {dias} días, LLM: {usar_llm})...")

        try:
            resultado = generar_sugerencias_compra(dias_proyeccion=dias, usar_llm=usar_llm)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error generando sugerencias: {e}"))
            sys.exit(1)

        # Normalización a dict
        if hasattr(resultado, "model_dump"):
            data = resultado.model_dump()
        elif hasattr(resultado, "dict"):
            data = resultado.dict()
        elif isinstance(resultado, dict):
            data = dict(resultado)
        else:
            data = {
                "items_sugeridos": [],
                "presupuesto_estimado_total": 0.0,
                "periodo_dias": dias,
                "metodo": "UNKNOWN"
            }

        items = data.get("items_sugeridos", [])
        total = data.get("presupuesto_estimado_total", 0.0)
        metodo = data.get("metodo", "UNKNOWN")

        # Guardar en archivo si se especificó
        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.stderr.write(self.style.SUCCESS(f"Reporte exportado exitosamente a: {output_file}"))

        if formato == "json":
            self.stdout.write(json.dumps(data, indent=2, ensure_ascii=False))
            return

        # Formato tabla para terminal
        self.stdout.write(self.style.SUCCESS(f"\n========================================================"))
        self.stdout.write(self.style.SUCCESS(f"  Sugerencia de Orden de Compra — Método: {metodo}"))
        self.stdout.write(self.style.SUCCESS(f"  Horizonte: {dias} días | Presupuesto Total: ${total:,.2f} CLP"))
        self.stdout.write(self.style.SUCCESS(f"========================================================\n"))

        if not items:
            self.stdout.write(self.style.WARNING("No se requieren compras urgentes para el periodo."))
            return

        header = f"{'SKU':<12} | {'Insumo':<25} | {'Sugerido':<12} | {'Costo Subtotal':<14} | {'Justificación'}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for it in items:
            sku = it.get("codigo") or it.get("insumo_codigo") or "N/A"
            nombre = it.get("nombre") or it.get("insumo_nombre") or "N/A"
            unidad = it.get("unidad_medida", "un")
            qty = f"{it.get('cantidad_sugerida', 0):.2f} {unidad}"
            costo = f"${it.get('costo_subtotal', 0):,.2f}"
            just = (it.get("justificacion") or "")[:40]
            self.stdout.write(f"{sku:<12} | {nombre[:25]:<25} | {qty:<12} | {costo:<14} | {just}")

        self.stdout.write(f"\nTotal ítems recomendados: {len(items)}\n")
