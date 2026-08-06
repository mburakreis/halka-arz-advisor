"""Deterministic sector classification, used to gate which financial
metrics/derived features are even a meaningful concept for a company —
so an unsupported metric reports ``NOT_APPLICABLE`` rather than looking
like a missing/zero value.

Classification reads only the company's registered legal name — no new
PDF pattern, no new external data source. Turkish company law requires
certain regulated structures to carry their type directly in the legal
name (every insurer's name ends "... Sigorta A.Ş.", every REIT's ends
"... Gayrimenkul Yatırım Ortaklığı A.Ş." / "... GYO A.Ş."), so this is
as deterministic and explicit as any other extractor in this project —
it just reads a field :mod:`halka_arz_advisor.kap` already resolves for
every disclosure (:attr:`~halka_arz_advisor.kap.models.KapDisclosure.company_name`)
instead of a PDF page.

The two applicability tables below are deliberately narrow and each
entry is grounded in something confirmed either from real report text
or from a well-established accounting rule, not guessed:

- insurance -> revenue, operating_profit (and the derived features that
  need them): confirmed directly — QUICK Sigorta A.Ş.'s real price
  determination report has no "Net Satışlar" line anywhere (insurers
  report written premiums, a different concept) and no "EBIT"/operating-
  profit line either (an insurer's income statement structure has no
  equivalent — underwriting result and investment result combine
  differently than an industrial company's). QUICK's balance sheet
  *does* classify assets/liabilities into current/non-current (labelled
  "Cari Varlıklar"/"Kısa Vadeli Yükümlülükler" — a wording difference
  from "Dönen Varlıklar", not a structural one), so those are *not*
  gated for insurance.
- banking -> current_assets, current_liabilities (and current_ratio):
  no live-validated bank report exists in this project yet — this rests
  on the general IFRS practice that a bank's statement of financial
  position is not presented in a classified current/non-current format,
  the same "designed by documented accounting-standard analogy, not a
  live match" precedent already used for
  :data:`halka_arz_advisor.kap.financials.FINANCIAL_METRIC_NAMES`'s
  financial_debt/operating_cash_flow.

REIT and investment holding structures are recognized (so the coverage
catalog/audit can label a company's sector correctly) but have no
applicability gate in this first version — no well-grounded reason to
gate any metric for them was found.
"""

from __future__ import annotations

import re
from typing import Literal

from .text import fold_turkish

Sector = Literal["standard", "insurance", "banking", "reit", "investment_holding", "unknown"]

SECTORS: tuple[Sector, ...] = ("standard", "insurance", "banking", "reit", "investment_holding", "unknown")


def classify_sector(company_name: str | None) -> Sector:
    """Classify from the company's registered legal name alone — never
    from PDF narrative text. ``UNKNOWN`` when no name is available."""
    if not company_name:
        return "unknown"
    folded = fold_turkish(company_name)
    if re.search(r"\bsigorta\b", folded) or "hayat ve emeklilik" in folded or re.search(r"\bemeklilik\b", folded):
        return "insurance"
    if re.search(r"\bbankasi\b", folded) or re.search(r"\bbank\b", folded):
        return "banking"
    if "gayrimenkul yatirim ortakligi" in folded or re.search(r"\bgyo\b", folded):
        return "reit"
    if "yatirim ortakligi" in folded:
        return "investment_holding"
    return "standard"


# Extracted financial_series metrics a sector never reports in a
# comparable sense (see module docstring for the evidence behind each).
SECTOR_INAPPLICABLE_METRICS: dict[Sector, frozenset[str]] = {
    "standard": frozenset(),
    "insurance": frozenset({"revenue", "operating_profit"}),
    "banking": frozenset({"current_assets", "current_liabilities"}),
    "reit": frozenset(),
    "investment_holding": frozenset(),
    "unknown": frozenset(),
}

# Derived features that structurally depend on an inapplicable metric
# above — stated explicitly rather than auto-derived from the table
# above, since a derived feature can depend on more than one metric and
# the mapping is worth stating plainly.
SECTOR_INAPPLICABLE_DERIVED_FEATURES: dict[Sector, frozenset[str]] = {
    "standard": frozenset(),
    "insurance": frozenset({"revenue_growth_yoy", "net_margin", "interest_coverage"}),
    "banking": frozenset({"current_ratio"}),
    "reit": frozenset(),
    "investment_holding": frozenset(),
    "unknown": frozenset(),
}
