"""Parser for Borsa İstanbul's official Pay Piyasası Günlük Bülteni CSV.

Schema confirmed live on 2026-08-07 against real bulletin files
downloaded from ``{base_url}/{yyyy}/{mm}/thb{yyyymmdd}1.zip`` (see
:mod:`halka_arz_advisor.market_prices.config`) for six real trading
dates spanning two different years (2024-12-19, and five 2026 dates
including two confirmed IPO first-trading-days, QUICK on 2026-08-06 and
SARAE on 2026-07-17) — not guessed, not copied from a third-party
write-up.

Each ZIP contains exactly one ``;``-delimited CSV with a **bilingual
two-row header** (row 1: Turkish column names, row 2: the same columns
in English) followed by one data row per traded instrument, e.g.::

    TARIH;ISLEM KODU;...;ACILIS FIYATI;...;KAPANIS FIYATI;...
    TRADE DATE;INSTRUMENT SERIES CODE;...;OPENING PRICE;...;CLOSING PRICE;...
    2026-08-06;QUICK.E;...;76.6;...;80.35;...

This parser reads column positions from the **English** header row by
name (never a fixed index) — confirmed stable ASCII text, unlike the
Turkish row's diacritics — so a BIST column reordering or an added
column doesn't silently misalign this project's data; only a genuine
rename/removal of a column this project depends on raises
:class:`~halka_arz_advisor.market_prices.exceptions.BulletinParseError`.

One same-day file holds every traded instrument class (equities,
warrants, ETFs, precious-metal funds, ...) sharing the same 57-column
shape — confirmed live: the ordinary secondary equity market is always
``INSTRUMENT GROUP == "EQT"`` *and* ``MARKET == "MSPOT"``, and every row
matching both carries an ``.E`` ticker suffix (e.g. ``QUICK.E``) with
zero exceptions across every sampled date. All three are used here to
select and normalize equity rows; non-equity instrument groups
(``ECW``/``EPW`` warrants, ``ETF``, ``GMF``/``GSF`` precious-metal
funds, ``AOF``/``XCR``/``RGT``/``EMS``/``GMS``) are dropped, and so is
one further same-``EQT``-group case confirmed live on an actual IPO
(ALBTN, 2026-07-23): a single one-off ``MARKET == "PMOSA"`` row with an
``.HE`` ticker suffix and an instrument name suffixed "BİRİNCİL
PİYASA" ("primary market") — the IPO's own book-building/price-discovery
session, a different instrument from that ticker's ordinary ``.E``
secondary-market listing (which starts trading separately, sometimes
days later) and out of scope for a daily *secondary*-market OHLC
series.

``TOPLAM ISLEM HACMI`` / English ``TOTAL TRADED VALUE`` is the TRY value
traded, and ``TOPLAM ISLEM ADEDI`` / English ``TOTAL TRADED VOLUME`` is
the share count — the reverse of what the Turkish column names read as
literally ("HACIM" = volume, "ADET" = count) but exactly as BIST's own
English header names them; trusted over the Turkish-only literal
translation since it is BIST's own disambiguation of its own file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from .exceptions import BulletinParseError

_INSTRUMENT_GROUP_EQUITY = "EQT"
_MARKET_SECONDARY_EQUITY = "MSPOT"
_EQUITY_TICKER_SUFFIX = ".E"

# English header name -> attribute this project reads. Every one of
# these must be present (by exact name, any position) for a bulletin to
# be considered parseable; anything else in the 57-column file is
# ignored.
_REQUIRED_COLUMNS: dict[str, str] = {
    "TRADE DATE": "trade_date",
    "INSTRUMENT SERIES CODE": "instrument_series_code",
    "INSTRUMENT GROUP": "instrument_group",
    "MARKET": "market",
    "OPENING PRICE": "open",
    "LOWEST PRICE": "low",
    "HIGHEST PRICE": "high",
    "CLOSING PRICE": "close",
    "TOTAL TRADED VALUE": "traded_value",
    "TOTAL TRADED VOLUME": "volume",
}


@dataclass(frozen=True, slots=True)
class ParsedBulletinRow:
    """One equity instrument's row, normalized but not yet stamped with
    fetch provenance — see :func:`halka_arz_advisor.market_prices.client.fetch_bulletin`,
    which adds ``source_url``/``fetched_at`` to build the public
    :class:`~halka_arz_advisor.market_prices.models.DailyPriceObservation`."""

    trading_date: date
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    traded_value: float


def _column_index_map(header_row: list[str]) -> dict[str, int]:
    positions = {name: i for i, name in enumerate(header_row)}
    missing = [name for name in _REQUIRED_COLUMNS if name not in positions]
    if missing:
        raise BulletinParseError(
            f"bulletin CSV is missing required column(s) {missing} in its English header row: {header_row!r}"
        )
    return positions


def _parse_float(raw: str, *, column: str, row_index: int) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise BulletinParseError(f"row {row_index}: could not parse '{column}' as a number: {raw!r}") from exc


def parse_bulletin_csv(csv_text: str) -> tuple[ParsedBulletinRow, ...]:
    """Parse one day's full bulletin CSV text into its equity
    (``INSTRUMENT GROUP == "EQT"``) rows only.

    Raises :class:`~halka_arz_advisor.market_prices.exceptions.BulletinParseError`
    if the file has fewer than two header rows, the English header is
    missing a required column, an equity row's ticker doesn't carry the
    expected ``.E`` suffix, or a numeric field doesn't parse — never
    silently drops or zero-fills a malformed row.
    """
    reader = csv.reader(io.StringIO(csv_text), delimiter=";")
    rows = list(reader)
    if len(rows) < 2:
        raise BulletinParseError(f"expected a bilingual two-row header, got {len(rows)} row(s) total")

    english_header = rows[1]
    positions = _column_index_map(english_header)
    expected_width = len(english_header)

    results: list[ParsedBulletinRow] = []
    for row_index, row in enumerate(rows[2:], start=3):
        if not row:
            continue
        if len(row) != expected_width:
            raise BulletinParseError(
                f"row {row_index}: expected {expected_width} columns (matching the header), got {len(row)}"
            )

        instrument_group = row[positions["INSTRUMENT GROUP"]]
        market = row[positions["MARKET"]]
        if instrument_group != _INSTRUMENT_GROUP_EQUITY or market != _MARKET_SECONDARY_EQUITY:
            # Drops non-equity instrument groups (warrants, ETFs, ...)
            # and same-group-but-not-secondary-market rows — confirmed
            # live: a same-day one-off "PMOSA" (primary market /
            # book-building) row for a just-IPO'd ticker, e.g.
            # ALBTN.HE on 2026-07-23 — see this module's docstring.
            continue

        instrument_code = row[positions["INSTRUMENT SERIES CODE"]]
        if not instrument_code.endswith(_EQUITY_TICKER_SUFFIX):
            raise BulletinParseError(
                f"row {row_index}: secondary-market equity instrument code {instrument_code!r} does not end "
                f"with the expected {_EQUITY_TICKER_SUFFIX!r} suffix"
            )
        ticker = instrument_code[: -len(_EQUITY_TICKER_SUFFIX)]

        trade_date_raw = row[positions["TRADE DATE"]]
        try:
            trading_date = date.fromisoformat(trade_date_raw)
        except ValueError as exc:
            raise BulletinParseError(f"row {row_index}: could not parse TRADE DATE {trade_date_raw!r}") from exc

        volume_raw = row[positions["TOTAL TRADED VOLUME"]]
        try:
            volume = int(float(volume_raw))
        except ValueError as exc:
            raise BulletinParseError(f"row {row_index}: could not parse TOTAL TRADED VOLUME {volume_raw!r}") from exc

        results.append(
            ParsedBulletinRow(
                trading_date=trading_date,
                ticker=ticker,
                open=_parse_float(row[positions["OPENING PRICE"]], column="OPENING PRICE", row_index=row_index),
                high=_parse_float(row[positions["HIGHEST PRICE"]], column="HIGHEST PRICE", row_index=row_index),
                low=_parse_float(row[positions["LOWEST PRICE"]], column="LOWEST PRICE", row_index=row_index),
                close=_parse_float(row[positions["CLOSING PRICE"]], column="CLOSING PRICE", row_index=row_index),
                volume=volume,
                traded_value=_parse_float(
                    row[positions["TOTAL TRADED VALUE"]], column="TOTAL TRADED VALUE", row_index=row_index
                ),
            )
        )
    return tuple(results)
