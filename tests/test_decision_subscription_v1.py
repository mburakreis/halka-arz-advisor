from datetime import date, datetime

from halka_arz_advisor.decision.subscription_v1 import (
    SubscriptionDecisionInputs,
    evaluate_subscription_decision,
)
from halka_arz_advisor.kap.derived_financials import DERIVED_FINANCIAL_FEATURE_NAMES, DerivedFinancialFeature, DerivedFinancialFeatures
from halka_arz_advisor.kap.manual_confirmation import complete_offering_terms
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.offering_terms import OFFERING_TERM_FIELD_NAMES, OfferingTerms, OfferingTermField

AS_OF = datetime(2026, 8, 10)


def _field(status="not_found", value=None, unit=None):
    return OfferingTermField(status=status, value=value, unit=unit, derived=False, observations=())


def _terms(**overrides) -> OfferingTerms:
    base = {name: _field() for name in OFFERING_TERM_FIELD_NAMES}
    base.update(overrides)
    return OfferingTerms(**base)


def _resolved_terms(**overrides) -> OfferingTerms:
    """A fully critical-evidence-resolved OfferingTerms: equal
    distribution, a substantial (40%) retail tranche, an open
    subscription window, and a consistent share equation — the
    baseline every test below starts from and tweaks."""
    base = dict(
        offer_price=_field("extracted", 12.5, "TRY"),
        subscription_start=_field("extracted", date(2026, 8, 1), "date"),
        subscription_end=_field("extracted", date(2026, 8, 20), "date"),
        distribution_method=_field("extracted", "sabit fiyatla talep toplama"),
        total_offered_shares=_field("extracted", 1_000_000.0, "shares"),
        new_issue_shares=_field("extracted", 1_000_000.0, "shares"),
        secondary_sale_shares=_field("not_found"),
        retail_distribution_rule=_field("extracted", "equal"),
        retail_allocation_percentage=_field("extracted", 40.0, "percent"),
        retail_offered_shares=_field("extracted", 400_000.0, "shares"),
    )
    base.update(overrides)
    return _terms(**base)


def _derived_feature(name: str, status="unavailable", value=None) -> DerivedFinancialFeature:
    return DerivedFinancialFeature(
        feature_name=name, status=status, value=value, unavailable_reason=None if status == "computed" else "no data",
        input_observation_ids=(), source_fact_ids=(), formula_version="1",
    )


def _derived(**overrides) -> DerivedFinancialFeatures:
    base = {name: _derived_feature(name) for name in DERIVED_FINANCIAL_FEATURE_NAMES}
    base.update(overrides)
    return DerivedFinancialFeatures(**base)


def _inputs(terms: OfferingTerms, *, derived=None, market=None, as_of=AS_OF, disclosures=(), confirmations=()) -> SubscriptionDecisionInputs:
    completed = complete_offering_terms(terms, confirmations)
    return SubscriptionDecisionInputs(
        offering_terms=terms, completed_terms=completed, derived_financials=derived,
        market_context=market, as_of=as_of, disclosures=disclosures,
    )


def test_missing_critical_field_forces_cannot_assess_not_pass():
    terms = _resolved_terms(offer_price=_field("not_found"))

    result = evaluate_subscription_decision(_inputs(terms))

    assert result.action == "CANNOT_ASSESS_SUBSCRIPTION"
    assert "offer_price" in result.missing_critical_evidence
    assert result.evidence_grade == "NONE"
    # Never treated as "we looked and it's bad".
    assert result.action != "PASS_SUBSCRIPTION"


def test_conflicting_critical_field_also_forces_cannot_assess():
    terms = _resolved_terms(distribution_method=_field("conflicting"))

    result = evaluate_subscription_decision(_inputs(terms))

    assert result.action == "CANNOT_ASSESS_SUBSCRIPTION"
    assert "distribution_method (conflicting)" in result.missing_critical_evidence


def test_listing_trade_action_with_ownership_not_assessable():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, derived=None))

    assert result.ownership_view == "NOT_ASSESSABLE"
    assert result.action == "SUBSCRIBE_FOR_LISTING_TRADE"
    assert result.intended_horizon == "listing_day_flip"


def test_subscribe_with_hold_option_when_ownership_is_a_hold_candidate():
    terms = _resolved_terms()
    derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 15.0),
        net_margin=_derived_feature("net_margin", "computed", 12.0),
        current_ratio=_derived_feature("current_ratio", "computed", 2.0),
    )

    result = evaluate_subscription_decision(_inputs(terms, derived=derived))

    assert result.ownership_view == "HOLD_CANDIDATE"
    assert result.action == "SUBSCRIBE_WITH_HOLD_OPTION"
    assert result.intended_horizon == "flip_or_hold"
    assert result.strongest_positive_evidence


def test_single_red_flag_ratio_forces_avoid_long_term_not_averaged_away():
    terms = _resolved_terms()
    # Every other ratio is healthy, but net_margin alone is negative —
    # non-compensable: this must still force AVOID_LONG_TERM.
    derived = _derived(
        net_margin=_derived_feature("net_margin", "computed", -5.0),
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 25.0),
        current_ratio=_derived_feature("current_ratio", "computed", 3.0),
        debt_to_equity=_derived_feature("debt_to_equity", "computed", 0.2),
        interest_coverage=_derived_feature("interest_coverage", "computed", 10.0),
    )

    result = evaluate_subscription_decision(_inputs(terms, derived=derived))

    assert result.ownership_view == "AVOID_LONG_TERM"
    # A pure listing-day flip still doesn't require good fundamentals.
    assert result.action == "SUBSCRIBE_FOR_LISTING_TRADE"


def test_official_conflict_not_overridden_by_manual_input():
    terms = _resolved_terms(offer_price=_field("conflicting", None, "TRY"))
    from halka_arz_advisor.kap.manual_confirmation import ManualFieldConfirmation

    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))

    result = evaluate_subscription_decision(_inputs(terms, confirmations=[confirmation]))

    # A conflicting offer_price is not resolved by the manual value —
    # the gate must still fail.
    assert result.action == "CANNOT_ASSESS_SUBSCRIPTION"
    assert "offer_price (conflicting)" in result.missing_critical_evidence


def test_manual_confirmation_unblocks_an_otherwise_cannot_assess_decision():
    terms = _resolved_terms(offer_price=_field("not_found"))
    from halka_arz_advisor.kap.manual_confirmation import ManualFieldConfirmation

    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))

    result = evaluate_subscription_decision(_inputs(terms, confirmations=[confirmation]))

    assert result.action != "CANNOT_ASSESS_SUBSCRIPTION"
    assert not result.missing_critical_evidence


def test_proportional_distribution_is_unfavorable_and_blocks_subscribe():
    terms = _resolved_terms(retail_distribution_rule=_field("extracted", "proportional"))

    result = evaluate_subscription_decision(_inputs(terms))

    assert result.subscription_edge == "UNFAVORABLE"
    assert result.action == "PASS_SUBSCRIPTION"


def test_strong_ownership_evidence_cannot_compensate_uneconomic_mechanics():
    # A structural red flag (inconsistent share equation) plus
    # excellent fundamentals: the red flag must still win.
    terms = _resolved_terms(new_issue_shares=_field("extracted", 100.0, "shares"))
    derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 40.0),
        net_margin=_derived_feature("net_margin", "computed", 20.0),
        current_ratio=_derived_feature("current_ratio", "computed", 3.0),
    )

    result = evaluate_subscription_decision(_inputs(terms, derived=derived))

    assert result.action == "PASS_SUBSCRIPTION"
    assert result.strongest_risks


def test_closed_window_with_watchworthy_ownership_reassesses_after_listing():
    terms = _resolved_terms(subscription_end=_field("extracted", date(2026, 8, 1), "date"))
    # Only one resolved ratio — too thin to qualify as HOLD_CANDIDATE
    # (needs >= 2 resolved and all positive), but still enough evidence
    # to be WATCH rather than NOT_ASSESSABLE.
    derived = _derived(net_margin=_derived_feature("net_margin", "computed", 5.0))

    result = evaluate_subscription_decision(_inputs(terms, derived=derived, as_of=AS_OF))

    assert result.ownership_view == "WATCH"
    assert result.action == "PASS_AND_REASSESS_AFTER_LISTING"
    assert result.intended_horizon == "watch_post_listing"


def test_withdrawal_disclosure_title_forces_pass_subscription():
    terms = _resolved_terms()
    disclosure = KapDisclosure(
        disclosure_id="d1", disclosure_index=1, published_at=datetime(2026, 8, 1),
        company_name="Örnek A.Ş.", ticker="ORNK", title="Halka Arzın İptali Hakkında",
        summary="", document_type="approved_prospectus", notification_url="https://example/1",
        attachment_urls=(), matched_spk_record_id="ipo:ORNK:2026", match_method="ticker", raw={},
    )

    result = evaluate_subscription_decision(_inputs(terms, disclosures=(disclosure,)))

    assert result.action == "PASS_SUBSCRIPTION"
    assert any("withdrawal" in r for r in result.strongest_risks)


def test_market_context_never_changes_the_action_or_ownership_view():
    from halka_arz_advisor.evds.models import MarketContextFeatureValue, MarketContextSnapshot

    terms = _resolved_terms()
    # A severely negative BIST regime, including policy_rate_minus_cpi —
    # must have zero effect on the decision.
    market = MarketContextSnapshot(
        features={
            "bist100_return_20d": MarketContextFeatureValue(-40.0, date(2026, 8, 8), ("TP.MK.F.BILESIK",)),
            "policy_rate_minus_cpi": MarketContextFeatureValue(-30.0, date(2026, 8, 8), ("x",)),
        }
    )

    without_market = evaluate_subscription_decision(_inputs(terms, market=None))
    with_market = evaluate_subscription_decision(_inputs(terms, market=market))

    assert without_market.action == with_market.action
    assert without_market.ownership_view == with_market.ownership_view
    assert without_market.subscription_edge == with_market.subscription_edge


def test_allocation_scenarios_are_populated_for_a_resolvable_decision():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms))

    assert len(result.allocation_scenarios) == 3
    assert all(s.status == "computed" for s in result.allocation_scenarios)
