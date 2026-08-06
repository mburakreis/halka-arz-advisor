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

Extended to also read the "Bilanço" (balance sheet) table — the same
heading+period-header+labelled-row shape as "Gelir Tablosu", confirmed
on the same two reports (METEN page 22, QUICK page 67-68):

- cash_and_equivalents ("Nakit ve Benzerleri" / "Nakit ve Nakit
  Benzeri(leri)" — both real, confirmed, values match on both reports).
- current_assets ("Dönen Varlıklar" / "Cari Varlıklar" — synonyms;
  QUICK's insurance-specific balance sheet uses "Cari Varlıklar" for
  the identical concept, not a different one — confirmed by wording in
  METEN's table; QUICK's own table has a *different*, unrelated OCR
  corruption on this specific label ("VARLIKLAR" rendered with a stray
  internal space, "V ARLIKLAR") that isn't matched, a genuine PDF-text
  quality gap rather than a design choice).
- current_liabilities ("Kısa Vad(eli) Yükümlülükler") — confirmed
  clean on QUICK; METEN's own table has a distinct OCR corruption on
  this one label ("Yükümlülükler" rendered "Y k ml l kler", missing
  every "ü" and space-fragmented) that isn't matched either — again a
  real PDF-quality gap on that specific page, not a regex design flaw.
- equity ("Ana Ortak(lığa) Özkaynak(ları)" / "Özsermaye" — synonyms;
  confirmed on both. **Not** the bare "Özkaynaklar" row METEN's table
  also has — cross-checked against that report's own narrative text
  ("Şirket'in özkaynakları ... 5,4 milyar TL") and independently
  against a second summary table elsewhere in the same report, both of
  which match "Ana Ortak. Özkaynakları", not the bare "Özkaynaklar"
  row (a different, smaller subtotal whose exact meaning couldn't be
  confirmed from the available text).
- financial_debt ("Finansal Borç(lar)") — the label wording is
  confirmed real (it appears in a single-period valuation snapshot
  elsewhere in METEN's own report), but neither sample's *multi-period*
  Bilanço table states a single combined total for it (only fragmented
  short-term/long-term sub-components, and summing sub-components
  would be calculating a value rather than extracting one) — this
  extractor is validated against the table-parsing machinery itself,
  not a live match in either sample; it activates for a report that
  does state this total directly.

Also reads operating_profit ("EBIT") and finance_expense ("Fin. Gid.")
from the "Gelir Tablosu" table (both confirmed on METEN; QUICK's
insurance-specific income statement has no comparable "EBIT" line at
all — see :mod:`halka_arz_advisor.kap.sector`), and
operating_cash_flow from a "Nakit Akış Tablosu" (statement of cash
flows) — neither sample report includes one at all (a DCF-based price
determination report projects *future* cash flows rather than
restating historical ones), so this extractor is, like
financial_debt, validated by the same table-parsing machinery and
label wording rather than a live match.

Row values may now be negative (parenthesized, e.g. a quarter's
"EBIT" was a real operating loss in one sample: "(41.387.392)") — see
:data:`_SIGNED_NUM`/:func:`_parse_signed_turkish_number`. Its lookbehind
also guards against a real, confirmed OCR artifact in QUICK's more
heavily garbled PDF text: a Turkish "ı" is sometimes misread as the
digit "1" *inside* a word (e.g. "Varlıklar" -> "Varl1klar"), which
would otherwise be picked up as a spurious stray one-digit "value"
immediately before the row's real numbers.
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

FINANCIAL_METRIC_NAMES: tuple[str, ...] = (
    "revenue",
    "net_income",
    "cash_and_equivalents",
    "financial_debt",
    "equity",
    "current_assets",
    "current_liabilities",
    "operating_profit",
    "operating_cash_flow",
    "finance_expense",
)


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
# "Gelir Tablosu" (income statement) / "Bilanço" (balance sheet) /
# "Nakit Akış Tablosu" (cash flow statement) table parsing — all three
# share the same heading+period-header+labelled-row shape.
# --------------------------------------------------------------------------

_GELIR_TABLOSU_HEADING = "gelir tablosu"
_BILANCO_HEADING = "bilanco"
_NAKIT_AKIS_HEADING = "nakit akis tablosu"

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

# Like _NUM, but also accepts a parenthesized or minus-signed value as
# negative (e.g. "(41.387.392)", a real operating loss observed in one
# sample's "EBIT" row) — never assumed for revenue/net_income, which
# have only ever been observed stated as plain positive magnitudes, but
# needed for operating_profit/finance_expense and any other row that
# can legitimately go negative. The leading negative lookbehind guards
# against a confirmed OCR artifact: a Turkish "ı" misread as digit "1"
# *inside* a word (e.g. "Varlıklar" -> "Varl1klar") would otherwise be
# picked up as a spurious stray value immediately preceding a row's
# real numbers — requiring the match not be preceded by a letter rules
# that out.
_SIGNED_NUM = r"(?<![a-zA-Z])\(?-?\d{1,3}(?:\.\d{3})*(?:,\d+)?\)?"

# Which table each metric is read from.
_METRIC_HEADINGS: dict[str, str] = {
    "revenue": _GELIR_TABLOSU_HEADING,
    "net_income": _GELIR_TABLOSU_HEADING,
    "operating_profit": _GELIR_TABLOSU_HEADING,
    "finance_expense": _GELIR_TABLOSU_HEADING,
    "cash_and_equivalents": _BILANCO_HEADING,
    "financial_debt": _BILANCO_HEADING,
    "equity": _BILANCO_HEADING,
    "current_assets": _BILANCO_HEADING,
    "current_liabilities": _BILANCO_HEADING,
    "operating_cash_flow": _NAKIT_AKIS_HEADING,
}

_METRIC_ROW_LABEL_PATTERNS: dict[str, tuple[str, ...]] = {
    # "Net Satışlar" (METEN) — QUICK, an insurance holding, has no
    # comparable line (insurers report written premiums, a different
    # metric never treated as equivalent here); correctly not_found.
    "revenue": (r"net\s+satislar",),
    # "Net Kar" (METEN) / "Net Donem Kari (Veya Zarari)" (QUICK) — tried
    # in this order, first match wins, same fallback convention used
    # throughout kap.extraction (e.g. extract_offering_price).
    "net_income": (r"net\s+kar\b", r"net\s+donem\s+kari"),
    # "Nakit ve Benzerleri" (METEN) / "Nakit ve Nakit Benzeri(leri)
    # Varlıkları" (QUICK) — both confirmed real.
    "cash_and_equivalents": (r"nakit\s+ve\s+(?:nakit\s+)?benzer\w*",),
    # See module docstring — label wording confirmed real elsewhere in
    # a sample report, but not as a multi-period Bilanço total.
    "financial_debt": (r"\bfinansal\s+borc\w*\b",),
    # "Ana Ortak. Özkaynakları" (METEN, confirmed — see module
    # docstring for why *not* the bare "Özkaynaklar" row) / "Özsermaye"
    # (QUICK, confirmed) — synonyms for the same concept, tried in
    # this order.
    "equity": (r"ana\s+ortak\w*\.?\s+ozkaynak\w*", r"\bozsermaye\b"),
    # "Dönen Varlıklar" (METEN) / "Cari Varlıklar" (QUICK) — synonyms
    # for the same concept (both real, confirmed), not two different
    # ones.
    "current_assets": (r"(?:donen|cari)\s+varlik\w*",),
    # "Kısa Vad(eli) Yükümlülükler" — identical wording confirmed on
    # both reports.
    "current_liabilities": (r"kisa\s+vad\w*\.?\s+yukumluluk\w*",),
    # "EBIT" (METEN, confirmed) — the trailing word boundary excludes
    # "EBITDA" (a different, earlier row in the same table) by
    # construction, since "t" and "d" are both word characters with no
    # boundary between them.
    "operating_profit": (r"\bebit\b",),
    # Standard Turkish CFS terminology — see module docstring; not a
    # live match in either sample.
    "operating_cash_flow": (r"isletme\s+faaliyetlerinden\s+(?:elde\s+edilen\s+)?nakit\w*",),
    # "Fin. Gid." (METEN, confirmed literal label; see module docstring
    # for the residual interpretive uncertainty around surrounding
    # context in that specific sample).
    "finance_expense": (r"\bfin\.?\s+gid\w*\b",),
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


def _parse_signed_turkish_number(raw: str) -> float | None:
    """Like :func:`_parse_turkish_number`, but ``"(41.387.392)"`` or
    ``"-41.387.392"`` -> ``-41387392.0``."""
    cleaned = raw.strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    value = _parse_turkish_number(cleaned)
    if value is None:
        return None
    return -value if negative else value


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
    ``metric_name``'s row in its table (see :data:`_METRIC_HEADINGS`)
    on this page, or ``None`` if the table/row/scale marker isn't
    present here."""
    heading = _METRIC_HEADINGS[metric_name]
    heading_idx = folded.find(heading)
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
    number_matches = list(re.finditer(_SIGNED_NUM, row_window))
    if len(number_matches) < len(periods):
        return None

    row_values = []
    for (period_type, start, end), number_match in zip(periods, number_matches[: len(periods)]):
        raw = text[label_end + number_match.start() : label_end + number_match.end()]
        value = _parse_signed_turkish_number(raw)
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
    """Run every metric's table-row extractor (see :data:`_METRIC_HEADINGS`)
    over ``pages``, keeping the *first* page each metric's table is found
    on (mirrors
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
