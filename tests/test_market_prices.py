from datetime import date

import pytest

from halka_arz_advisor.market_prices.cache import BulletinCache
from halka_arz_advisor.market_prices.client import bulletin_url, fetch_bulletin
from halka_arz_advisor.market_prices.config import MarketPricesConfig
from halka_arz_advisor.market_prices.exceptions import BulletinParseError, BulletinUnavailableError
from halka_arz_advisor.market_prices.parsing import parse_bulletin_csv
from halka_arz_advisor.market_prices.provider import get_observations
from halka_arz_advisor.probe.config import ProbeConfig

EN_HEADER = "TRADE DATE;INSTRUMENT SERIES CODE;INSTRUMENT NAME;INSTRUMENT GROUP;MARKET;OPENING PRICE;LOWEST PRICE;HIGHEST PRICE;CLOSING PRICE;TOTAL TRADED VALUE;TOTAL TRADED VOLUME"
TR_HEADER = "TARIH;ISLEM KODU;BULTEN ADI;ENSTRUMAN GRUBU;PAZAR;ACILIS FIYATI;EN DUSUK FIYAT;EN YUKSEK FIYAT;KAPANIS FIYATI;TOPLAM ISLEM HACMI;TOPLAM ISLEM ADEDI"


def _bulletin_csv(*data_rows: str) -> str:
    return "\n".join([TR_HEADER, EN_HEADER, *data_rows])


# --------------------------------------------------------------------------
# 1. Bulletin parsing
# --------------------------------------------------------------------------


def test_parse_bulletin_csv_selects_and_normalizes_equity_secondary_market_rows():
    csv_text = _bulletin_csv(
        # Ordinary secondary-market equity row (QUICK-shaped) — kept.
        "2026-08-06;QUICK.E;QUICK SIGORTA;EQT;MSPOT;76.6;76.55;84.25;80.35;4521693512.65;56338323",
        # Same-EQT-group but PMOSA (primary-market/book-building) row,
        # confirmed live on ALBTN 2026-07-23 — must be dropped, not
        # treated as a secondary-market observation.
        "2026-07-23;ALBTN.HE;ALBAYRAK BETON PRIMARY;EQT;PMOSA;38.6;38.6;38.6;38.6;2702000000;70000000",
        # A non-equity instrument group sharing the same file — dropped.
        "2026-08-06;GOLDX.F;GOLD ETF;ETF;MSPOT;10;9;11;10.5;1000;100",
    )

    rows = parse_bulletin_csv(csv_text)

    assert len(rows) == 1
    row = rows[0]
    assert row.ticker == "QUICK"
    assert row.trading_date == date(2026, 8, 6)
    assert row.open == 76.6
    assert row.low == 76.55
    assert row.high == 84.25
    assert row.close == 80.35
    # TOPLAM ISLEM HACMI / "TOTAL TRADED VALUE" is TRY value, and TOPLAM
    # ISLEM ADEDI / "TOTAL TRADED VOLUME" is share count — the reverse
    # of what the Turkish names alone would suggest.
    assert row.traded_value == 4521693512.65
    assert row.volume == 56338323


def test_parse_bulletin_csv_rejects_unexpected_secondary_market_ticker_shape():
    # An EQT/MSPOT row is expected to always carry the ".E" suffix; a
    # violation is a genuine schema surprise, not silently ignored.
    csv_text = _bulletin_csv("2026-08-06;QUICK;QUICK SIGORTA;EQT;MSPOT;76.6;76.55;84.25;80.35;100;10")
    with pytest.raises(BulletinParseError):
        parse_bulletin_csv(csv_text)


# --------------------------------------------------------------------------
# 2. Missing bulletin (weekend/holiday) handling
# --------------------------------------------------------------------------


def test_fetch_bulletin_raises_unavailable_on_404(httpx_mock):
    holiday = date(2026, 4, 23)
    config = MarketPricesConfig()
    httpx_mock.add_response(url=bulletin_url(holiday, config=config), status_code=404)

    with pytest.raises(BulletinUnavailableError):
        fetch_bulletin(holiday, config=config, probe_config=ProbeConfig(max_retries=0))


def test_get_observations_skips_weekends_without_any_request_and_caches_confirmed_unavailable_holiday(httpx_mock, tmp_path):
    config = MarketPricesConfig()
    # 2026-04-18/19 is a real Saturday/Sunday; 2026-04-20 (Monday) is
    # mocked as a confirmed-unavailable day (e.g. a public holiday) —
    # only that one weekday should ever hit the network.
    saturday, sunday, holiday_monday = date(2026, 4, 18), date(2026, 4, 19), date(2026, 4, 20)
    httpx_mock.add_response(url=bulletin_url(holiday_monday, config=config), status_code=404)

    cache = BulletinCache(tmp_path / "bulletins")
    result = get_observations(
        "QUICK", saturday, holiday_monday, cache=cache, config=config, probe_config=ProbeConfig(max_retries=0)
    )

    assert result == ()
    assert cache.get(saturday) is None and cache.is_confirmed_unavailable(saturday) is False
    assert cache.get(sunday) is None and cache.is_confirmed_unavailable(sunday) is False
    assert cache.is_confirmed_unavailable(holiday_monday) is True
    assert len(httpx_mock.get_requests()) == 1  # only the one weekday, never the weekends

    # A second call over the same range must not re-request the
    # already-confirmed-unavailable day.
    get_observations("QUICK", saturday, holiday_monday, cache=cache, config=config, probe_config=ProbeConfig(max_retries=0))
    assert len(httpx_mock.get_requests()) == 1
