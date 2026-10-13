"""
Unit tests for Phase 2-5 connectors and services.
All HTTP calls mocked; no network or DB dependencies.
"""
import re
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Auto-patch _scrape_live for all new connectors
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_all_live_scraping():
    with (
        patch("src.connectors.regulatory.usa.cra.CraConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.ffiec_call_report.FfiecCallReportConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.ffiec_bsa.FfiecBsaConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.ffiec_cybersecurity.FfiecCybersecurityConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.irr.IrrConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.cre_concentration.CreConcentrationConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.states.nydfs.NydfsConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.states.ca_dfpi.CaDfpiConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.states.tx_dob.TxDobConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.states.il_idfpr.IlIdfprConnector._scrape_live", return_value=[]),
        patch("src.connectors.regulatory.usa.states.fl_ofr.FlOfrConnector._scrape_live", return_value=[]),
    ):
        yield


# ===========================================================================
# Phase 2 — Critical connectors
# ===========================================================================

class TestCraConnector:
    def test_connects(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        c = CraConnector()
        assert c.connect() is True
        assert c.test_connection() is True

    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        result = CraConnector().extract_all_rules()
        for key in ("regulations", "requirements", "kpis", "test_scenarios", "scraped_documents"):
            assert key in result, f"Missing key: {key}"

    def test_regulations_have_required_fields(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        regs = CraConnector()._build_regulations()
        assert len(regs) >= 3
        for reg in regs:
            assert "regulation_id" in reg
            assert "cfr_citation" in reg
            assert reg["jurisdiction_level"] == "federal"
            assert reg["geography"] == "usa"

    def test_requirements_cover_assessment_area(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        reqs = CraConnector()._build_requirements()
        req_ids = [r["requirement_id"] for r in reqs]
        assert "CRA-REQ-ASSESS-001" in req_ids

    def test_requirements_include_rating_system(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        reqs = CraConnector()._build_requirements()
        rating_req = next(r for r in reqs if r["requirement_id"] == "CRA-REQ-RATING-001")
        assert "Outstanding" in rating_req["parameters"]["ratings"]
        assert "Substantial Noncompliance" in rating_req["parameters"]["ratings"]

    def test_kpis_present(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        kpis = CraConnector()._build_kpis()
        assert len(kpis) >= 3
        assert all("kpi_id" in k for k in kpis)

    def test_large_bank_threshold(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        reqs = CraConnector()._build_requirements()
        large = next(r for r in reqs if r["requirement_id"] == "CRA-REQ-EXAM-LARGE-001")
        assert large["threshold_amount"] == 1_400_000_000
        assert large["parameters"]["lending_test_weight"] == 0.50

    def test_geography_attribute(self):
        from src.connectors.regulatory.usa.cra import CraConnector
        assert CraConnector.geography == "usa"
        assert CraConnector.connector_name == "cra"


class TestFfiecCallReportConnector:
    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.ffiec_call_report import FfiecCallReportConnector
        result = FfiecCallReportConnector().extract_all_rules()
        assert set(result.keys()) >= {"regulations", "requirements", "kpis", "test_scenarios"}

    def test_three_form_types(self):
        from src.connectors.regulatory.usa.ffiec_call_report import FfiecCallReportConnector
        regs = FfiecCallReportConnector()._build_regulations()
        reg_ids = {r["regulation_id"] for r in regs}
        assert "FFIEC-CALL-031" in reg_ids
        assert "FFIEC-CALL-041" in reg_ids
        assert "FFIEC-CALL-051" in reg_ids

    def test_filing_deadline_requirement(self):
        from src.connectors.regulatory.usa.ffiec_call_report import FfiecCallReportConnector
        reqs = FfiecCallReportConnector()._build_requirements()
        deadline_req = next(r for r in reqs if r["requirement_id"] == "FFIEC-CALL-DEADLINE-001")
        assert deadline_req["parameters"]["deadline_days_after_quarter"] == 30

    def test_form_eligibility_threshold(self):
        from src.connectors.regulatory.usa.ffiec_call_report import FfiecCallReportConnector
        reqs = FfiecCallReportConnector()._build_requirements()
        eligibility = next(r for r in reqs if r["requirement_id"] == "FFIEC-CALL-FORM-SELECT-001")
        assert eligibility["threshold_amount"] == 5_000_000_000

    def test_xbrl_submission_requirement(self):
        from src.connectors.regulatory.usa.ffiec_call_report import FfiecCallReportConnector
        reqs = FfiecCallReportConnector()._build_requirements()
        xbrl = next(r for r in reqs if r["requirement_id"] == "FFIEC-CALL-XBRL-001")
        assert xbrl["parameters"]["submission_method"] == "XBRL_via_CDR"

    def test_kpis_include_timeliness(self):
        from src.connectors.regulatory.usa.ffiec_call_report import FfiecCallReportConnector
        kpis = FfiecCallReportConnector()._build_kpis()
        kpi_ids = [k["kpi_id"] for k in kpis]
        assert "FFIEC-CALL-KPI-001" in kpi_ids


class TestFfiecBsaConnector:
    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.ffiec_bsa import FfiecBsaConnector
        result = FfiecBsaConnector().extract_all_rules()
        assert "regulations" in result

    def test_four_pillar_requirement(self):
        from src.connectors.regulatory.usa.ffiec_bsa import FfiecBsaConnector
        reqs = FfiecBsaConnector()._build_requirements()
        pillars = next(r for r in reqs if r["requirement_id"] == "FFIEC-BSA-PILLAR-001")
        assert len(pillars["parameters"]["pillars"]) == 4
        assert "bsa_officer_designated" in pillars["parameters"]["pillars"]

    def test_314a_response_deadline(self):
        from src.connectors.regulatory.usa.ffiec_bsa import FfiecBsaConnector
        reqs = FfiecBsaConnector()._build_requirements()
        req_314 = next(r for r in reqs if r["requirement_id"] == "FFIEC-BSA-314A-001")
        assert req_314["parameters"]["response_deadline_days"] == 14
        assert req_314["parameters"]["tippoff_prohibited"] is True

    def test_edd_high_risk_categories(self):
        from src.connectors.regulatory.usa.ffiec_bsa import FfiecBsaConnector
        reqs = FfiecBsaConnector()._build_requirements()
        edd = next(r for r in reqs if r["requirement_id"] == "FFIEC-BSA-EDD-001")
        assert "politically_exposed_persons" in edd["parameters"]["high_risk_categories"]


# ===========================================================================
# Phase 3 — High priority connectors
# ===========================================================================

class TestFfiecCybersecurityConnector:
    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.ffiec_cybersecurity import FfiecCybersecurityConnector
        result = FfiecCybersecurityConnector().extract_all_rules()
        assert set(result.keys()) >= {"regulations", "requirements", "kpis", "test_scenarios"}

    def test_irp_five_categories(self):
        from src.connectors.regulatory.usa.ffiec_cybersecurity import FfiecCybersecurityConnector
        reqs = FfiecCybersecurityConnector()._build_requirements()
        irp = next(r for r in reqs if r["requirement_id"] == "FFIEC-CAT-IRP-001")
        assert len(irp["parameters"]["irp_categories"]) == 5

    def test_maturity_five_domains(self):
        from src.connectors.regulatory.usa.ffiec_cybersecurity import FfiecCybersecurityConnector
        reqs = FfiecCybersecurityConnector()._build_requirements()
        maturity = next(r for r in reqs if r["requirement_id"] == "FFIEC-CAT-MATURITY-001")
        assert len(maturity["parameters"]["domains"]) == 5
        assert "Baseline" in maturity["parameters"]["maturity_levels"]
        assert "Innovative" in maturity["parameters"]["maturity_levels"]

    def test_patch_management_sla(self):
        from src.connectors.regulatory.usa.ffiec_cybersecurity import FfiecCybersecurityConnector
        reqs = FfiecCybersecurityConnector()._build_requirements()
        patch_req = next(r for r in reqs if r["requirement_id"] == "FFIEC-ITSEC-PATCH-001")
        assert patch_req["parameters"]["critical_patch_days"] == 30
        assert patch_req["parameters"]["high_patch_days"] == 90

    def test_mfa_requirement_present(self):
        from src.connectors.regulatory.usa.ffiec_cybersecurity import FfiecCybersecurityConnector
        reqs = FfiecCybersecurityConnector()._build_requirements()
        mfa = next(r for r in reqs if r["requirement_id"] == "FFIEC-ITSEC-MFA-001")
        assert mfa["parameters"]["minimum_factors"] == 2


class TestIrrConnector:
    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.irr import IrrConnector
        result = IrrConnector().extract_all_rules()
        assert set(result.keys()) >= {"regulations", "requirements", "kpis", "test_scenarios"}

    def test_eve_shock_scenarios(self):
        from src.connectors.regulatory.usa.irr import IrrConnector
        reqs = IrrConnector()._build_requirements()
        eve = next(r for r in reqs if r["requirement_id"] == "IRR-REQ-EVE-001")
        assert 400 in eve["parameters"]["rate_shocks_bps"]
        assert -400 in eve["parameters"]["rate_shocks_bps"]
        assert eve["parameters"]["eve_decline_alert_pct"] == 20.0

    def test_alco_minimum_meetings(self):
        from src.connectors.regulatory.usa.irr import IrrConnector
        reqs = IrrConnector()._build_requirements()
        alco = next(r for r in reqs if r["requirement_id"] == "IRR-REQ-ALCO-001")
        assert alco["parameters"]["minimum_meetings_per_year"] == 4

    def test_model_validation_period(self):
        from src.connectors.regulatory.usa.irr import IrrConnector
        reqs = IrrConnector()._build_requirements()
        model = next(r for r in reqs if r["requirement_id"] == "IRR-REQ-MODEL-001")
        assert model["parameters"]["validation_frequency_years"] == 3

    def test_connector_name(self):
        from src.connectors.regulatory.usa.irr import IrrConnector
        assert IrrConnector.connector_name == "irr"


class TestCreConcentrationConnector:
    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.cre_concentration import CreConcentrationConnector
        result = CreConcentrationConnector().extract_all_rules()
        assert set(result.keys()) >= {"regulations", "requirements", "kpis", "test_scenarios"}

    def test_construction_threshold_100pct(self):
        from src.connectors.regulatory.usa.cre_concentration import CreConcentrationConnector
        reqs = CreConcentrationConnector()._build_requirements()
        constr = next(r for r in reqs if r["requirement_id"] == "CRE-REQ-CONSTR-001")
        assert constr["parameters"]["threshold_pct_of_capital"] == 100.0

    def test_total_cre_threshold_300pct(self):
        from src.connectors.regulatory.usa.cre_concentration import CreConcentrationConnector
        reqs = CreConcentrationConnector()._build_requirements()
        total = next(r for r in reqs if r["requirement_id"] == "CRE-REQ-TOTAL-001")
        assert total["parameters"]["threshold_pct_of_capital"] == 300.0

    def test_growth_threshold_50pct_36months(self):
        from src.connectors.regulatory.usa.cre_concentration import CreConcentrationConnector
        reqs = CreConcentrationConnector()._build_requirements()
        growth = next(r for r in reqs if r["requirement_id"] == "CRE-REQ-GROWTH-001")
        assert growth["parameters"]["growth_threshold_pct"] == 50.0
        assert growth["parameters"]["growth_window_months"] == 36

    def test_stress_test_scenario_20pct(self):
        from src.connectors.regulatory.usa.cre_concentration import CreConcentrationConnector
        reqs = CreConcentrationConnector()._build_requirements()
        stress = next(r for r in reqs if r["requirement_id"] == "CRE-REQ-STRESS-001")
        assert stress["parameters"]["stress_scenario"] == "20_pct_collateral_decline"

    def test_kpi_alert_thresholds(self):
        from src.connectors.regulatory.usa.cre_concentration import CreConcentrationConnector
        kpis = CreConcentrationConnector()._build_kpis()
        construction_kpi = next(k for k in kpis if k["kpi_id"] == "CRE-KPI-001")
        total_kpi = next(k for k in kpis if k["kpi_id"] == "CRE-KPI-002")
        assert construction_kpi["target_value"] == 100.0
        assert total_kpi["target_value"] == 300.0


# ===========================================================================
# Phase 5 — State connectors
# ===========================================================================

class TestNydfsConnector:
    def test_extract_all_rules_structure(self):
        from src.connectors.regulatory.usa.states.nydfs import NydfsConnector
        result = NydfsConnector().extract_all_rules()
        assert set(result.keys()) >= {"regulations", "requirements", "kpis", "test_scenarios"}

    def test_state_code_ny(self):
        from src.connectors.regulatory.usa.states.nydfs import NydfsConnector
        regs = NydfsConnector()._build_regulations()
        assert all(r["state_code"] == "NY" for r in regs)
        assert all(r["jurisdiction_level"] == "state" for r in regs)

    def test_part_500_ciso_requirement(self):
        from src.connectors.regulatory.usa.states.nydfs import NydfsConnector
        reqs = NydfsConnector()._build_requirements()
        ciso = next(r for r in reqs if r["requirement_id"] == "NYDFS-500-CISO-001")
        assert ciso["parameters"]["ciso_required"] is True

    def test_72hour_breach_notification(self):
        from src.connectors.regulatory.usa.states.nydfs import NydfsConnector
        reqs = NydfsConnector()._build_requirements()
        breach = next(r for r in reqs if r["requirement_id"] == "NYDFS-500-BREACH-001")
        assert breach["parameters"]["notification_hours"] == 72
        assert breach["parameters"]["notification_recipient"] == "NYDFS"

    def test_annual_certification_deadline(self):
        from src.connectors.regulatory.usa.states.nydfs import NydfsConnector
        reqs = NydfsConnector()._build_requirements()
        cert = next(r for r in reqs if r["requirement_id"] == "NYDFS-500-CERT-001")
        assert "April 15" in cert["parameters"]["deadline"]

    def test_part_504_certification_present(self):
        from src.connectors.regulatory.usa.states.nydfs import NydfsConnector
        reqs = NydfsConnector()._build_requirements()
        cert504 = next(r for r in reqs if r["requirement_id"] == "NYDFS-504-CERT-001")
        assert "transaction_monitoring_program" in cert504["parameters"]["covers"]


class TestCaDfpiConnector:
    def test_state_code_ca(self):
        from src.connectors.regulatory.usa.states.ca_dfpi import CaDfpiConnector
        regs = CaDfpiConnector()._build_regulations()
        assert all(r["state_code"] == "CA" for r in regs)

    def test_ccpa_opt_out_15_days(self):
        from src.connectors.regulatory.usa.states.ca_dfpi import CaDfpiConnector
        reqs = CaDfpiConnector()._build_requirements()
        opt_out = next(r for r in reqs if r["requirement_id"] == "CA-CCPA-OPT-OUT-001")
        assert opt_out["parameters"]["opt_out_response_days"] == 15
        assert opt_out["parameters"]["opt_out_persistence_months"] == 12

    def test_dfal_effective_date(self):
        from src.connectors.regulatory.usa.states.ca_dfpi import CaDfpiConnector
        reqs = CaDfpiConnector()._build_requirements()
        dfal = next(r for r in reqs if r["requirement_id"] == "CA-DFAL-LIC-001")
        assert "2025-07-01" in dfal["parameters"]["effective_date"]


class TestTxDobConnector:
    def test_state_code_tx(self):
        from src.connectors.regulatory.usa.states.tx_dob import TxDobConnector
        regs = TxDobConnector()._build_regulations()
        assert all(r["state_code"] == "TX" for r in regs)

    def test_money_services_net_worth(self):
        from src.connectors.regulatory.usa.states.tx_dob import TxDobConnector
        reqs = TxDobConnector()._build_requirements()
        lic = next(r for r in reqs if r["requirement_id"] == "TX-MSA-LIC-001")
        assert lic["threshold_amount"] == 100_000
        assert lic["parameters"]["surety_bond_required"] is True


class TestIlIdfprConnector:
    def test_state_code_il(self):
        from src.connectors.regulatory.usa.states.il_idfpr import IlIdfprConnector
        regs = IlIdfprConnector()._build_regulations()
        assert all(r["state_code"] == "IL" for r in regs)

    def test_toma_net_worth(self):
        from src.connectors.regulatory.usa.states.il_idfpr import IlIdfprConnector
        reqs = IlIdfprConnector()._build_requirements()
        lic = next(r for r in reqs if r["requirement_id"] == "IL-TOMA-LIC-001")
        assert lic["threshold_amount"] == 35_000


class TestFlOfrConnector:
    def test_state_code_fl(self):
        from src.connectors.regulatory.usa.states.fl_ofr import FlOfrConnector
        regs = FlOfrConnector()._build_regulations()
        assert all(r["state_code"] == "FL" for r in regs)

    def test_msa_net_worth(self):
        from src.connectors.regulatory.usa.states.fl_ofr import FlOfrConnector
        reqs = FlOfrConnector()._build_requirements()
        lic = next(r for r in reqs if r["requirement_id"] == "FL-MSA-LIC-001")
        assert lic["threshold_amount"] == 100_000


# ===========================================================================
# Phase 4 — Services (unit tests, no DB)
# ===========================================================================

class TestObligationExtractor:
    def test_extract_obligation_sentences_basic(self):
        from src.services.obligation_extractor import extract_obligation_sentences
        text = (
            "The bank shall file a CTR within 15 days. "
            "Customer service hours are 9-5. "
            "All staff must complete annual training. "
            "Coffee is available in the break room."
        )
        obligations = extract_obligation_sentences(text)
        assert len(obligations) == 2
        assert any("shall" in s.lower() for s in obligations)
        assert any("must" in s.lower() for s in obligations)

    def test_extract_empty_text(self):
        from src.services.obligation_extractor import extract_obligation_sentences
        assert extract_obligation_sentences("") == []
        assert extract_obligation_sentences(None) == []

    def test_classify_obligation_type(self):
        from src.services.obligation_extractor import _classify_obligation_type
        assert _classify_obligation_type("Banks shall maintain records") == "shall"
        assert _classify_obligation_type("Banks must file reports") == "must"
        assert _classify_obligation_type("Fees are prohibited from being charged") == "prohibited"
        assert _classify_obligation_type("Institutions may not engage in X") == "prohibited"
        assert _classify_obligation_type("Banks are required to verify identity") == "required"

    def test_classify_returns_prohibited_first(self):
        # prohibited takes priority over shall/must
        from src.services.obligation_extractor import _classify_obligation_type
        result = _classify_obligation_type("Banks shall not and are prohibited from discriminating")
        assert result == "prohibited"

    def test_obligation_count_for_rule(self):
        from src.services.obligation_extractor import extract_obligation_sentences
        text = (
            "Institutions shall file SARs within 30 days. "
            "CTR must be filed for cash over $10,000. "
            "Records required to be retained for 5 years. "
            "The weather is nice today."
        )
        results = extract_obligation_sentences(text)
        assert len(results) == 3


class TestEnforcementPenaltyParser:
    def test_parse_million(self):
        from src.services.enforcement_actions import _parse_penalty_usd
        assert _parse_penalty_usd("fined $1.5 million") == Decimal("1500000")

    def test_parse_billion(self):
        from src.services.enforcement_actions import _parse_penalty_usd
        assert _parse_penalty_usd("penalty of $2 billion") == Decimal("2000000000")

    def test_parse_plain_usd(self):
        from src.services.enforcement_actions import _parse_penalty_usd
        assert _parse_penalty_usd("$500,000 civil money penalty") == Decimal("500000")

    def test_parse_no_penalty(self):
        from src.services.enforcement_actions import _parse_penalty_usd
        assert _parse_penalty_usd("cease and desist order issued") is None

    def test_extract_regulations_cited(self):
        from src.services.enforcement_actions import _extract_regulations_cited
        text = "Violations of 12 C.F.R. Part 30 and BSA requirements were found."
        regs = _extract_regulations_cited(text)
        assert any("12 C.F.R." in r for r in regs)
        assert any("BSA" in r for r in regs)

    def test_infer_action_type_consent_order(self):
        from src.services.enforcement_actions import EnforcementActionService
        svc = EnforcementActionService()
        assert svc._infer_action_type("Consent Order Issued", "") == "consent_order"
        assert svc._infer_action_type("Civil Money Penalty", "") == "civil_money_penalty"
        assert svc._infer_action_type("Cease and Desist", "") == "cease_and_desist"


class TestEnforcementRiskScorer:
    def test_compute_risk_score_zero_actions(self):
        from src.services.enforcement_risk_scorer import compute_risk_score
        assert compute_risk_score(0, None, None) == 0.0

    def test_compute_risk_score_high_actions(self):
        from src.services.enforcement_risk_scorer import compute_risk_score
        score = compute_risk_score(100, 10_000_000, "increasing")
        assert 0.0 < score <= 1.0
        assert score > 0.5  # high frequency + penalty + increasing trend

    def test_compute_risk_score_decreasing_trend(self):
        from src.services.enforcement_risk_scorer import compute_risk_score
        increasing = compute_risk_score(10, 1_000_000, "increasing")
        decreasing = compute_risk_score(10, 1_000_000, "decreasing")
        assert increasing > decreasing

    def test_compute_risk_score_capped_at_1(self):
        from src.services.enforcement_risk_scorer import compute_risk_score
        score = compute_risk_score(10000, 1e15, "increasing")
        assert score <= 1.0

    def test_normalise_function(self):
        from src.services.enforcement_risk_scorer import _normalise
        assert _normalise(0, 100) == 0.0
        assert 0.0 < _normalise(50, 100) < 1.0
        assert _normalise(10000, 100) == 1.0


class TestDeadlineTracker:
    def test_next_occurrence_future(self):
        from src.services.deadline_tracker import _next_occurrence
        from datetime import date
        result = _next_occurrence((12, 31))
        assert isinstance(result, date)
        assert result >= date.today()

    def test_seed_deadlines_have_required_fields(self):
        from src.services.deadline_tracker import _SEED_DEADLINES
        for dl in _SEED_DEADLINES:
            assert "deadline_key" in dl
            assert "regulator" in dl
            assert "deadline_type" in dl
            assert "deadline_name" in dl
            # must have either fixed_date or month_day
            assert "fixed_date" in dl or "month_day" in dl

    def test_seed_deadlines_include_call_reports(self):
        from src.services.deadline_tracker import _SEED_DEADLINES
        keys = [d["deadline_key"] for d in _SEED_DEADLINES]
        for q in ("Q1", "Q2", "Q3", "Q4"):
            assert f"FFIEC-CALL-{q}" in keys

    def test_seed_includes_1071_deadline(self):
        from src.services.deadline_tracker import _SEED_DEADLINES
        keys = [d["deadline_key"] for d in _SEED_DEADLINES]
        assert "CFPB-1071-COMPLIANCE" in keys

    def test_alert_thresholds_defined(self):
        from src.services.deadline_tracker import _ALERT_THRESHOLDS
        assert 30 in _ALERT_THRESHOLDS
        assert 7 in _ALERT_THRESHOLDS


class TestChangeManager:
    def test_compute_text_diff_basic(self):
        from src.services.change_manager import _compute_text_diff
        old = "Banks shall maintain records. Customer data is protected."
        new = "Banks shall maintain records for 5 years. New regulatory requirements apply."
        diff = _compute_text_diff(old, new)
        assert "change_pct" in diff
        assert isinstance(diff["added"], list)
        assert isinstance(diff["removed"], list)

    def test_classify_change_type_new_rule(self):
        from src.services.change_manager import _classify_change_type
        assert _classify_change_type("", "New rule text", is_new_rule=True) == "added"

    def test_classify_change_type_removed(self):
        from src.services.change_manager import _classify_change_type
        assert _classify_change_type("Old text here.", "", is_new_rule=False) == "removed"

    def test_classify_change_type_minor_is_clarified(self):
        from src.services.change_manager import _classify_change_type
        old = "Banks shall maintain records for five years."
        new = "Banks shall maintain records for five (5) years."
        # Very small change → clarified
        result = _classify_change_type(old, new, is_new_rule=False)
        assert result in ("clarified", "modified")

    def test_impact_rating_thresholds(self):
        from src.services.change_manager import _impact_rating
        assert _impact_rating(60.0) == "high"
        assert _impact_rating(25.0) == "medium"
        assert _impact_rating(5.0) == "low"

    def test_change_ref_format(self):
        from src.services.change_manager import _change_ref
        from datetime import datetime
        ref = _change_ref("OCC-BSA-001", datetime(2026, 4, 21, 12, 0, 0))
        assert ref.startswith("CHG-")
        assert len(ref) == 12  # CHG- + 8 hex chars

    def test_text_diff_identical_returns_zero_pct(self):
        from src.services.change_manager import _compute_text_diff
        text = "Banks shall file CTRs. Records must be kept."
        diff = _compute_text_diff(text, text)
        assert diff["change_pct"] == 0.0
        assert diff["added"] == []
        assert diff["removed"] == []


# ===========================================================================
# Manager import resolution for new connectors
# ===========================================================================

class TestManagerImportsNewConnectors:
    def test_cra_importable_via_manager(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "cra")
        assert cls is not None
        assert cls.connector_name == "cra"

    def test_ffiec_call_report_importable(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "ffiec_call_report")
        assert cls is not None

    def test_irr_importable(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "irr")
        assert cls is not None

    def test_nydfs_importable_via_states_subpackage(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "nydfs")
        assert cls is not None
        assert cls.connector_name == "nydfs"

    def test_ca_dfpi_importable(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "ca_dfpi")
        assert cls is not None

    def test_tx_dob_importable(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "tx_dob")
        assert cls is not None

    def test_il_idfpr_importable(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "il_idfpr")
        assert cls is not None

    def test_fl_ofr_importable(self):
        from src.connectors.manager import PluginManager
        mgr = PluginManager(geography="usa")
        cls = mgr._import_connector("regulatory", "fl_ofr")
        assert cls is not None
