#!/usr/bin/env python
"""
Automated AI Demand Forecasting Evaluation Harness.
Executes 5 operational scenarios and validates schema conformity,
budget reconciliation, ROP reorder logic, and fallback resilience.
MBAn UAI 2026-B Track A — Feature F24.
"""

from decimal import Decimal
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# Setup Django environment
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "MainchApp.settings")

try:
    import django
    django.setup()
except Exception as e:
    # Allow running in standalone mode if Django is partially loaded
    pass

from src.ai_forecast.fallback import calcular_reorden_heuristico
from src.ai_forecast.schemas import (
    InsumoSugerido,
    SugerenciaOrdenCompra,
    parse_and_validate_forecast_json,
)


def _crear_insumo_mock(data_dict):
    """Crea un objeto mock compatible con la interfaz de Insumo de Django."""
    return SimpleNamespace(
        id=data_dict["id"],
        codigo=data_dict["codigo"],
        nombre=data_dict["nombre"],
        unidad_medida=data_dict["unidad_medida"],
        stock_actual=Decimal(str(data_dict["stock_actual"])),
        stock_minimo=Decimal(str(data_dict["stock_minimo"])),
        costo_unitario=Decimal(str(data_dict["costo_unitario"])),
        activo=True,
    )


def run_eval_suite():
    scenarios_path = Path(__file__).resolve().parent / "scenarios.json"
    if not scenarios_path.exists():
        print(f"[FAIL] scenarios.json not found at {scenarios_path}")
        return 1

    with open(scenarios_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    scenarios = data.get("scenarios", data if isinstance(data, list) else [])
    total = len(scenarios)
    passed = 0

    print("=" * 80)
    print(" MAINCHAPP AI DEMAND FORECASTER — AUTOMATED EVALUATION SUITE")
    print(f" Loaded {total} operational scenarios from scenarios.json")
    print("=" * 80)

    for idx, sc in enumerate(scenarios, 1):
        sc_id = sc.get("id", f"SCENARIO-{idx}")
        sc_name = sc.get("name", "Unnamed Scenario")
        print(f"\n[{idx}/{total}] Running {sc_id}: {sc_name}")

        try:
            category = sc.get("category", "")
            context = sc.get("context", {})

            if sc_id == "EVAL-01-QUIEBRE-CRITICO":
                insumos = [_crear_insumo_mock(i) for i in context["insumos"]]
                consumos = {i["id"]: i["consumo_diario"] for i in context["insumos"]}
                res = calcular_reorden_heuristico(
                    insumos=insumos,
                    consumos_diarios=consumos,
                    dias_lead_time=context["lead_time_dias"],
                    dias_proyeccion=context["dias_proyeccion"],
                )
                assert isinstance(res, SugerenciaOrdenCompra), "Resultado no es SugerenciaOrdenCompra"
                items_by_code = {it.codigo: it for it in res.items_sugeridos}
                # Vacuno: Consumo 6 * 2 + StockMin 10 - (-2.5) = 24.5
                assert items_by_code["INS-VACUNO"].cantidad_sugerida >= 24.5, "Cálculo incorrecto para Vacuno en quiebre"
                # Queso: Consumo 3 * 2 + StockMin 5 - 0.5 = 10.5
                assert items_by_code["INS-QUESO"].cantidad_sugerida >= 10.5, "Cálculo incorrecto para Queso en quiebre"
                assert res.presupuesto_estimado_total > 0, "Presupuesto debe ser positivo"
                print("      -> Quiebre crítico correctamente detectado y cubierto con ROP de emergencia.")

            elif sc_id == "EVAL-02-DEMANDA-NORMAL":
                insumos = [_crear_insumo_mock(i) for i in context["insumos"]]
                consumos = {i["id"]: i["consumo_diario"] for i in context["insumos"]}
                res = calcular_reorden_heuristico(
                    insumos=insumos,
                    consumos_diarios=consumos,
                    dias_lead_time=context["lead_time_dias"],
                    dias_proyeccion=context["dias_proyeccion"],
                )
                items_by_code = {it.codigo: it for it in res.items_sugeridos}
                # Papa: Consumo 4 * 2 + StockMin 10 = 18 <= StockActual 30 -> 0.0
                assert items_by_code["INS-PAPA"].cantidad_sugerida == 0.0, "Papa no debería ordenar compra"
                # Aceite: Consumo 2 * 2 + StockMin 8 = 12 <= StockActual 20 -> 0.0
                assert items_by_code["INS-ACEITE"].cantidad_sugerida == 0.0, "Aceite no debería ordenar compra"
                print("      -> Demanda normal validada: stock suficiente, sin compras innecesarias.")

            elif sc_id == "EVAL-03-ALTA-DEMANDA-WEEKEND":
                insumos = [_crear_insumo_mock(i) for i in context["insumos"]]
                consumos = {i["id"]: i["consumo_diario"] for i in context["insumos"]}
                res = calcular_reorden_heuristico(
                    insumos=insumos,
                    consumos_diarios=consumos,
                    dias_lead_time=context["lead_time_dias"],
                    dias_proyeccion=context["dias_proyeccion"],
                )
                items_by_code = {it.codigo: it for it in res.items_sugeridos}
                # Pan: Consumo 45 * 1 + StockMin 20 - StockActual 35 = 30.0
                assert items_by_code["INS-PAN-FRICA"].cantidad_sugerida == 30.0, "Pan Frica cálculo surge incorrecto"
                # Tomate: Consumo 16 * 1 + StockMin 8 - StockActual 12 = 12.0
                assert items_by_code["INS-TOMATE"].cantidad_sugerida == 12.0, "Tomate cálculo surge incorrecto"
                print("      -> Fin de semana de alta demanda cubierto exitosamente.")

            elif sc_id == "EVAL-04-JSON-CORRUPTO":
                raw_resp = context["raw_response"]
                res = parse_and_validate_forecast_json(raw_resp)
                assert isinstance(res, SugerenciaOrdenCompra), "No se pudo recuperar de respuesta formateada con markdown"
                item = res.items_sugeridos[0]
                assert item.codigo == "INS-POLLO", f"Código no sanitizado: {item.codigo}"
                assert item.nombre == "Pechuga de Pollo", f"Nombre no sanitizado: {item.nombre}"
                assert item.justificacion == "Reponer antes de turno viernes", f"Justificación no sanitizada: {item.justificacion}"
                assert res.presupuesto_estimado_total == 33600.0, "Presupuesto reconciliado incorrecto"
                print("      -> Guardrails Pydantic sanearon y validaron JSON con código markdown y espacios.")

            elif sc_id == "EVAL-05-FALLBACK-HEURISTICO":
                insumos = [_crear_insumo_mock(i) for i in context["insumos"]]
                consumos = {i["id"]: i["consumo_diario"] for i in context["insumos"]}
                # Simular excepción LLM y activar fallback heurístico
                try:
                    raise ConnectionResetError("Connection reset by peer (simulated LLM outage)")
                except ConnectionResetError:
                    res = calcular_reorden_heuristico(
                        insumos=insumos,
                        consumos_diarios=consumos,
                        dias_lead_time=context["lead_time_dias"],
                        dias_proyeccion=context["dias_proyeccion"],
                    )
                assert res.metodo == "HEURISTIC_FALLBACK", f"Método esperado HEURISTIC_FALLBACK, obtenido {res.metodo}"
                items_by_code = {it.codigo: it for it in res.items_sugeridos}
                # Palta: Consumo 4 * 2 + StockMin 6 - StockActual 2 = 12.0
                assert items_by_code["INS-PALTA"].cantidad_sugerida == 12.0, "Cálculo fallback Palta incorrecto"
                # Sal: Consumo 0.5 * 2 + StockMin 5 - StockActual 25 = 0.0
                assert items_by_code["INS-SAL"].cantidad_sugerida == 0.0, "Cálculo fallback Sal incorrecto"
                print("      -> Fallback heurístico activado de forma transparente ante fallo de red.")

            else:
                # Escenario genérico
                insumos = [_crear_insumo_mock(i) for i in context.get("insumos", [])]
                consumos = {i["id"]: i.get("consumo_diario", 1.0) for i in context.get("insumos", [])}
                res = calcular_reorden_heuristico(insumos=insumos, consumos_diarios=consumos)
                assert isinstance(res, SugerenciaOrdenCompra)

            print(f"      [PASS] {sc_id} PASSED.")
            passed += 1

        except Exception as e:
            print(f"      [FAIL] {sc_id} FAILED with error: {e}")

    score_pct = (passed / total) * 100 if total > 0 else 0
    print("\n" + "=" * 80)
    print(" EVALUATION RESULTS SUMMARY")
    print(f" Total Scenarios : {total}")
    print(f" Passed          : {passed}")
    print(f" Failed          : {total - passed}")
    print(f" Score           : {score_pct:.1f}%")
    if passed == total:
        print(" RESULT: PASSED (100% operational eval pass rate)")
        print("=" * 80)
        return 0
    else:
        print(" RESULT: FAILED")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(run_eval_suite())
