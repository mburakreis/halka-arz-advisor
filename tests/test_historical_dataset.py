from datetime import UTC, date, datetime
from pathlib import Path

from halka_arz_advisor.evds.cache import EvdsCache
from halka_arz_advisor.evds.models import EvdsObservation
from halka_arz_advisor.historical_dataset import build_historical_snapshot, market_context_as_of
from halka_arz_advisor.ipo_outcomes.models import IpoMarketOutcome
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure

RECORD_ID = "ipo:TEST:2026 / 7"
CUTOFF = date(2026, 7, 10)  # subscription_end_date, as stated in the announcement below

SRC_ANNOUNCEMENT = SourceRef("investor_sale_announcement", "d-announcement", "url-a", 1)


def _announcement() -> KapDisclosure:
    facts = build_extracted_facts(
        None,
        {
            "subscription_start_date": FieldObservation(date(2026, 7, 8), "08.07.2026 - 10.07.2026", SRC_ANNOUNCEMENT),
            "subscription_end_date": FieldObservation(date(2026, 7, 10), "08.07.2026 - 10.07.2026", SRC_ANNOUNCEMENT),
        },
    )
    return KapDisclosure(
        disclosure_id="d-announcement", disclosure_index=1, published_at=datetime(2026, 7, 1),
        company_name="TEST A.Ş.", ticker="TEST", title="Tasarruf Sahiplerine Satış Duyurusu", summary="",
        document_type="investor_sale_announcement", notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(), matched_spk_record_id=RECORD_ID, match_method="ticker", raw={},
        pdf_status="ok", extracted_facts=facts,
    )


def _price_determination_report(*, disclosure_id: str, published_at: datetime, reported_pe: float) -> KapDisclosure:
    src = SourceRef("price_determination_report", disclosure_id, "url-pdr", 1)
    facts = build_extracted_facts(None, None, None, {"reported_pe": FieldObservation(reported_pe, f"F/K {reported_pe}", src)})
    return KapDisclosure(
        disclosure_id=disclosure_id, disclosure_index=2, published_at=published_at,
        company_name="TEST A.Ş.", ticker="TEST", title="Fiyat Tespit Raporu", summary="",
        document_type="price_determination_report", notification_url="https://www.kap.org.tr/tr/Bildirim/2",
        attachment_urls=(), matched_spk_record_id=RECORD_ID, match_method="ticker", raw={},
        pdf_status="ok", extracted_facts=facts,
    )


def _outcome(*, first_day_return: float) -> IpoMarketOutcome:
    return IpoMarketOutcome(
        ticker="TEST", company_name="TEST A.Ş.", offer_price=10.0,
        resolved_trading_start_date=date(2026, 7, 13), spk_trading_start_date=date(2026, 7, 13),
        kap_trading_start_announcement_dates=(), trading_start_conflict=False,
        price_observation_count=1, last_price_observation_date=date(2026, 7, 13),
        first_day_return=first_day_return, return_5d=None, return_20d=None, return_3m=None,
        max_drawdown_5d=None, max_drawdown_20d=None, max_drawdown_3m=None,
        bist_relative_first_day=None, bist_relative_5d=None, bist_relative_20d=None, bist_relative_3m=None,
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _build(tmp_path: Path, disclosures: tuple[KapDisclosure, ...], outcome: IpoMarketOutcome | None = None):
    return build_historical_snapshot(
        RECORD_ID, ticker="TEST", spk_record=None, application_record=None,
        disclosures=disclosures, evds_cache=EvdsCache(tmp_path / "evds"), outcome=outcome,
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


# --------------------------------------------------------------------------
# 1. A post-cutoff document cannot affect the snapshot
# --------------------------------------------------------------------------


def test_post_cutoff_document_is_excluded_from_features_and_decision(tmp_path):
    pre_cutoff_report = _price_determination_report(
        disclosure_id="d-pdr-before", published_at=datetime(2026, 7, 5), reported_pe=12.0
    )
    post_cutoff_report = _price_determination_report(
        disclosure_id="d-pdr-after", published_at=datetime(2026, 7, 15), reported_pe=99.0
    )

    without_leak = _build(tmp_path, (_announcement(), pre_cutoff_report))
    with_post_cutoff_doc = _build(tmp_path, (_announcement(), pre_cutoff_report, post_cutoff_report))

    assert without_leak.cutoff.status == "resolved"
    assert without_leak.cutoff.cutoff_date == CUTOFF == with_post_cutoff_doc.cutoff.cutoff_date

    # The post-cutoff document is recorded as excluded, never as considered.
    assert "d-pdr-after" not in with_post_cutoff_doc.considered_disclosure_ids
    assert "d-pdr-after" in with_post_cutoff_doc.excluded_post_cutoff_disclosure_ids

    # Its (very different) reported_pe value never reaches the feature audit.
    pe_result = next(r for r in with_post_cutoff_doc.audit_results if r.feature_id == "earnings_multiple_at_offer")
    assert pe_result.status == "AVAILABLE"
    assert pe_result.evidence[0].value == 12.0
    assert all(e.disclosure_id != "d-pdr-after" for r in with_post_cutoff_doc.audit_results for e in r.evidence)

    # Whether or not the post-cutoff document is even present upstream,
    # the reconstructed decision is byte-for-byte identical.
    assert without_leak.decision_result == with_post_cutoff_doc.decision_result
    assert without_leak.audit_results == with_post_cutoff_doc.audit_results


# --------------------------------------------------------------------------
# 2. Future market observations cannot leak into historical features
# --------------------------------------------------------------------------


def test_market_context_as_of_ignores_observations_after_cutoff(tmp_path):
    cache = EvdsCache(tmp_path / "evds")
    fetched_at = datetime(2026, 8, 1, tzinfo=UTC)
    past = [
        EvdsObservation("TP.MK.F.BILESIK", date(2026, 6, d), 10000.0 + d, "index_points", "daily", "Borsa İstanbul", fetched_at)
        for d in range(1, 10)
    ]
    cache.merge_and_save("bist100_index", past)

    before_future_data = market_context_as_of(cache, CUTOFF)

    # A wildly different future observation, added to the very same cache.
    future = [EvdsObservation("TP.MK.F.BILESIK", date(2026, 7, 20), 999999.0, "index_points", "daily", "Borsa İstanbul", fetched_at)]
    cache.merge_and_save("bist100_index", future)

    after_future_data_added = market_context_as_of(cache, CUTOFF)

    assert before_future_data == after_future_data_added
    for feature in after_future_data_added.features.values():
        assert feature.as_of_date <= CUTOFF


# --------------------------------------------------------------------------
# 3. Outcome labels remain separate from decision inputs
# --------------------------------------------------------------------------


def test_outcome_label_never_changes_the_reconstructed_decision(tmp_path):
    disclosures = (_announcement(), _price_determination_report(disclosure_id="d-pdr", published_at=datetime(2026, 7, 5), reported_pe=12.0))

    no_outcome = _build(tmp_path, disclosures, outcome=None)
    wildly_positive = _build(tmp_path, disclosures, outcome=_outcome(first_day_return=500.0))
    wildly_negative = _build(tmp_path, disclosures, outcome=_outcome(first_day_return=-90.0))

    assert no_outcome.decision_result == wildly_positive.decision_result == wildly_negative.decision_result
    assert no_outcome.audit_results == wildly_positive.audit_results == wildly_negative.audit_results
    assert no_outcome.cutoff == wildly_positive.cutoff == wildly_negative.cutoff
    assert no_outcome.considered_disclosure_ids == wildly_positive.considered_disclosure_ids == wildly_negative.considered_disclosure_ids

    # The label itself is still exactly what was attached — it just never
    # fed back into anything computed above it.
    assert no_outcome.outcome is None
    assert wildly_positive.outcome.first_day_return == 500.0
    assert wildly_negative.outcome.first_day_return == -90.0
