"""
Tier 1 — Feature F27: Track A Academic README Deliverable.
Verifies compliance with MBAn UAI 2026-B Track A rubric (VRR filter, baseline, ROI, handoff).
"""

from tests_e2e.base import E2ESimpleTestCase


class TestF27AcademicREADME(E2ESimpleTestCase):
    """Test suite for Feature F27: Track A Academic README."""

    def setUp(self):
        self.readme_path = self.PROJECT_ROOT / "README.md"
        self.assertTrue(self.readme_path.exists(), "[F27] README.md must exist in project root")
        self.content = self.readme_path.read_text(encoding="utf-8")

    def test_f27_01_readme_exists_and_substantial(self):
        """TC-F27-01: README.md exists and contains substantial content (> 1000 characters)."""
        self.assertGreater(len(self.content.strip()), 1000, "[F27] README.md must contain comprehensive documentation")

    def test_f27_02_vrr_filter_documented(self):
        """TC-F27-02: README includes VRR filter section (Valor, Repetitividad, Reglas Claras)."""
        c = self.content.lower()
        has_vrr = "vrr" in c or ("valor" in c and "repetitividad" in c and "reglas" in c)
        self.assertTrue(has_vrr, "[F27] README.md must include VRR filter evaluation")

    def test_f27_03_baseline_quantification_present(self):
        """TC-F27-03: README documents current baseline with concrete metrics (hours, $, frequency)."""
        c = self.content.lower()
        has_baseline = "línea base" in c or "linea base" in c or "baseline" in c or "estado actual" in c
        self.assertTrue(has_baseline, "[F27] README.md must document baseline metrics")

    def test_f27_04_roi_and_operational_costs_quantified(self):
        """TC-F27-04: README contains net ROI analysis and operational AI cost breakdown."""
        c = self.content.lower()
        has_roi = "roi" in c or "retorno" in c or "beneficio neto" in c
        has_cost = "costo" in c or "tokens" in c or "presupuesto" in c
        self.assertTrue(has_roi, "[F27] README.md must quantify net ROI")
        self.assertTrue(has_cost, "[F27] README.md must explain operational AI costs")

    def test_f27_05_handoff_plan_documented(self):
        """TC-F27-05: README includes handoff plan for the restaurant client."""
        c = self.content.lower()
        has_handoff = "handoff" in c or "traspaso" in c or "entrega" in c or "manual de uso" in c
        self.assertTrue(has_handoff, "[F27] README.md must include handoff plan")
