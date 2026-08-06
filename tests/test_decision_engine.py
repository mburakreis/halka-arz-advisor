from datetime import date, datetime

from halka_arz_advisor.decision.audit import CompanyDecisionInputs
from halka_arz_advisor.decision.engine import evaluate_decision, score_category
from halka_arz_advisor.decision.scoring_config import get_scoring_config
from halka_arz_advisor.decision.snapshot import build_decision_snapshot
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef as FactSourceRef, build_extracted_facts
from halka_arz_advisor.kap.financials import FinancialObservation, SourceRef as ObsSourceRef
from halka_arz_advisor.kap.models import KapDisclosure

RECORD_ID = "ipo:ORNK:2026 / 1"

SRC_P = FactSourceRef("approved_prospectus", "d-p", "url-p", 1)
SRC_A = FactSourceRef("investor_sale_announcement", "d-a", "url-a", 1)
SRC_PDR = FactSourceRef("price_determination_report", "d-pdr", "url-pdr", 8)
OBS_SRC = ObsSourceRef("price_determination_report", "d-pdr", "url-pdr", 26, "digital")


def _disclosure(disclosure_id: str, document_type: str, published_at: datetime) -> KapDisclosure:
    return KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=1,
        published_at=published_at,
        company_name="Örnek Enerji A.Ş.",
        ticker="ORNK",
        title="test",
        summary="",
        document_type=document_type,
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id=RECORD_ID,
        match_method="ticker",
        raw={},
        pdf_status="ok",
    )


def _financial_obs(metric: str, value: float, year: int, *, scope="standalone") -> FinancialObservation:
    return FinancialObservation(
        metric, value, "TRY", "unit", date(year, 1, 1), date(year, 12, 31), "ANNUAL", scope, None, str(value), OBS_SRC
    )


def test_high_quality_complete_case_participates():
    prospectus_obs = {
        "business_description": FieldObservation("bir sirket", "snip", SRC_P),
        "key_risk_factors": FieldObservation(["risk1"], "snip", SRC_P),
        "use_of_proceeds_plan": FieldObservation(["buyume"], "snip", SRC_P),
        "capital_increase_shares": FieldObservation(1000.0, "snip", SRC_P),
        "secondary_sale_shares": FieldObservation(200.0, "snip", SRC_P),
        "total_offered_shares": FieldObservation(1200.0, "snip", SRC_P),
        "capital_increase_ratio": FieldObservation(50.0, "snip", SRC_P),
        "subscription_start_date": FieldObservation(date(2026, 1, 1), "snip", SRC_P),
        "subscription_end_date": FieldObservation(date(2026, 1, 3), "snip", SRC_P),
        "distribution_method": FieldObservation("sabit fiyatla talep toplama", "snip", SRC_P),
        "offering_price": FieldObservation(10.0, "snip", SRC_P),
        "currency": FieldObservation("TRY", "snip", SRC_P),
    }
    announcement_obs = {
        "offering_price": FieldObservation(10.0, "snip", SRC_A),
        "currency": FieldObservation("TRY", "snip", SRC_A),
    }
    pdr_obs = {
        "headline_discount_percentage": FieldObservation(20.0, "snip", SRC_PDR),
        "reported_pe": FieldObservation(10.0, "snip", SRC_PDR),
        "reported_post_money_market_cap": FieldObservation(500.0, "snip", SRC_PDR),
    }
    facts = build_extracted_facts(prospectus_obs, announcement_obs, None, pdr_obs)

    financial_observations = (
        _financial_obs("revenue", 1000.0, 2023),
        _financial_obs("revenue", 1300.0, 2024),
        _financial_obs("net_income", 100.0, 2024),
        _financial_obs("financial_debt", 400.0, 2024),
        _financial_obs("cash_and_equivalents", 100.0, 2024),
        _financial_obs("equity", 1000.0, 2024),
        _financial_obs("current_assets", 300.0, 2024),
        _financial_obs("current_liabilities", 150.0, 2024),
        _financial_obs("operating_cash_flow", 90.0, 2024),
        _financial_obs("operating_profit", 120.0, 2024),
        _financial_obs("finance_expense", 20.0, 2024),
    )
    disclosures = (
        _disclosure("d-p", "approved_prospectus", datetime(2026, 1, 1)),
        _disclosure("d-a", "investor_sale_announcement", datetime(2026, 1, 2)),
        _disclosure("d-pdr", "price_determination_report", datetime(2026, 1, 3)),
    )

    inputs = CompanyDecisionInputs(
        spk_record_id=RECORD_ID,
        spk_record=None,
        application_record=None,
        facts=facts,
        disclosures=disclosures,
        financial_observations=financial_observations,
        company_name="Örnek Enerji A.Ş.",
    )
    snapshot = build_decision_snapshot(inputs, reference_date=datetime(2026, 1, 10))
    result = evaluate_decision(snapshot)

    assert result.signal == "participate"
    assert result.total_score is not None and result.total_score >= 68
    assert result.confidence_score >= 70
    assert all(not rule.triggered for rule in result.hard_rules)
    assert result.rule_version == "expert_v0"
    assert result.weight_set_version == "expert_v0"
    # The category gates behind "participate" must both be real,
    # available scores, not a missing value that happened not to block.
    assert result.category_score("valuation").score >= 55
    assert result.category_score("fundamental_quality").score >= 55


def test_no_data_is_insufficient_data():
    inputs = CompanyDecisionInputs(
        spk_record_id=RECORD_ID, spk_record=None, application_record=None, facts=None, disclosures=(), financial_observations=()
    )
    snapshot = build_decision_snapshot(inputs, reference_date=datetime(2026, 1, 10))
    result = evaluate_decision(snapshot)

    assert result.signal == "insufficient_data"
    assert result.total_score is None
    assert all(c.status == "INSUFFICIENT" for c in result.category_scores)
    triggered = {rule.rule_id for rule in result.hard_rules if rule.triggered}
    assert "missing_mandatory_documents" in triggered
    assert "insufficient_mandatory_category_coverage" in triggered


def test_unresolved_conflict_forces_insufficient_data():
    # Prospectus and announcement disagree on the offering price — a
    # conflict on a critical field must never be silently picked
    # between, and must force insufficient_data regardless of whatever
    # else is available.
    prospectus_obs = {"offering_price": FieldObservation(10.0, "snip", SRC_P), "currency": FieldObservation("TRY", "snip", SRC_P)}
    announcement_obs = {"offering_price": FieldObservation(12.0, "snip", SRC_A), "currency": FieldObservation("TRY", "snip", SRC_A)}
    facts = build_extracted_facts(prospectus_obs, announcement_obs, None, None)
    assert facts.offering_price.status == "conflicting"

    disclosures = (
        _disclosure("d-p", "approved_prospectus", datetime(2026, 1, 1)),
        _disclosure("d-a", "investor_sale_announcement", datetime(2026, 1, 2)),
    )
    inputs = CompanyDecisionInputs(
        spk_record_id=RECORD_ID,
        spk_record=None,
        application_record=None,
        facts=facts,
        disclosures=disclosures,
        financial_observations=(),
        company_name="Örnek Enerji A.Ş.",
    )
    snapshot = build_decision_snapshot(inputs, reference_date=datetime(2026, 1, 10))
    result = evaluate_decision(snapshot)

    assert result.signal == "insufficient_data"
    conflict_rule = next(rule for rule in result.hard_rules if rule.rule_id == "unresolved_critical_conflict")
    assert conflict_rule.triggered
    assert "offering_price" in conflict_rule.reason
    # Never selected or averaged: the offering_price feature must not
    # have contributed a score to any category.
    contributions = [c for c in result.feature_contributions if c.feature_id == "offering_price"]
    assert all(not c.included_in_score for c in contributions)


def test_missing_features_reduce_coverage_without_a_neutral_score():
    config = get_scoring_config("expert_v0")

    disclosures = (_disclosure("d-p", "approved_prospectus", datetime(2026, 1, 1)),)
    # Only distribution_method (weight 15) is available; the other 4
    # offering_structure features (weight 85) are missing entirely.
    facts = build_extracted_facts({"distribution_method": FieldObservation("sabit fiyatla talep toplama", "snip", SRC_P)}, None)
    inputs = CompanyDecisionInputs(
        spk_record_id=RECORD_ID,
        spk_record=None,
        application_record=None,
        facts=facts,
        disclosures=disclosures,
        financial_observations=(),
        company_name="Örnek Enerji A.Ş.",
    )
    snapshot = build_decision_snapshot(inputs, reference_date=datetime(2026, 1, 10))

    result = score_category("offering_structure", config.offering_structure, snapshot)

    assert result.coverage == 15 / 100
    assert result.status == "INSUFFICIENT"
    # The single available feature still scores on its own merits
    # (100 for a presence feature) — missing ones are excluded, never
    # averaged in as a 0 or any other neutral placeholder.
    assert result.score == 100.0
    included = {c.feature_id for c in result.contributions if c.included_in_score}
    assert included == {"distribution_method"}
