"""Period-aware deterministic extraction of financial-statement metrics
from price determination report ("Fiyat Tespit Raporu") PDF text.

Kept entirely separate from :mod:`halka_arz_advisor.kap.extraction` — a
:class:`FinancialObservation` is inherently *multi-valued per company*
(one company reports several comparable periods for the same metric in
the same table), which doesn't fit :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`'s
one-value-per-field shape. Adding year-specific scalar fields there
(``revenue_2023``, ``revenue_2024``, ...) would hardcode a fixed set of
periods that doesn't generalize across companies with different fiscal
calendars/report vintages, so this stays a flat, open-ended series
instead.

Patterns below are confirmed against the "Gelir Tablosu" (income
statement) summary table of two real, independently-brokered reports:

- METEN/İnfo Yatırım: a clean, single table headed "Gelir Tablosu"
  with columns "31.Ara 2023 2024 2025 2025/03 2026/03 Son 4Ç" (three
  full calendar years, two interim first-quarter cumulative periods,
  and a trailing-four-quarters column), each column explicitly marked
  "gerçekleşmiş" (realized, i.e. not a forecast), immediately followed
  by a "TL" scale marker and then labelled rows ("Net Satışlar",
  "Net Kar", ...).
- QUICK/Garanti Yatırım: a "Konsolide Olmayan Finansal Tablolar -Gelir
  Tablosu" table headed "mnTL 2023 2024 2025 2025/03" (note the
  "mnTL" — this report's figures are in *millions* of TL, not TL
  itself; scale is per-report and must never be assumed), with a
  "NET DONEM KARI VEYA ZARARI" row. QUICK, an insurance holding, has
  no comparable "Net Satışlar"/"Hasılat" line anywhere in its report
  (insurers report written premiums instead, a genuinely different
  metric) — revenue extraction correctly returns nothing for it,
  which is the honest outcome, not a bug.

Only the "Gelir Tablosu" table itself is read — never the separate
"Gelir Tablosu Projeksiyonu" (income statement *projection*) tables
that also appear in these reports, which mix real historical years
with the report's own forward-looking estimates ("2026T", "2027T", ...).
Mixing a forecast into a "financial observation" would misrepresent it
as an actual, reported result — the projection tables are deliberately
never matched by :data:`_GELIR_TABLOSU_HEADING`'s window search here
(bounded by the metric row itself, and Turkish forecast-year tokens
like "2026T" don't match this module's period-token pattern, see
:data:`_PERIOD_TOKEN_RE`).

A period whose label isn't a plain 4-digit year or a "YYYY/MM" interim
mark (e.g. METEN's own "Son 4Ç" trailing-four-quarters column) is
simply not recognized as a period at all and its value is dropped —
never guessed at a start/end date. Consolidated/standalone scope is
only ever set from an explicit "Konsolide"/"Konsolide Olmayan" marker
found on the *same page* as the table (QUICK's caption states this
directly; METEN's page has no such marker at all, so it's correctly
left unstated there) — never inferred from a mention elsewhere in the
report. Inflation-adjustment status is left unstated in this first
commit: neither sample report carries an unambiguous, table-adjacent
label for it (a same-page "ÖBDR" row marker was observed in METEN's
table but its meaning couldn't be confirmed from the (partially
garbled) extracted text, so it's not treated as reliable evidence).
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from .extraction import ExtractionMethod, SourceRef
from .pdf import PdfPage
from .text import fold_turkish

FinancialPeriodType = Literal["ANNUAL", "INTERIM", "UNKNOWN"]
ConsolidationScope = Literal["consolidated", "standalone"]

FINANCIAL_METRIC_NAMES: tuple[str, ...] = ("revenue", "net_income")


@dataclass(frozen=True, slots=True)
class FinancialObservation:
    """One metric's value for one explicitly labelled comparable period,
    as found in one specific document/page."""

    metric_name: str
    value: float
    currency: str
    scale: str  # e.g. "unit" | "million" — the *reported* scale, never normalized
    period_start: date | None
    period_end: date | None
    period_type: FinancialPeriodType
    consolidation_scope: ConsolidationScope | None  # None = not stated explicitly on this page
    inflation_adjusted: bool | None  # None = not stated explicitly on this page
    raw_snippet: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class FinancialSeries:
    """Every :class:`FinancialObservation` collected for one company,
    across every eligible document processed for it."""

    observations: tuple[FinancialObservation, ...]

    def for_metric(self, metric_name: str) -> tuple[FinancialObservation, ...]:
        return tuple(obs for obs in self.observations if obs.metric_name == metric_name)


# --------------------------------------------------------------------------
# "Gelir Tablosu" (income statement) table parsing
# --------------------------------------------------------------------------

_GELIR_TABLOSU_HEADING = "gelir tablosu"

# How far past the heading to look for the table's own period-header row
# — generous enough to cover every header row/currency marker observed
# in both real samples, bounded so it can't run past the heading into
# an unrelated later section.
_HEADER_WINDOW_CHARS = 250

# How far past a metric row's label to look for that row's own numbers
# — bounded so a metric with no real row on this page can't accidentally
# pick up numbers from a much later, unrelated table.
_ROW_VALUE_WINDOW_CHARS = 400

# A bare 4-digit year ("2023") or a "YYYY/MM" interim mark ("2025/03")
# — the only two period-label shapes observed in either real sample.
# The bare-year alternative's negative lookaheads reject a forecast-year
# token like "2026T" (a letter immediately follows) and reject the first
# 4 digits of a longer run (more digits immediately follow).
_PERIOD_TOKEN_RE = re.compile(r"(\d{4})/(\d{1,2})(?!\d)|(?<!\d)(\d{4})(?!\d)(?![a-zA-Z])")

_NUM = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?"  # Turkish-formatted number, matches kap.extraction's own _NUM

_METRIC_ROW_LABEL_PATTERNS: dict[str, tuple[str, ...]] = {
    # "Net Satışlar" (METEN) — QUICK, an insurance holding, has no
    # comparable line (insurers report written premiums, a different
    # metric never treated as equivalent here); correctly not_found.
    "revenue": (r"net\s+satislar",),
    # "Net Kar" (METEN) / "Net Donem Kari (Veya Zarari)" (QUICK) — tried
    # in this order, first match wins, same fallback convention used
    # throughout kap.extraction (e.g. extract_offering_price).
    "net_income": (r"net\s+kar\b", r"net\s+donem\s+kari"),
}


def _extract_period_tokens(window: str) -> list[tuple[FinancialPeriodType, date, date]]:
    """Every recognizable period token in ``window``, in the order found."""
    periods: list[tuple[FinancialPeriodType, date, date]] = []
    for match in _PERIOD_TOKEN_RE.finditer(window):
        if match.group(1) is not None:
            year, month = int(match.group(1)), int(match.group(2))
            if not 1 <= month <= 12:
                continue
            start = date(year, 1, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])
            periods.append(("INTERIM", start, end))
        else:
            year = int(match.group(3))
            periods.append(("ANNUAL", date(year, 1, 1), date(year, 12, 31)))
    return periods


def _detect_currency_and_scale(header_window: str) -> tuple[str, str] | None:
    """``"mnTL"`` -> millions of TRY; a bare ``"TL"`` -> unit TRY. Every
    period-header row observed in either real sample states one or the
    other — never guessed when neither is present."""
    if re.search(r"\bmntl\b", header_window):
        return "TRY", "million"
    if re.search(r"\btl\b", header_window):
        return "TRY", "unit"
    return None


def _detect_consolidation_scope(page_folded: str) -> ConsolidationScope | None:
    """Only set from an explicit "Konsolide"/"Konsolide Olmayan" marker
    on this same page — never inferred from a mention elsewhere in the
    report (see module docstring)."""
    if re.search(r"konsolide\s+olmayan", page_folded):
        return "standalone"
    if re.search(r"\bkonsolide\b", page_folded):
        return "consolidated"
    return None


def _parse_turkish_number(raw: str) -> float | None:
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class _RowValue:
    value: float
    period_type: FinancialPeriodType
    period_start: date
    period_end: date
    raw_snippet: str


def _extract_metric_row(
    folded: str, text: str, metric_name: str
) -> tuple[list[_RowValue], str, str, ConsolidationScope | None] | None:
    """``(row_values, currency, scale, consolidation_scope)`` for
    ``metric_name``'s "Gelir Tablosu" row on this page, or ``None`` if
    the table/row/scale marker isn't present here."""
    heading_idx = folded.find(_GELIR_TABLOSU_HEADING)
    if heading_idx == -1:
        return None

    header_window = folded[heading_idx : heading_idx + _HEADER_WINDOW_CHARS]
    periods = _extract_period_tokens(header_window)
    if not periods:
        return None

    currency_scale = _detect_currency_and_scale(header_window)
    if currency_scale is None:
        return None
    currency, scale = currency_scale

    label_match = None
    for pattern in _METRIC_ROW_LABEL_PATTERNS[metric_name]:
        label_match = re.search(pattern, folded[heading_idx:])
        if label_match:
            break
    if not label_match:
        return None

    label_end = heading_idx + label_match.end()
    row_window = folded[label_end : label_end + _ROW_VALUE_WINDOW_CHARS]
    number_matches = list(re.finditer(_NUM, row_window))
    if len(number_matches) < len(periods):
        return None

    row_values = []
    for (period_type, start, end), number_match in zip(periods, number_matches[: len(periods)]):
        raw = text[label_end + number_match.start() : label_end + number_match.end()]
        value = _parse_turkish_number(raw)
        if value is None:
            return None
        row_values.append(_RowValue(value, period_type, start, end, raw))

    consolidation_scope = _detect_consolidation_scope(folded)
    return row_values, currency, scale, consolidation_scope


def extract_financial_observations_from_pages(
    pages: Sequence[PdfPage],
    *,
    document_type: str,
    disclosure_id: str,
    attachment_url: str,
    extraction_method: ExtractionMethod = "digital",
) -> tuple[FinancialObservation, ...]:
    """Run every metric's "Gelir Tablosu" row extractor over ``pages``,
    keeping the *first* page each metric's table is found on (mirrors
    :func:`halka_arz_advisor.kap.extraction.extract_observations_from_pages`'s
    first-match-wins convention) — one :class:`FinancialObservation`
    per explicitly labelled period found for that metric."""
    observations: list[FinancialObservation] = []

    for metric_name in FINANCIAL_METRIC_NAMES:
        for page in pages:
            folded_page = fold_turkish(page.text)
            found = _extract_metric_row(folded_page, page.text, metric_name)
            if found is None:
                continue

            row_values, currency, scale, consolidation_scope = found
            source = SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method)
            for row_value in row_values:
                observations.append(
                    FinancialObservation(
                        metric_name=metric_name,
                        value=row_value.value,
                        currency=currency,
                        scale=scale,
                        period_start=row_value.period_start,
                        period_end=row_value.period_end,
                        period_type=row_value.period_type,
                        consolidation_scope=consolidation_scope,
                        inflation_adjusted=None,
                        raw_snippet=row_value.raw_snippet,
                        source=source,
                    )
                )
            break  # first page this metric's table was found on

    return tuple(observations)
