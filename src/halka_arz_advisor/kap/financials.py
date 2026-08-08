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
statement) / "Bilanço" (balance sheet) / "Nakit Akış Tablosu" (cash
flow statement) summary tables of a dozen real, independently-brokered
reports (varied companies, brokers, and table layouts), not just the
original two samples — see the table-region-detection section below for
how a real table is now told apart from a narrative/table-of-contents
mention of the same heading word, which was this module's original,
now-fixed, core bug.

Row-label wording confirmed real across multiple *different* companies'
reports (never company-specific — every pattern here is either the
label's own most common spelling or a confirmed synonym, tried in a
fixed, documented order so the more specific/preferred wording always
wins when more than one could apply):

- revenue: "Net Satışlar" or bare "Hasılat" (both real, interchangeable
  conventions — "Hasılat" is IFRS-style "revenue", "Net Satışlar" is the
  older/more literal "net sales" phrasing; a report uses one or the
  other, never both ambiguously in the same table). An insurer (QUICK)
  has neither line at all (insurers report written premiums instead, a
  genuinely different metric) — revenue extraction correctly returns
  nothing for it, the honest outcome, not a bug.
- net_income: "Net Kar", "Net Dönem Karı (Veya Zararı)", or bare "Dönem
  Kârı / Zararı" (all three real — the third is the standard SPK/KGK
  reporting-format label for "profit/loss for the period" used by
  reports that don't prefix it with "Net", confirmed to appear exactly
  once, as the actual bottom line, in every table it was found in).
- equity: "Ana Ortak(lığa) Özkaynak(ları)" / "Özsermaye" (synonyms,
  tried first — see below for why), or bare "Özkaynaklar" as a last-
  resort fallback, confirmed real as the genuine grand total in a
  report that has no separate parent-attributable line at all. **Not**
  tried first because one real report (METEN) also has a bare
  "Özkaynaklar" row that is a *different*, smaller subtotal (cross-
  checked against that report's own narrative text and a second summary
  table) — "Ana Ortak. Özkaynakları"/"Özsermaye" is tried first
  specifically so that report's real, more specific line wins instead.
- current_assets: "Dönen Varlıklar" / "Cari Varlıklar" — synonyms (an
  insurance-specific balance sheet uses "Cari Varlıklar" for the
  identical concept, not a different one).
- current_liabilities: "Kısa Vad(eli) Yükümlülükler" — confirmed
  identical wording across every real report it was found in.
- cash_and_equivalents: "Nakit ve (Nakit) Benzer(i/leri)" — confirmed
  identical concept, minor wording variants, across every real report.
- financial_debt: label wording ("Finansal Borç(lar)") is confirmed
  real, but no real report seen so far states a single *combined*
  total for it in its multi-period Bilanço table (only fragmented
  short-term/long-term sub-components) — summing sub-components would
  be calculating a value rather than extracting one, so this stays an
  honest gap until a report that states the total directly is seen.
- operating_profit: "EBIT" (a literal abbreviation, one real report),
  or "Esas Faaliyet Kârı / Zararı" (a second real report's own core-
  operating-profit line — not the broader "Finansman Geliri (Gideri)
  Öncesi Faaliyet Kârı" subtotal some reports *also* carry a few rows
  later, which is closer to a textbook EBIT but was judged too easy to
  mismatch against a similarly-worded intermediate subtotal; left as an
  honest gap rather than risk a wrong value, the same trade-off this
  project already made for `earnings_multiple_at_offer`).
- operating_cash_flow: "İşletme Faaliyetlerinden (Elde Edilen) Nakit"
  (the standard first line of a cash flow statement), or bare "Esas
  Faaliyet" (a second real report's synonym for the same first line —
  confirmed real even though that report's own PDF text extraction
  interleaves the row's numbers *inside* the label, "Esas
  faaliyetlerden <numbers> kaynaklanan net nakit", which is exactly why
  this pattern doesn't require "nakit" immediately after "faaliyet").
- finance_expense: "Fin. Gid." (a literal abbreviated label, one real
  report) or "Finansman Gider(leri)" (a second real report's unabridged
  form — deliberately distinct from "Finansman Gelirleri", finance
  *income*, a separate line in the same table).

A period whose label isn't a plain 4-digit year or a "YYYY/MM" interim
mark (e.g. a trailing-four-quarters column) is simply not recognized as
a period at all and its value is dropped — never guessed at a
start/end date. Consolidated/standalone scope is only ever set from an
explicit "Konsolide"/"Konsolide Olmayan" marker found on the same page
as the table — never inferred from a mention elsewhere in the report.
Inflation-adjustment status is left unstated (``None``) — no cached
report carries an unambiguous, table-adjacent label for it yet.

Row values may be negative (parenthesized, e.g. "(41.387.392)") — see
:data:`_SIGNED_NUM`/:func:`_parse_signed_turkish_number`. Its lookbehind
also guards against a confirmed real OCR artifact: a Turkish "ı"
sometimes misread as the digit "1" *inside* a word (e.g. "Varlıklar" ->
"Varl1klar"), which would otherwise be picked up as a spurious stray
one-digit "value" immediately before a row's real numbers.

--------------------------------------------------------------------------
Table-region detection (the core fix)
--------------------------------------------------------------------------

The original version of this module located a table by finding the
*first* occurrence of its heading word anywhere on a page and assuming
a period-header/currency marker followed shortly after. Confirmed
live, across a dozen real cached reports, that this systematically
picks the wrong occurrence: heading words appear repeatedly as table-
of-contents entries, running page headers on table-continuation pages,
and plain narrative mentions ("bilanço ve gelir tablosu hesap
planları..."), almost always *before* the real table in document order.
One real report also turned out to extract its "ç" glyph three
different, inconsistent ways within the same document ("Bilanço",
"Bilan9o", "Bilan~o") — a font-encoding artifact, not a design choice —
so even an exact literal match of the correctly-spelled heading missed
the real table entirely on that report.

Fixed generically, not per-report:

1. Every occurrence of a heading is considered (:func:`re.finditer`,
   not the first `.find()`), on every page, in document order — not
   just the first one. :data:`_BILANCO_HEADING_RE` also tolerates the
   three confirmed real "ç" encodings.
2. An occurrence only becomes a candidate table region if it's followed
   within a short window by a *tight cluster* of at least two period
   tokens (:func:`_find_period_cluster` — consecutive tokens no more
   than :data:`_MAX_PERIOD_TOKEN_GAP` characters apart, i.e. a genuine
   "31.12.2023 31.12.2024 31.12.2025 ..." column-header row) *and* a
   currency/scale marker near that same cluster. A single incidental
   year mention in a sentence, or a table-of-contents page-number, does
   not form a tight cluster and is correctly rejected — this is what
   actually fixes the original bug, not merely trying more occurrences.
3. A candidate whose header window contains a forecast-year token
   (``"2026T"`` etc., :data:`_FORECAST_TOKEN_RE`) is rejected outright —
   this is a projection table ("... Projeksiyonu"), confirmed real to
   sometimes restate the same historical years alongside forecast ones
   under a caption whose own spelling can't be relied on (also
   confirmed corrupted, e.g. "Gelir Tablosu Pro· cksivonu", in the same
   report that has the "ç" encoding issue above) — the forecast-token
   check is a content-structure signal, not a caption-spelling one, so
   it isn't defeated by that corruption either.
4. Once a candidate validates, the row-label search for a given metric
   starts right after its local period header — using *that
   occurrence's own* period list, never one borrowed from a different
   occurrence or page. Confirmed necessary: one real report restates
   the same table's caption+header on every continuation page, but with
   a *different number of periods* on different pages (some columns
   only exist on later pages) — reusing an earlier page's period count
   against a later page's row would either fail safely (too few
   numbers) or, worse, zip the wrong period label onto the wrong
   number.
5. If the row isn't found on the same page as its header, a bounded
   peek (:data:`_CONTINUATION_PEEK_CHARS`) into the very start of the
   *next* page is tried, still using the original page's header —
   confirmed necessary: a second real report's Bilanço table runs a
   labelled row straight across a page break with no repeated caption
   at all. The peek is disabled if the next page's own early text
   re-announces the same heading (a real continuation never does; if it
   does, the original table already ended at the page break and the
   peek must not run into a *different* table instead).
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from .extraction import ExtractionMethod, SourceRef, SourceSystem
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
    scale: str  # e.g. "unit" | "million" | "thousand" — the *reported* scale, never normalized
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

_GELIR_TABLOSU_HEADING_RE = re.compile(r"gelir\s+tablosu")
# Tolerates three confirmed real extractions of "ç" on the same document
# (see module docstring): a clean "c", the digit "9", and "~" — all
# observed for the *same* glyph in the *same* PDF's text layer.
_BILANCO_HEADING_RE = re.compile(r"bilan[c9~]o")
_NAKIT_AKIS_HEADING_RE = re.compile(r"nakit\s+akis\s+tablosu")

_METRIC_HEADING_RE: dict[str, re.Pattern[str]] = {
    "revenue": _GELIR_TABLOSU_HEADING_RE,
    "net_income": _GELIR_TABLOSU_HEADING_RE,
    "operating_profit": _GELIR_TABLOSU_HEADING_RE,
    "finance_expense": _GELIR_TABLOSU_HEADING_RE,
    "cash_and_equivalents": _BILANCO_HEADING_RE,
    "financial_debt": _BILANCO_HEADING_RE,
    "equity": _BILANCO_HEADING_RE,
    "current_assets": _BILANCO_HEADING_RE,
    "current_liabilities": _BILANCO_HEADING_RE,
    "operating_cash_flow": _NAKIT_AKIS_HEADING_RE,
}

# How far past a heading occurrence to look for its own period-header
# row — generous enough to cover an interposed phrase like "(TL) Özel
# Bağımsız Denetimden Geçmiş" observed between a scale marker and the
# actual dates in several real reports, bounded so it can't run past
# the heading into an unrelated later section.
_HEADER_WINDOW_CHARS = 300

# How far past a tight period-token cluster to look for a currency/scale
# marker — covers both "(TL) <phrase> <dates>" (marker before) and
# "<dates> TL <realized-status words>" (marker after) real layouts.
_CURRENCY_SEARCH_MARGIN = 80

# The real column-header "row" for a genuine table is always a *tight*
# run of period tokens (each only a few characters from the next) — a
# single incidental year mention in a sentence is not. This is what
# actually distinguishes a real table from a table-of-contents entry or
# a narrative mention of the same heading word (see module docstring).
# Confirmed real and *also* necessary to distinguish from a genuine
# table: several real reports' table captions are immediately preceded
# by a narrative sentence that itself lists the same periods ("Şirket'in
# 31.12.2022, 31.12.2023, ... tabloları aşağıdaki gibidir:") — just as
# tight a cluster as the real header row a few dozen characters later,
# but with no currency marker nearby (see _validate_table_header, which
# tries every cluster in the window, not just the first).
_MIN_PERIOD_TOKENS = 2
_MAX_PERIOD_TOKEN_GAP = 35
# A real table observed in any cached report has at most ~6 genuine
# (non-forecast) comparable-period columns. A cluster larger than this
# is a structural anomaly, not a bigger legitimate table — confirmed
# real: one report's cash-flow-statement header states each column's
# period as a *pair* of DD.MM.YYYY tokens on two separate lines (a
# start-dates row, then an end-dates row) rather than one token per
# column; naively merging both lines into one 10-token cluster and
# zipping it against row numbers would silently pull the *next* row's
# numbers in as if they were extra periods of the first row (verified
# against that report's real text before adding this guard). Rejecting
# an over-large cluster outright — rather than guessing which half of
# it is real — is the same "never mix" precedent as everywhere else in
# this module; that report's cash flow statement is an honest gap here,
# not a silently wrong one.
_MAX_CLUSTER_TOKENS = 8

# How far past a metric row's label to look for that row's own numbers
# — bounded so a metric with no real row on this page can't accidentally
# pick up numbers from a much later, unrelated table.
_ROW_VALUE_WINDOW_CHARS = 400

# How far into the *next* page to look for a row whose table's header
# was only stated once, on the previous page, with no repeated caption
# on the continuation page (see module docstring, point 5) — bounded so
# this can't run into an unrelated section several pages later. Sized
# generously (a full page is rarely much longer than this) now that
# _gap_is_plausible_row_continuation independently guards against
# matching a narrative mention anywhere within the peek, rather than
# relying on a short peek to avoid that risk.
_CONTINUATION_PEEK_CHARS = 1500

# Three period-label shapes observed across real samples, tried in this
# order (a regex alternation tries earlier branches first at each
# starting position, so a full "DD.MM.YYYY" is always matched whole
# before its trailing "YYYY" could be mistaken for a bare year on its
# own):
#  1. "DD.MM.YYYY" — an explicit full date (e.g. "31.03.2026"). Its real
#     end date and ANNUAL/INTERIM status are derived from the actual
#     day/month given (ANNUAL only for "31.12", confirmed the only
#     real fiscal-year-end date seen) — never defaulted to Dec 31 for a
#     column that's actually a Q1/Q3 interim mark stated this way (a
#     real, confirmed metadata-accuracy bug this shape fixes: a bare-
#     year fallback previously mislabelled e.g. "30.09.2025" as
#     "ANNUAL, ending 2025-12-31" instead of "INTERIM, ending
#     2025-09-30").
#  2. "YYYY/MM" — an interim mark (e.g. "2025/03").
#  3. a bare 4-digit year ("2023") — an annual mark. Negative lookaheads
#     reject a forecast-year token like "2026T" (a letter immediately
#     follows) and reject the first 4 digits of a longer run (more
#     digits immediately follow).
_PERIOD_TOKEN_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.(\d{4})"
    r"|(\d{4})/(\d{1,2})(?!\d)"
    r"|(?<!\d)(\d{4})(?!\d)(?![a-zA-Z])"
)

# A forecast-year column marker ("2026T", "2027T", ...) — real reports
# use a trailing "T" (tahmini/forecast) suffix on a projection table's
# own year columns, confirmed real and confirmed to sometimes coexist
# with genuine historical years in the same header row (see module
# docstring, point 3). Matched against already-folded (lowercased) text.
_FORECAST_TOKEN_RE = re.compile(r"(?<!\d)\d{4}t\b")

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

# A short, purely structural "connector" between a row label and its
# first number — whitespace/light punctuation (including a bare "*",
# a confirmed real footnote marker on several row labels, e.g. "...
# Varlıklar(*)"), optionally followed by
# the one confirmed-real label-continuation phrase ("... Kârı **veya
# Zararı**", "... Kârı **/ Zararı**" — "profit *or* loss", a standard
# suffix on several real Turkish P&L subtotal lines, tried with a
# trailing ``\w*`` since one real report's text extraction renders the
# Turkish dotless "ı" in "Zararı" as a literal "l" rather than folding
# to "i" like everywhere else — a second, independent glyph-corruption
# artifact from the same document as the "ç" one in the module
# docstring, confirmed by direct inspection). Every confirmed real row
# has nothing else between the label and its numbers; a row-label
# *pattern* match followed by anything else (an actual, different word)
# means the match landed in narrative prose describing the metric, not
# the table row itself — confirmed real and dangerous: a bare
# "Özkaynaklar" match inside "Özkaynaklar. Şirket'in özkaynakları,
# 2023, 2024 ve 2025 yıl sonları ile ... 5,4 milyar TL, 13,1 milyar
# TL, ..." would otherwise silently harvest nonsense values out of that
# sentence's own numbers. This is the row-level equivalent of the
# heading-level tight-period-cluster check.
_LABEL_TO_NUMBER_GAP_RE = re.compile(r"^[\s()./,*\-]*(?:(?:veya|/)\s*zarar\w*[\s()./,*\-]*)?$")


def _gap_is_plausible_row_continuation(gap_text: str) -> bool:
    return bool(_LABEL_TO_NUMBER_GAP_RE.match(gap_text))


_METRIC_ROW_LABEL_PATTERNS: dict[str, tuple[str, ...]] = {
    # "Net Satışlar" / bare "Hasılat" — both real, interchangeable
    # revenue conventions (see module docstring). An insurer has neither
    # (insurers report written premiums, a different metric never
    # treated as equivalent here); correctly not_found.
    "revenue": (r"net\s+satislar", r"\bhasilat\b"),
    # "Net Kar" / "Net Donem Kari (Veya Zarari)" / bare "Donem Kari (/
    # (Veya) Zarari)" — tried in this order, first match wins (see
    # docstring for why the bare form is real and safe as a last
    # resort). Any "veya/  zararı" suffix is handled by
    # _LABEL_TO_NUMBER_GAP_RE, not consumed here.
    "net_income": (r"net\s+kar\b", r"net\s+donem\s+kari", r"\bdonem\s+kar\w*"),
    # "Nakit ve Benzerleri" / "Nakit ve Nakit Benzeri(leri)", optionally
    # extended "... Varlıkları" (both real, confirmed on multiple
    # independent reports — "\w*" on the optional suffix also tolerates
    # a confirmed real OCR/text-extraction artifact where "ı" renders as
    # the digit "1", e.g. "Varlıklar" -> "Varl1klar").
    "cash_and_equivalents": (r"nakit\s+ve\s+(?:nakit\s+)?benzer\w*(?:\s+varl\w*)?",),
    # See module docstring — label wording confirmed real elsewhere, but
    # not yet as a multi-period Bilanço total in any cached report.
    "financial_debt": (r"\bfinansal\s+borc\w*\b",),
    # "Ana Ortak. Özkaynakları" / "Özsermaye" (tried first — see module
    # docstring for why), then bare "Özkaynaklar" as a last resort,
    # confirmed real as the genuine grand total in a report with no
    # separate parent-attributable line — safe now that
    # _gap_is_plausible_row_continuation rejects a narrative-prose match
    # of this same bare word (confirmed real and confirmed dangerous
    # without that check, see its own docstring).
    "equity": (r"ana\s+ortak\w*\.?\s+ozkaynak\w*", r"\bozsermaye\b", r"\bozkaynaklar\b"),
    # "Dönen Varlıklar" / "Cari Varlıklar" — synonyms for the same
    # concept (both real, confirmed), not two different ones.
    "current_assets": (r"(?:donen|cari)\s+varlik\w*",),
    # "Kısa Vad(eli) Yükümlülükler" — identical wording confirmed across
    # every real report it was found in.
    "current_liabilities": (r"kisa\s+vad\w*\.?\s+yukumluluk\w*",),
    # "EBIT" (a literal abbreviation — the trailing word boundary
    # excludes "EBITDA" by construction), or "Esas Faaliyet Kârı /
    # Zararı" (a second real report's core-operating-profit line — see
    # module docstring for why the broader "... Öncesi Faaliyet Kârı"
    # subtotal some reports also carry is deliberately not matched).
    "operating_profit": (r"\bebit\b", r"esas\s+faaliyet\s+kar\w*"),
    # "İşletme Faaliyetlerinden (Elde Edilen) Nakit" (the standard first
    # cash-flow-statement line), or bare "Esas Faaliyet" (a second real
    # report's synonym — see module docstring for why this pattern
    # doesn't require "nakit" immediately after "faaliyet").
    "operating_cash_flow": (r"isletme\s+faaliyetlerinden\s+(?:elde\s+edilen\s+)?nakit\w*", r"esas\s+faaliyet\w*"),
    # "Fin. Gid." (a literal abbreviated label) or "Finansman Gider(leri)"
    # (a second real report's unabridged form — deliberately distinct
    # from "Finansman Gelirleri", finance *income*, a separate line in
    # the same table).
    "finance_expense": (r"\bfin\.?\s+gid\w*\b", r"finansman\s+gider\w*"),
}


def _period_from_match(match: re.Match[str]) -> tuple[FinancialPeriodType, date, date] | None:
    if match.group(1) is not None:  # DD.MM.YYYY
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if not 1 <= month <= 12:
            return None
        try:
            end = date(year, month, day)
        except ValueError:
            return None
        period_type: FinancialPeriodType = "ANNUAL" if (month, day) == (12, 31) else "INTERIM"
        return (period_type, date(year, 1, 1), end)
    if match.group(4) is not None:  # YYYY/MM
        year, month = int(match.group(4)), int(match.group(5))
        if not 1 <= month <= 12:
            return None
        start = date(year, 1, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        return ("INTERIM", start, end)
    year = int(match.group(6))  # bare YYYY
    return ("ANNUAL", date(year, 1, 1), date(year, 12, 31))


def _iter_period_clusters(window: str) -> list[list[re.Match[str]]]:
    """Every tight run of >= :data:`_MIN_PERIOD_TOKENS` period tokens in
    ``window`` (consecutive tokens no more than :data:`_MAX_PERIOD_TOKEN_GAP`
    characters apart), in order — not just the first. Confirmed
    necessary, not merely thorough: several real reports' table captions
    are immediately preceded by a narrative sentence that itself lists
    the same periods just as tightly as the real header row a bit
    further on (see module docstring) — :func:`_validate_table_header`
    needs to be able to reject that first cluster (no currency marker
    nearby) and fall through to the real one, rather than give up."""
    matches = list(_PERIOD_TOKEN_RE.finditer(window))
    clusters: list[list[re.Match[str]]] = []
    run: list[re.Match[str]] = []
    for match in matches:
        if run and match.start() - run[-1].end() > _MAX_PERIOD_TOKEN_GAP:
            if len(run) >= _MIN_PERIOD_TOKENS:
                clusters.append(run)
            run = []
        run.append(match)
    if len(run) >= _MIN_PERIOD_TOKENS:
        clusters.append(run)
    return clusters


def _detect_currency_and_scale(window: str) -> tuple[str, str] | None:
    """``"bin TL"`` -> thousands of TRY, ``"mnTL"`` -> millions, a bare
    ``"TL"`` -> unit TRY. Every real table's period-header row states
    one of these — never guessed when none is present."""
    if re.search(r"\bbin\s*tl\b", window):
        return "TRY", "thousand"
    if re.search(r"\bmntl\b", window):
        return "TRY", "million"
    if re.search(r"\btl\b", window):
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


@dataclass(frozen=True, slots=True)
class _TableHeader:
    periods: tuple[tuple[FinancialPeriodType, date, date], ...]
    currency: str
    scale: str
    row_search_start: int  # position in the *same window* the header was found in, right after the header


def _validate_table_header(window: str) -> _TableHeader | None:
    """``window`` starts right after a heading occurrence — tries every
    tight period-token cluster in it (see :func:`_iter_period_clusters`
    for why more than one candidate matters), in order, returning the
    first that resolves to a genuine table header: a currency/scale
    marker nearby, no forecast-year token nearby (a projection table),
    a reasonable column count, and every token parseable. ``None`` if
    no cluster in this window qualifies at all."""
    for cluster in _iter_period_clusters(window):
        if len(cluster) > _MAX_CLUSTER_TOKENS:
            continue
        cluster_start, cluster_end = cluster[0].start(), cluster[-1].end()

        margin_start = max(0, cluster_start - _CURRENCY_SEARCH_MARGIN)
        margin_end = min(len(window), cluster_end + _CURRENCY_SEARCH_MARGIN)
        margin_window = window[margin_start:margin_end]
        if _FORECAST_TOKEN_RE.search(margin_window):
            continue

        currency_scale = _detect_currency_and_scale(margin_window)
        if currency_scale is None:
            continue

        periods = [_period_from_match(m) for m in cluster]
        if any(p is None for p in periods):
            # Never silently drop a token and shift period<->number
            # alignment (see module docstring) — a cluster with an
            # unparseable token is rejected outright, not trimmed.
            continue

        currency, scale = currency_scale
        return _TableHeader(
            periods=tuple(p for p in periods if p is not None),  # type: ignore[misc]
            currency=currency,
            scale=scale,
            row_search_start=cluster_end,
        )
    return None


def _extract_metric_row(
    folded_page: str,
    page_text: str,
    next_page_folded: str,
    next_page_text: str,
    metric_name: str,
) -> tuple[list[_RowValue], str, str, ConsolidationScope | None] | None:
    """``(row_values, currency, scale, consolidation_scope)`` for
    ``metric_name``'s row in its table (see :data:`_METRIC_HEADING_RE`)
    anchored on this page, or ``None`` if no occurrence of the heading
    on this page resolves to a real table containing this metric's row
    (see module docstring's table-region-detection section for exactly
    how a real table is told apart from a narrative mention)."""
    heading_re = _METRIC_HEADING_RE[metric_name]

    # A bounded peek into the next page, for a table whose row runs
    # across a page break with no repeated caption (see module
    # docstring, point 5) — disabled if the peeked text itself
    # re-announces this heading, since a real continuation never does.
    peek_folded = next_page_folded[:_CONTINUATION_PEEK_CHARS]
    peek_text = next_page_text[:_CONTINUATION_PEEK_CHARS]
    if heading_re.search(peek_folded):
        peek_folded = ""
        peek_text = ""
    extended_folded = folded_page + "\n" + peek_folded
    extended_text = page_text + "\n" + peek_text

    for heading_match in heading_re.finditer(folded_page):
        window_start = heading_match.end()
        header = _validate_table_header(folded_page[window_start : window_start + _HEADER_WINDOW_CHARS])
        if header is None:
            continue

        row_search_start = window_start + header.row_search_start
        label_end = None
        number_matches: list[re.Match[str]] = []
        row_window = ""
        for pattern in _METRIC_ROW_LABEL_PATTERNS[metric_name]:
            label_match = re.search(pattern, extended_folded[row_search_start:])
            if not label_match:
                continue
            candidate_label_end = row_search_start + label_match.end()
            candidate_row_window = extended_folded[candidate_label_end : candidate_label_end + _ROW_VALUE_WINDOW_CHARS]
            candidate_numbers = list(re.finditer(_SIGNED_NUM, candidate_row_window))
            if len(candidate_numbers) < len(header.periods):
                continue
            gap_text = candidate_row_window[: candidate_numbers[0].start()]
            if not _gap_is_plausible_row_continuation(gap_text):
                # A real label match, but immediately followed by prose
                # rather than a table row's own numbers (see
                # _gap_is_plausible_row_continuation's docstring) — try
                # the next pattern instead of accepting a match that
                # would harvest nonsense values out of narrative text.
                continue
            label_end, row_window, number_matches = candidate_label_end, candidate_row_window, candidate_numbers
            break
        if label_end is None:
            continue

        row_values: list[_RowValue] = []
        for (period_type, start, end), number_match in zip(header.periods, number_matches[: len(header.periods)]):
            raw = extended_text[label_end + number_match.start() : label_end + number_match.end()]
            value = _parse_signed_turkish_number(raw)
            if value is None:
                row_values = []
                break
            row_values.append(_RowValue(value, period_type, start, end, raw))
        if not row_values:
            continue

        consolidation_scope = _detect_consolidation_scope(folded_page)
        return row_values, header.currency, header.scale, consolidation_scope

    return None


def extract_financial_observations_from_pages(
    pages: Sequence[PdfPage],
    *,
    document_type: str,
    disclosure_id: str,
    attachment_url: str,
    extraction_method: ExtractionMethod = "digital",
    source_system: SourceSystem = "kap",
) -> tuple[FinancialObservation, ...]:
    """Run every metric's table-row extractor (see :data:`_METRIC_HEADING_RE`)
    over ``pages``, keeping the *first* page each metric's table is found
    on (mirrors
    :func:`halka_arz_advisor.kap.extraction.extract_observations_from_pages`'s
    first-match-wins convention) — one :class:`FinancialObservation`
    per explicitly labelled period found for that metric."""
    observations: list[FinancialObservation] = []
    folded_pages = [fold_turkish(page.text) for page in pages]

    for metric_name in FINANCIAL_METRIC_NAMES:
        for index, page in enumerate(pages):
            next_folded = folded_pages[index + 1] if index + 1 < len(pages) else ""
            next_text = pages[index + 1].text if index + 1 < len(pages) else ""
            found = _extract_metric_row(folded_pages[index], page.text, next_folded, next_text, metric_name)
            if found is None:
                continue

            row_values, currency, scale, consolidation_scope = found
            source = SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method, source_system)
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
