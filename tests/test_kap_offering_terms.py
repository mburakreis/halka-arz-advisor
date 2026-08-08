from datetime import datetime

from halka_arz_advisor.kap.allocation_scenario import build_allocation_scenario
from halka_arz_advisor.kap.extraction import (
    AllocationLineItem,
    DistributionRuleLineItem,
    FieldObservation,
    SourceRef,
    build_extracted_facts,
)
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.offering_terms import build_offering_terms

SRC_A = SourceRef("investor_sale_announcement", "d-a", "url-a", 1)
SRC_A2 = SourceRef("investor_sale_announcement", "d-a2", "url-a2", 1)
SRC_P = SourceRef("approved_prospectus", "d-p", "url-p", 8)


def _disclosure(disclosure_id: str, document_type: str, published_at: datetime) -> KapDisclosure:
    return KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=1,
        published_at=published_at,
        company_name="Örnek A.Ş.",
        ticker="ORNK",
        title="t",
        summary="",
        document_type=document_type,
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id="ipo:ORNK:2026",
        match_method="ticker",
        raw={},
    )


DISCLOSURES = [
    _disclosure("d-a", "investor_sale_announcement", datetime(2026, 8, 1)),
    _disclosure("d-a2", "investor_sale_announcement", datetime(2026, 8, 1)),
    _disclosure("d-p", "approved_prospectus", datetime(2026, 7, 20)),
]


def test_build_offering_terms_with_no_facts_returns_all_not_found():
    terms = build_offering_terms(None)
    assert terms.offer_price.status == "not_found"
    assert terms.gross_offer_size.status == "not_found"
    assert terms.investor_group_allocations.status == "not_found"


def test_offer_price_and_dates_are_direct_passthroughs_with_provenance():
    facts = build_extracted_facts(
        None,
        {
            "offering_price": FieldObservation(12.5, "12,50 TL fiyattan", SRC_A),
            "subscription_start_date": FieldObservation("2026-09-01", "01.09.2026", SRC_A),
            "subscription_end_date": FieldObservation("2026-09-03", "03.09.2026", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)

    assert terms.offer_price.status == "extracted"
    assert terms.offer_price.value == 12.5
    assert terms.offer_price.unit == "TRY"
    assert terms.offer_price.derived is False
    obs = terms.offer_price.observations[0]
    assert obs.source_disclosure_id == "d-a"
    assert obs.source_document_type == "investor_sale_announcement"
    assert obs.source_published_at == datetime(2026, 8, 1)


def test_ipo_results_sourced_observation_is_excluded_from_offering_terms():
    # ExtractedFacts is a shared model — kap_extraction's own
    # _SCALAR_EXTRACTORS is not scoped per document type, so an
    # ipo_results (post-offer) disclosure can populate the very same
    # total_offered_shares field slot a prospectus/announcement uses
    # (confirmed live against a real 2026 IPO-results notice). OfferingTerms
    # is pre-offer-safe by contract: a lone ipo_results observation with
    # no pre-offer counterpart must resolve not_found, never "extracted"
    # from a post-offer source.
    src_results = SourceRef("ipo_results", "d-results", "url-results", 1)
    facts = build_extracted_facts(
        None,
        None,
        {"total_offered_shares": FieldObservation(70_000_000.0, "70.000.000 TL", src_results)},
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.total_offered_shares.status == "not_found"
    assert terms.total_offered_shares.value is None


def test_ipo_results_sourced_observation_does_not_mask_a_real_prospectus_announcement_conflict():
    # The pre-offer prospectus/announcement disagreement must still be
    # reported conflicting, independent of whatever an ipo_results
    # observation says.
    src_results = SourceRef("ipo_results", "d-results", "url-results", 1)
    facts = build_extracted_facts(
        {"total_offered_shares": FieldObservation(49_000_000.0, "49M", SRC_P)},
        {"total_offered_shares": FieldObservation(21_000_000.0, "21M", SRC_A)},
        {"total_offered_shares": FieldObservation(70_000_000.0, "70M", src_results)},
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.total_offered_shares.status == "conflicting"
    assert {obs.source_document_type for obs in terms.total_offered_shares.observations} == {
        "approved_prospectus",
        "investor_sale_announcement",
    }


def test_offer_price_conflicting_stays_conflicting_never_arbitrated():
    facts = build_extracted_facts(
        {"offering_price": FieldObservation(45.0, "45,00", SRC_P)},
        {"offering_price": FieldObservation(50.0, "50,00", SRC_A)},
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.offer_price.status == "conflicting"
    assert terms.offer_price.value is None
    assert len(terms.offer_price.observations) == 2


def test_secondary_sale_prefers_direct_extraction_when_present():
    facts = build_extracted_facts(
        None,
        {
            "secondary_sale_shares": FieldObservation(20_000_000.0, "20.000.000 TL", SRC_A),
            "total_offered_shares": FieldObservation(50_000_000.0, "50.000.000 TL", SRC_A),
            "capital_increase_shares": FieldObservation(30_000_000.0, "30.000.000 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.secondary_sale_shares.status == "extracted"
    assert terms.secondary_sale_shares.value == 20_000_000.0
    assert terms.secondary_sale_shares.derived is False


def test_secondary_sale_derives_from_total_minus_new_issue_when_not_directly_found():
    # Verified live against EMPAE's real multi-seller announcement:
    # 9,000,000 = 38,000,000 - 29,000,000, matching the sum of its 8
    # individually named sellers exactly.
    facts = build_extracted_facts(
        None,
        {
            "total_offered_shares": FieldObservation(38_000_000.0, "toplam 38.000.000 TL", SRC_A),
            "capital_increase_shares": FieldObservation(29_000_000.0, "artırılacak 29.000.000 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.secondary_sale_shares.status == "extracted"
    assert terms.secondary_sale_shares.value == 9_000_000.0
    assert terms.secondary_sale_shares.derived is True


def test_secondary_sale_derivation_blocked_when_a_dependency_is_conflicting():
    facts = build_extracted_facts(
        {"total_offered_shares": FieldObservation(50_000_000.0, "50M", SRC_P)},
        {"total_offered_shares": FieldObservation(60_000_000.0, "60M", SRC_A)},
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.secondary_sale_shares.status == "conflicting"
    assert terms.secondary_sale_shares.value is None


def test_pre_and_post_offer_share_count_derived_from_capital_and_par_value():
    facts = build_extracted_facts(
        None,
        {
            "pre_offer_capital": FieldObservation(100_000_000.0, "100.000.000 TL", SRC_A),
            "post_offer_capital": FieldObservation(130_000_000.0, "130.000.000 TL", SRC_A),
            "par_value_per_share": FieldObservation(1.0, "nominal değeri 1 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.pre_offer_share_count.status == "extracted"
    assert terms.pre_offer_share_count.value == 100_000_000.0
    assert terms.pre_offer_share_count.derived is True
    assert terms.post_offer_share_count.value == 130_000_000.0


def test_share_count_not_found_when_par_value_missing_never_assumes_one():
    facts = build_extracted_facts(
        None,
        {
            "pre_offer_capital": FieldObservation(100_000_000.0, "100.000.000 TL", SRC_A),
            "post_offer_capital": FieldObservation(130_000_000.0, "130.000.000 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.pre_offer_share_count.status == "not_found"
    assert terms.pre_offer_share_count.value is None


def test_share_count_not_found_when_par_value_is_zero():
    facts = build_extracted_facts(
        None,
        {
            "pre_offer_capital": FieldObservation(100_000_000.0, "100.000.000 TL", SRC_A),
            "par_value_per_share": FieldObservation(0.0, "nominal değeri 0 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.pre_offer_share_count.status == "not_found"


def test_gross_offer_size_derived_from_price_times_total_offered_shares():
    facts = build_extracted_facts(
        None,
        {
            "offering_price": FieldObservation(12.5, "12,50 TL", SRC_A),
            "total_offered_shares": FieldObservation(50_000_000.0, "50.000.000 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.gross_offer_size.status == "extracted"
    assert terms.gross_offer_size.value == 625_000_000.0
    assert terms.gross_offer_size.derived is True
    assert terms.gross_offer_size.unit == "TRY"


def test_gross_offer_size_not_found_when_total_offered_shares_missing():
    facts = build_extracted_facts(
        None,
        {"offering_price": FieldObservation(12.5, "12,50 TL", SRC_A)},
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.gross_offer_size.status == "not_found"
    assert terms.gross_offer_size.value is None


def test_implied_post_money_market_cap_composes_price_and_post_offer_share_count():
    facts = build_extracted_facts(
        None,
        {
            "offering_price": FieldObservation(12.5, "12,50 TL", SRC_A),
            "post_offer_capital": FieldObservation(130_000_000.0, "130.000.000 TL", SRC_A),
            "par_value_per_share": FieldObservation(1.0, "nominal değeri 1 TL", SRC_A),
        },
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.implied_post_money_market_cap.status == "extracted"
    assert terms.implied_post_money_market_cap.value == 1_625_000_000.0


def test_investor_group_allocations_passthrough_and_retail_fields_derived():
    allocations = (
        AllocationLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", amount_try=20_800_000.0, percentage=40.0),
        AllocationLineItem(group="domestic_institutional", group_label_raw="Yurt İçi Kurumsal Yatırımcılara", amount_try=15_600_000.0, percentage=30.0),
    )
    facts = build_extracted_facts(
        {"investor_group_allocations": FieldObservation(allocations, "tahsisat oranları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)

    assert terms.investor_group_allocations.status == "extracted"
    assert terms.investor_group_allocations.value == allocations

    assert terms.retail_allocation_percentage.status == "extracted"
    assert terms.retail_allocation_percentage.value == 40.0
    assert terms.retail_offered_shares.status == "extracted"
    assert terms.retail_offered_shares.value == 20_800_000.0


def test_retail_fields_not_found_when_no_retail_group_line_present():
    allocations = (
        AllocationLineItem(group="domestic_institutional", group_label_raw="Yurt İçi Kurumsal Yatırımcılara", amount_try=15_600_000.0, percentage=30.0),
    )
    facts = build_extracted_facts(
        {"investor_group_allocations": FieldObservation(allocations, "tahsisat oranları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.retail_allocation_percentage.status == "not_found"
    assert terms.retail_offered_shares.status == "not_found"


def test_retail_fields_not_found_when_allocation_table_itself_not_found():
    facts = build_extracted_facts(None, None)
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.investor_group_allocations.status == "not_found"
    assert terms.retail_allocation_percentage.status == "not_found"
    assert terms.retail_offered_shares.status == "not_found"


def test_institutional_allocation_sums_domestic_and_foreign_lines():
    allocations = (
        AllocationLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", amount_try=20_800_000.0, percentage=40.0),
        AllocationLineItem(group="domestic_institutional", group_label_raw="Yurt İçi Kurumsal Yatırımcılara", amount_try=15_600_000.0, percentage=30.0),
        AllocationLineItem(group="foreign_institutional", group_label_raw="Yurt Dışı Kurumsal Yatırımcılara", amount_try=10_400_000.0, percentage=20.0),
    )
    facts = build_extracted_facts(
        {"investor_group_allocations": FieldObservation(allocations, "tahsisat oranları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)

    assert terms.institutional_allocation_percentage.status == "extracted"
    assert terms.institutional_allocation_percentage.value == 50.0
    assert terms.institutional_offered_shares.status == "extracted"
    assert terms.institutional_offered_shares.value == 26_000_000.0


def test_institutional_allocation_not_found_when_neither_institutional_group_present():
    allocations = (
        AllocationLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", amount_try=20_800_000.0, percentage=40.0),
    )
    facts = build_extracted_facts(
        {"investor_group_allocations": FieldObservation(allocations, "tahsisat oranları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.institutional_allocation_percentage.status == "not_found"
    assert terms.institutional_offered_shares.status == "not_found"


def test_institutional_allocation_partial_data_reports_not_found_rather_than_partial_sum():
    # Foreign-institutional line has no stated percentage — summing just
    # the domestic figure would silently understate the real total, so
    # the percentage side reports not_found while the share side (which
    # both lines do state) still resolves.
    allocations = (
        AllocationLineItem(group="domestic_institutional", group_label_raw="Yurt İçi Kurumsal Yatırımcılara", amount_try=15_600_000.0, percentage=30.0),
        AllocationLineItem(group="foreign_institutional", group_label_raw="Yurt Dışı Kurumsal Yatırımcılara", amount_try=10_400_000.0, percentage=None),
    )
    facts = build_extracted_facts(
        {"investor_group_allocations": FieldObservation(allocations, "tahsisat oranları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.institutional_allocation_percentage.status == "not_found"
    assert terms.institutional_offered_shares.status == "extracted"
    assert terms.institutional_offered_shares.value == 26_000_000.0


def test_retail_distribution_rule_passthrough_from_investor_group_distribution_rules():
    rules = (
        DistributionRuleLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", method="equal"),
        DistributionRuleLineItem(group="high_demand", group_label_raw="Yüksek Talepte Bulunacak Yatırımcı Grubuna", method="proportional"),
    )
    facts = build_extracted_facts(
        {"investor_group_distribution_rules": FieldObservation(rules, "Dağıtım Esasları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.investor_group_distribution_rules.status == "extracted"
    assert terms.retail_distribution_rule.status == "extracted"
    assert terms.retail_distribution_rule.value == "equal"


def test_retail_distribution_rule_not_found_when_retail_line_absent():
    rules = (DistributionRuleLineItem(group="high_demand", group_label_raw="Yüksek Talepte Bulunacak Yatırımcı Grubuna", method="proportional"),)
    facts = build_extracted_facts(
        {"investor_group_distribution_rules": FieldObservation(rules, "Dağıtım Esasları...", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.retail_distribution_rule.status == "not_found"


def test_distribution_regulation_reference_passthrough():
    facts = build_extracted_facts(
        {"distribution_regulation_reference": FieldObservation("II-5.2", "II-5.2 sayılı ... Tebliği", SRC_P)},
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    assert terms.distribution_regulation_reference.status == "extracted"
    assert terms.distribution_regulation_reference.value == "II-5.2"


# --------------------------------------------------------------------------
# AllocationScenario
# --------------------------------------------------------------------------


def _terms_with_equal_retail(retail_shares: float, offer_price: float | None = 45.0):
    observations = {
        "investor_group_allocations": FieldObservation(
            (AllocationLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", amount_try=retail_shares, percentage=40.0),),
            "tahsisat oranları...",
            SRC_P,
        ),
        "investor_group_distribution_rules": FieldObservation(
            (DistributionRuleLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", method="equal"),),
            "Dağıtım Esasları...",
            SRC_P,
        ),
    }
    if offer_price is not None:
        observations["offering_price"] = FieldObservation(offer_price, f"{offer_price} TL fiyattan", SRC_A)
    facts = build_extracted_facts(observations, None)
    return build_offering_terms(facts, DISCLOSURES)


def test_allocation_scenario_computes_lots_and_tl_for_equal_distribution():
    terms = _terms_with_equal_retail(retail_shares=1_000_000.0, offer_price=45.0)
    scenario = build_allocation_scenario(terms, hypothetical_retail_participant_count=100_000)

    assert scenario.status == "computed"
    assert scenario.lots_per_investor == 10.0
    assert scenario.tl_allocation_per_investor == 450.0
    assert scenario.assumptions  # non-empty: equal-distribution/lot-definition/order assumptions stated
    assert not scenario.caveats


def test_allocation_scenario_lots_computed_but_tl_blocked_without_offer_price():
    terms = _terms_with_equal_retail(retail_shares=1_000_000.0, offer_price=None)
    scenario = build_allocation_scenario(terms, hypothetical_retail_participant_count=100_000)

    assert scenario.status == "computed"
    assert scenario.lots_per_investor == 10.0
    assert scenario.tl_allocation_per_investor is None
    assert any("offer_price" in c for c in scenario.caveats)


def test_allocation_scenario_unavailable_when_distribution_rule_is_proportional():
    rules = (DistributionRuleLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", method="proportional"),)
    facts = build_extracted_facts(
        {
            "investor_group_allocations": FieldObservation(
                (AllocationLineItem(group="retail", group_label_raw="Yurt İçi Bireysel Yatırımcılara", amount_try=1_000_000.0, percentage=40.0),),
                "tahsisat oranları...",
                SRC_P,
            ),
            "investor_group_distribution_rules": FieldObservation(rules, "Dağıtım Esasları...", SRC_P),
        },
        None,
    )
    terms = build_offering_terms(facts, DISCLOSURES)
    scenario = build_allocation_scenario(terms, hypothetical_retail_participant_count=100_000)

    assert scenario.status == "unavailable"
    assert scenario.lots_per_investor is None
    assert any("proportional" in c for c in scenario.caveats)


def test_allocation_scenario_unavailable_when_distribution_rule_not_found():
    terms = build_offering_terms(build_extracted_facts(None, None), DISCLOSURES)
    scenario = build_allocation_scenario(terms, hypothetical_retail_participant_count=100_000)

    assert scenario.status == "unavailable"
    assert scenario.lots_per_investor is None
    assert scenario.caveats


def test_allocation_scenario_rejects_nonpositive_participant_count():
    terms = _terms_with_equal_retail(retail_shares=1_000_000.0)
    scenario = build_allocation_scenario(terms, hypothetical_retail_participant_count=0)
    assert scenario.status == "unavailable"
    assert scenario.caveats
