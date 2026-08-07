"""Classify a KAP disclosure title into one of the IPO-related document
types this phase cares about, or ``other``.

Classification is purely title-text-pattern matching (case-insensitive,
Turkish-character-robust via :func:`~halka_arz_advisor.kap.text.fold_turkish`)
against the patterns given in the brief — it does not inspect the
disclosure's summary, attachments, or KAP's own ``disclosureType``/
``disclosureClass`` codes, which are far coarser than what we need here
(e.g. every one of our target patterns falls under KAP's own generic
"DG"/"ODA" classes alongside hundreds of unrelated disclosure kinds).

**Official report vs. third-party review of that report.** KAP disclosure
titles for a real IPO's price determination step come in two shapes,
confirmed against live data:

- ``"Fiyat Tespit Raporu"`` — the issuer/underwriter's own official report.
- ``"Fiyat Tespit Raporuna İlişkin Analist Raporu (...)"`` and
  ``"Fiyat Tespit Raporuna İlişkin Değerlendirme Raporu"`` — a *different*
  brokerage's or analyst's independent review/opinion **of** that report,
  filed as its own separate disclosure (e.g. seen live: "Fiyat Tespit
  Raporuna İlişkin Analist Raporu (Halka Arza Aracılık Eden Kuruluş
  Dışında Farklı bir Kuruluş Tarafından Hazırlanan)" filed by a
  *different* brokerage than the one that ran the IPO).

Both contain the substring "fiyat tespit raporu", so the review variants
are matched *first*, using the "iliskin ... raporu" (review-of-a-report)
shape, before falling through to the plain official-report pattern —
otherwise every review would be misclassified as the official report.
"""

from __future__ import annotations

import re
from typing import Literal

from .text import fold_turkish

DocumentType = Literal[
    "approved_prospectus",
    "investor_sale_announcement",
    "price_determination_report",
    "price_determination_review",
    "ipo_results",
    "trading_start",
    # The two below are never produced by classify_title() / KAP disclosure
    # titles — KAP doesn't file these as their own disclosure type (a
    # financial-statement attachment or use-of-proceeds report is either
    # embedded in another document or extracted as a field, not a
    # separate KAP disclosure). They exist only for
    # halka_arz_advisor.issuer_ir's classify_issuer_link_title(), which
    # classifies standalone PDF attachments an issuer's own investor-
    # relations page links directly (see that function's docstring).
    "financial_statement_attachment",
    "use_of_proceeds_report",
    "other",
]

# (folded pattern, resulting document type) — checked in order, first
# match wins. "İzahname" and "Onaylı İzahname" both resolve to the same
# category; both are listed for direct traceability to the brief even
# though "izahname" alone already subsumes the "onaylı" variant.
#
# The two "...raporuna iliskin ... raporu" review patterns MUST be
# checked before the bare "fiyat tespit raporu" pattern, since both are
# substrings of the review titles too. A broader guard below also
# catches re-ordered/unseen review phrasings that mention "fiyat tespit
# raporu" alongside "analist"/"değerlendirme" without matching either
# exact phrase here.
_CLASSIFICATION_PATTERNS: tuple[tuple[str, DocumentType], ...] = (
    ("onayli izahname", "approved_prospectus"),
    ("izahname", "approved_prospectus"),
    ("tasarruf sahiplerine satis duyurusu", "investor_sale_announcement"),
    ("fiyat tespit raporuna iliskin analist raporu", "price_determination_review"),
    ("fiyat tespit raporuna iliskin degerlendirme raporu", "price_determination_review"),
    ("fiyat tespit raporu", "price_determination_report"),
    ("halka arz sonuclari", "ipo_results"),
    ("islem gormeye baslama", "trading_start"),
)

# A "fiyat tespit raporu" title is only ever the *official* report when
# it does NOT also mention one of these third-party-review markers.
# Catches phrasing variants beyond the two exact patterns above (e.g. a
# differently-worded or re-ordered analyst/broker commentary title).
_PRICE_REPORT_REVIEW_MARKERS: tuple[str, ...] = ("analist", "degerlendirme")

TARGET_DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    "approved_prospectus",
    "investor_sale_announcement",
    "price_determination_report",
    "ipo_results",
    "trading_start",
)


def classify_title(title: str) -> DocumentType:
    """Classify a disclosure title using substring matching on folded text."""
    normalized = fold_turkish(title)
    for pattern, document_type in _CLASSIFICATION_PATTERNS:
        if pattern in normalized:
            if document_type == "price_determination_report" and any(
                marker in normalized for marker in _PRICE_REPORT_REVIEW_MARKERS
            ):
                return "price_determination_review"
            return document_type
    return "other"


# Checked only by classify_issuer_link_title() below, on link *text*
# from an issuer's own investor-relations page — confirmed live against
# real "EK" (appendix) link labels on quicksigorta.com and
# metgunenerji.com.tr, e.g. "EK 3: ... Konsolide Finansal Tablolar ve
# Bağımsız Denetim Raporları ve Dipnotlar" and "EK 7/EK 9: Fonun/Fon
# Kullanım(ına) ... Yönetim Kurulu Kararı ve Raporu". Order matters here
# too: a title could plausibly mention both, but in every real example
# seen so far each "EK" attachment is one or the other, never both.
# "fon kullanim" (QUICK: "Fon Kullanım Raporuna İlişkin...") doesn't
# catch every real phrasing on its own — METEN's own link reads "Fonun
# Kullanım Yerlerine İlişkin..." ("the fund's usage", Turkish genitive
# suffix breaking the plain "fon kullanim" substring) — so this one is a
# regex: "fon" plus any word-forming suffix, whitespace, then "kullan"
# (the stem shared by kullanim/kullanılan/kullanılacak/...), still a
# deterministic pattern match, never fuzzy/similarity-based.
_FON_KULLANIM_RE = re.compile(r"fon\w*\s+kullan")

_ISSUER_LINK_ONLY_PATTERNS: tuple[tuple[re.Pattern[str], DocumentType], ...] = (
    (_FON_KULLANIM_RE, "use_of_proceeds_report"),
    (re.compile(r"finansal tablolar"), "financial_statement_attachment"),
    (re.compile(r"bagimsiz denetim raporu"), "financial_statement_attachment"),
)


def classify_issuer_link_title(link_text: str) -> DocumentType:
    """Classify one issuer investor-relations page's PDF link, by its
    visible link text — the same deterministic substring matching
    :func:`classify_title` already does for KAP disclosure titles (in
    fact this tries that first, so a link literally titled "İzahname" or
    "Fiyat Tespit Raporu" classifies identically either way), extended
    with two patterns for the standalone attachment types only an
    issuer's own site links directly (see
    :data:`_ISSUER_LINK_ONLY_PATTERNS`). Never uses an LLM or fuzzy/
    similarity matching — plain deterministic pattern checks on folded
    text, same as KAP's own classifier.
    """
    kap_type = classify_title(link_text)
    if kap_type != "other":
        return kap_type
    normalized = fold_turkish(link_text)
    for pattern, document_type in _ISSUER_LINK_ONLY_PATTERNS:
        if pattern.search(normalized):
            return document_type
    return "other"


def target_document_types() -> tuple[DocumentType, ...]:
    """The five target IPO document types — everything except ``other``
    and (deliberately) except ``price_determination_review``, which is a
    third-party commentary document, not one of the issuer's own
    official IPO documents."""
    return TARGET_DOCUMENT_TYPES
