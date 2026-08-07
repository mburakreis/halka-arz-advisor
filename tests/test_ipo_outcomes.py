from datetime import UTC, date, datetime

import pytest

from halka_arz_advisor.ipo_outcomes.calculations import (
    RETURN_WINDOWS,
    bist_relative_first_day,
    bist_relative_return,
    first_day_return,
    n_day_max_drawdown,
    n_day_return,
)
from halka_arz_advisor.ipo_outcomes.trading_start import resolve_trading_start_date
from halka_arz_advisor.evds.models import EvdsObservation
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.market_prices.models import DailyPriceObservation
from halka_arz_advisor.spk.models import SpkIpoRecord

FETCHED_AT = datetime(2026, 8, 7, tzinfo=UTC)


def _price(trading_date: date, *, open: float, close: float) -> DailyPriceObservation:
    return DailyPriceObservation(
        trading_date=trading_date, ticker="QUICK", open=open, high=max(open, close), low=min(open, close),
        close=close, volume=1000, traded_value=1000.0, source_url="https://example/thb.zip", fetched_at=FETCHED_AT,
    )


def _bist(observation_date: date, value: float) -> EvdsObservation:
    return EvdsObservation(
        series_code="TP.MK.F.BILESIK", observation_date=observation_date, value=value,
        unit="index_points", frequency="daily", source_institution="Borsa İstanbul", fetched_at=FETCHED_AT,
    )


def _ipo_record(ticker: str, trading_start: datetime | None) -> SpkIpoRecord:
    return SpkIpoRecord(
        ay=8, donem="2026 / 8", borsa_kodu=ticker, sirket_unvani=f"{ticker} A.S.", halka_arz_sekli=None,
        halka_arz_orani=None, halka_arz_fiyati_tl=None, ortak_satis_bin_tl=None, nakit_sermaye_artisi_bin_tl=None,
        ek_satis_tutari_bin_tl=None, satisa_hazir_bekletilen_pay_tutari_bin_tl=None,
        satisa_sunulan_toplam_tutar_bin_abd_dolari=None, satisa_sunulan_toplam_tutar_bin_tl=None,
        mevcut_sermaye_bin_tl=None, yeni_sermaye_bin_tl=None, satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl=None,
        ilk_islem_gordugu_pazar=None, halka_arza_aracilik_eden_kurum=None,
        borsada_islem_gorme_tarihi=trading_start, raw={},
    )


def _trading_start_disclosure(ticker: str, published_at: datetime) -> KapDisclosure:
    return KapDisclosure(
        disclosure_id="x", disclosure_index=1, published_at=published_at, company_name="BORSA İSTANBUL A.Ş.",
        ticker=ticker, title="Payların İşlem Görmeye Başlaması", summary="", document_type="trading_start",
        notification_url="https://kap.org.tr/tr/Bildirim/1", attachment_urls=(), matched_spk_record_id=None,
        match_method="ticker", raw={},
    )


# --------------------------------------------------------------------------
# 1. Trading-start date resolution
# --------------------------------------------------------------------------


def test_resolve_trading_start_date_uses_spk_date_and_keeps_kap_announcement_separately():
    # Confirmed live (see trading_start.py's module docstring): the KAP
    # "trading_start" disclosure's own publish date is a variable-lead
    # advance announcement, not the trading date — 2026-08-03 here is 3
    # days before the real 2026-08-06 listing, exactly the pattern
    # observed on the real QUICK/MASFN IPOs.
    ipo_record = _ipo_record("QUICK", datetime(2026, 8, 6))
    disclosures = [_trading_start_disclosure("QUICK", datetime(2026, 8, 3, 18, 10, tzinfo=UTC))]

    resolution = resolve_trading_start_date("QUICK", ipo_record, disclosures)

    assert resolution.resolved_date == date(2026, 8, 6)
    assert resolution.spk_trading_start_date == date(2026, 8, 6)
    assert resolution.kap_trading_start_announcement_dates == (date(2026, 8, 3),)
    assert resolution.conflict is False


def test_resolve_trading_start_date_is_none_without_an_spk_record():
    resolution = resolve_trading_start_date("NOPE", None, [])
    assert resolution.resolved_date is None
    assert resolution.spk_trading_start_date is None
    assert resolution.conflict is False


# --------------------------------------------------------------------------
# 2. Outcome math: trading-observation windows, not calendar days
# --------------------------------------------------------------------------


def test_returns_and_drawdowns_are_computed_from_trading_observations_not_calendar_gaps():
    # A real BIST IPO calendar shape: listing Friday, then trading
    # resumes Monday (a 3-calendar-day gap) — "5 trading days" must
    # still mean 5 observations, not 5 calendar days.
    prices = [
        _price(date(2026, 7, 17), open=77.0, close=77.0),  # day 0: listing, flat (limit-locked)
        _price(date(2026, 7, 20), open=84.7, close=84.7),  # day 1 (Monday, after the weekend gap)
        _price(date(2026, 7, 21), open=93.15, close=93.15),  # day 2
        _price(date(2026, 7, 22), open=102.4, close=90.0),  # day 3: intraday high then pulls back
        _price(date(2026, 7, 23), open=90.0, close=95.0),  # day 4
        _price(date(2026, 7, 24), open=95.0, close=123.8),  # day 5
    ]

    fd = first_day_return(prices)
    assert fd is not None
    assert fd.value == pytest.approx(0.0)

    n5 = n_day_return(prices, RETURN_WINDOWS["5d"])
    assert n5 is not None
    assert n5.as_of_date == date(2026, 7, 24)
    assert n5.value == pytest.approx((123.8 / 77.0 - 1.0) * 100.0)

    # Not enough observations yet for a 20-observation window.
    assert n_day_return(prices, RETURN_WINDOWS["20d"]) is None

    dd5 = n_day_max_drawdown(prices, RETURN_WINDOWS["5d"])
    assert dd5 is not None
    # Closing-price peak-to-trough only (mirrors evds.features'
    # bist100_max_drawdown, which is also close-only): day 2's close
    # 93.15 is the running peak -> day 3's close 90.0 is the trough,
    # even though day 3's own intraday high (102.4) was higher still.
    assert dd5.value == pytest.approx((90.0 / 93.15 - 1.0) * 100.0)

    bist_observations = [
        _bist(date(2026, 7, 16), 10000.0),  # previous trading day's close
        _bist(date(2026, 7, 17), 10100.0),
        _bist(date(2026, 7, 24), 10500.0),
    ]
    relative_first_day = bist_relative_first_day(fd, bist_observations, date(2026, 7, 17))
    assert relative_first_day is not None
    assert relative_first_day.value == pytest.approx(fd.value - ((10100.0 / 10000.0 - 1.0) * 100.0))

    relative_5d = bist_relative_return(n5, bist_observations, date(2026, 7, 17), n5.as_of_date)
    assert relative_5d is not None
    assert relative_5d.value == pytest.approx(n5.value - ((10500.0 / 10100.0 - 1.0) * 100.0))

    # A benchmark date this project hasn't already cached must never be
    # fetched or approximated — just None.
    assert bist_relative_return(n5, [], date(2026, 7, 17), n5.as_of_date) is None
