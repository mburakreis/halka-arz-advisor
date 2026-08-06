"""Match a KAP disclosure to an existing SPK record — a completed IPO
or an IPO application — by exact ticker first, then normalized company
name.

Reuses the same SPK record identity scheme already established in
:mod:`halka_arz_advisor.notify.identity` (rather than inventing a new
ID format), so ``matched_spk_record_id`` values line up with the ones
the notification MVP already tracks.

Ambiguous results — more than one SPK record matches — are never
auto-resolved; the disclosure is left ``unmatched`` rather than guessed
at.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ..notify.identity import application_identity, ipo_identity
from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord
from .models import KapDisclosure
from .text import fold_turkish

# Explicit, controlled suffix tokens — exactly the ones this phase was
# asked to strip ("A.Ş.", "AŞ", "Anonim Şirketi", "Sanayi", "Ticaret"),
# plus their common alternate Turkish spellings ("Sanayii"/"Ticareti")
# observed in real SPK/KAP company names (e.g. "KARSU TEKSTİL SANAYİİ
# VE TİCARET A.Ş."), and the connecting "VE" ("and") so stripping both
# halves of "Sanayi ve Ticaret" doesn't leave a dangling "ve" behind.
# Company names are folded, periods removed, and split on whitespace
# first, so these only ever match whole tokens — never a substring
# inside an unrelated word.
_SUFFIX_TOKENS = frozenset(
    {
        "as",  # "A.Ş." / "AŞ" once periods are stripped and Ş is folded to s
        "anonim",
        "sirketi",
        "sanayi",
        "sanayii",
        "ticaret",
        "ticareti",
        "ve",
    }
)


def normalize_company_name(name: str) -> str:
    """Fold Turkish characters, drop periods, and strip controlled
    legal/commercial suffix tokens, for comparing two company names."""
    # Periods are removed rather than replaced with a space so "A.Ş."
    # merges into the single token "as" (matching the no-dots "AŞ"
    # spelling) instead of splitting into stray single-letter tokens.
    text = fold_turkish(name).replace(".", "")
    tokens = [t for t in text.split() if t and t not in _SUFFIX_TOKENS]
    return " ".join(tokens)


def _ticker_key(value: str) -> str:
    return value.strip().upper()


def match_disclosure(
    disclosure: KapDisclosure,
    *,
    ipo_records: Sequence[SpkIpoRecord] = (),
    application_records: Sequence[SpkIpoApplicationRecord] = (),
) -> KapDisclosure:
    """Return a copy of ``disclosure`` with ``matched_spk_record_id`` and
    ``match_method`` filled in.

    Only completed IPO records (:class:`SpkIpoRecord`) carry a ticker,
    so ticker matching only ever considers ``ipo_records``. Company-name
    matching considers both pools.

    If ``disclosure.ticker`` is set but doesn't match anything, this
    does **not** fall back to company-name — ``ticker`` is preferentially
    sourced from KAP's ``relatedStocks`` (see
    :func:`halka_arz_advisor.kap.models._extract_ticker`), which for
    intermediary-filed disclosures (Fiyat Tespit Raporu, Halka Arz
    Sonuçları) identifies the actual IPO company, while ``company_name``
    is always the *filer* (``companyTitle`` — often the underwriting
    brokerage, a different company entirely). Falling back from an
    unmatched subject-ticker to the filer's name previously produced a
    real false positive: a "Bewen Enerji A.Ş." price-determination
    report filed by "Marbaş Menkul Değerler A.Ş." matched Marbaş's own,
    unrelated SPK application record purely because the filer's name
    happened to also appear in the applications list. Company-name
    matching is therefore only attempted when there was no ticker to
    begin with.
    """
    if disclosure.ticker:
        key = _ticker_key(disclosure.ticker)
        ticker_matches = {
            ipo_identity(record)
            for record in ipo_records
            if record.borsa_kodu and _ticker_key(record.borsa_kodu) == key
        }
        if len(ticker_matches) == 1:
            return replace(disclosure, matched_spk_record_id=next(iter(ticker_matches)), match_method="ticker")
        return replace(disclosure, matched_spk_record_id=None, match_method="unmatched")

    target_name = normalize_company_name(disclosure.company_name)
    if target_name:
        ipo_name_matches = {
            ipo_identity(record)
            for record in ipo_records
            if record.sirket_unvani and normalize_company_name(record.sirket_unvani) == target_name
        }
        application_name_matches = {
            application_identity(record)
            for record in application_records
            if normalize_company_name(record.company_name) == target_name
        }
        name_matches = ipo_name_matches | application_name_matches
        if len(name_matches) == 1:
            return replace(disclosure, matched_spk_record_id=next(iter(name_matches)), match_method="company_name")
        if len(name_matches) > 1:
            return replace(disclosure, matched_spk_record_id=None, match_method="unmatched")

    return replace(disclosure, matched_spk_record_id=None, match_method="unmatched")
