"""Deterministic, regex-based extraction of core IPO participation fields
from prospectus (İzahname) and investor sale announcement (Tasarruf
Sahiplerine Satış Duyurusu) PDF text.

Design principles (per the brief):

- No LLM, no fuzzy NLP — plain regexes over each PDF page's extracted
  text, matched against real language observed in live KAP filings
  (see the pattern comments below for where each one came from).
- Every field is either **found** (with its raw snippet, source
  disclosure/attachment/page) or **not found** — never invented.
- Numbers are normalized from Turkish formatting (``.`` thousands
  separator, ``,`` decimal separator) into plain numeric values; dates
  from ``DD.MM.YYYY``/``DD/MM/YYYY`` into ISO dates.
- When both the prospectus and the investor sale announcement state a
  value for the same field, and they **disagree**, neither is silently
  preferred — the fact is marked ``"conflicting"`` and both
  observations are kept. When they agree, or only one has a value, the
  field-priority rule (prospectus vs. announcement, see
  :data:`PROSPECTUS_PRIORITY_FIELDS`/:data:`ANNOUNCEMENT_PRIORITY_FIELDS`)
  picks which single observation becomes the selected value.

Turkish text passed in is matched via a folded (ASCII-lowercased) copy
for keyword lookups, but every reported snippet/value is sliced back out
of the *original* text — :func:`~halka_arz_advisor.kap.text.fold_turkish`
is a strict one-Turkish-character-to-one-ASCII-character map, so match
offsets on the folded copy line up with the original string.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from .pdf import PdfPage
from .text import fold_turkish

FactStatus = Literal["extracted", "not_found", "conflicting"]

FIELD_NAMES: tuple[str, ...] = (
    "subscription_start_date",
    "subscription_end_date",
    "offering_price",
    "currency",
    "distribution_method",
    "capital_increase_shares",
    "secondary_sale_shares",
    "total_offered_shares",
    "capital_increase_ratio",
    "secondary_sale_ratio",
    "use_of_proceeds",
    "key_risk_items",
)

# Per rule 8 of the brief: which document type wins when both the
# prospectus and the investor sale announcement state the *same* value
# for a field (only relevant when they agree — see module docstring for
# the conflicting-values behavior).
ANNOUNCEMENT_PRIORITY_FIELDS = frozenset({"subscription_start_date", "subscription_end_date", "offering_price", "currency", "distribution_method"})
PROSPECTUS_PRIORITY_FIELDS = frozenset(
    {
        "capital_increase_shares",
        "secondary_sale_shares",
        "total_offered_shares",
        "capital_increase_ratio",
        "secondary_sale_ratio",
        "use_of_proceeds",
        "key_risk_items",
    }
)


ExtractionMethod = Literal["digital", "ocr"]


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where one observed value came from."""

    document_type: str  # e.g. "approved_prospectus" | "investor_sale_announcement"
    disclosure_id: str
    attachment_url: str
    page_number: int | None
    extraction_method: ExtractionMethod = "digital"


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """One field value as found in one specific document."""

    value: object
    raw_snippet: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """The result for one field, across every document it was looked for in."""

    status: FactStatus
    value: object | None
    raw_snippet: str | None
    source: SourceRef | None
    observations: tuple[FieldObservation, ...]


def _not_found() -> ExtractedFact:
    return ExtractedFact(status="not_found", value=None, raw_snippet=None, source=None, observations=())


# --------------------------------------------------------------------------
# Turkish number/date normalization
# --------------------------------------------------------------------------


def parse_turkish_number(raw: str) -> float | None:
    """``"2.380.000.000"`` -> ``2380000000.0``; ``"25,03"`` -> ``25.03``."""
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_turkish_date(token: str) -> date | None:
    """``"22.07.2026"`` / ``"22/07/2026"`` -> ``date(2026, 7, 22)``."""
    match = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", token.strip())
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Per-field regex extraction over one page's text
#
# All patterns below are written in already-folded (ASCII-lowercase)
# spelling and matched against `fold_turkish(text)`, never the original —
# e.g. "artırılacak" is written here as "artirilacak", "TL" as "tl". Every
# pattern also carries re.IGNORECASE as a second line of defense.
# --------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?"  # Turkish-formatted number, e.g. 2.380.000.000 or 25,03
_DATE = r"\d{1,2}[./]\d{1,2}[./]\d{4}"


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# "22.07.2026 - 24.07.2026", "22.07.2026 ile 24.07.2026 tarihleri arasında",
# anchored near "talep toplama" (subscription collection).
_SUBSCRIPTION_DATE_RANGE_RE = _re(rf"talep\s+toplama[^\n]{{0,120}}?({_DATE})\s*(?:-|ile)\s*({_DATE})")

# "Halka arz satış fiyatı olarak belirlenen 76,60 TL" — observed live in a
# real Fiyat Tespit Raporu.
_PRICE_NARRATIVE_RE = _re(rf"belirlenen\s+({_NUM})\s*tl")
# "Halka Arz Fiyatı (TL) : 76,60" / "Halka Arz Fiyatı: 76,60 TL" — a more
# direct label:value form, tried if the narrative sentence isn't found.
_PRICE_LABEL_RE = _re(rf"halka\s+arz\s+fiyati\s*\(?\s*tl\s*\)?\s*[:\-]?\s*({_NUM})")

# Controlled vocabulary of SPK-defined IPO distribution methods — checked
# in this order (first match wins, most-specific first).
_DISTRIBUTION_METHODS: tuple[str, ...] = (
    "fiyat araligi ile talep toplama",
    "sabit fiyatla talep toplama",
    "degisken fiyatla talep toplama",
    "borsada satis yontemi",
    "borsada satis",
)

# "artırılacak 2.380.000.000 TL nominal değerli" — the capital-increase
# nominal amount, observed live in a real prospectus.
_CAPITAL_INCREASE_RE = _re(rf"artirilacak\s+({_NUM})\s*tl\s+nominal\s+degerli")
# "%170 oranında" / "% 25,03" near a capital-increase context.
_CAPITAL_INCREASE_RATIO_RE = _re(rf"%\s*({_NUM})\s+oraninda[^\n]{{0,60}}artir")

# "ortak satışı yoluyla ... 1.300.000.000 TL" / "ortak satışına konu
# ... TL nominal değerli" — secondary (existing-shareholder) sale amount.
_SECONDARY_SALE_RE = _re(rf"ortak\s+satisi[^\n]{{0,60}}?({_NUM})\s*tl")
_SECONDARY_SALE_RATIO_RE = _re(rf"ortak\s+satisi[^\n]{{0,60}}?%\s*({_NUM})")

# "satışa sunulan toplam ... TL" / "toplam ... TL nominal değerli
# paylarının halka arzı" — the combined total offered amount.
_TOTAL_OFFERED_RE = _re(rf"toplam\s+({_NUM})\s*tl\s+nominal\s+degerli")

_USE_OF_PROCEEDS_HEADINGS: tuple[str, ...] = (
    "halka arzdan elde edilecek fonun kullanimi",
    "fon kullanim yeri",
    "fon kullanimi",
)
_RISK_HEADINGS: tuple[str, ...] = (
    "risk faktorleri",
    "ihracciya iliskin riskler",
    "halka arz edilecek paylara iliskin riskler",
)


def _search(folded: str, original: str, pattern: re.Pattern[str], group: int = 1) -> tuple[str, str] | None:
    """Search ``pattern`` in ``folded``; return ``(value_text, full_match_snippet)``
    sliced out of ``original`` at the same offsets, or ``None``."""
    match = pattern.search(folded)
    if not match:
        return None
    return original[match.start(group) : match.end(group)], original[match.start() : match.end()]


def extract_subscription_dates(text: str) -> tuple[tuple[date, str] | None, tuple[date, str] | None]:
    folded = fold_turkish(text)
    match = _SUBSCRIPTION_DATE_RANGE_RE.search(folded)
    if not match:
        return None, None
    snippet = text[match.start() : match.end()]
    start_date = parse_turkish_date(text[match.start(1) : match.end(1)])
    end_date = parse_turkish_date(text[match.start(2) : match.end(2)])
    start = (start_date, snippet) if start_date else None
    end = (end_date, snippet) if end_date else None
    return start, end


def extract_offering_price(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _PRICE_NARRATIVE_RE) or _search(folded, text, _PRICE_LABEL_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_distribution_method(text: str) -> tuple[str, str] | None:
    folded = fold_turkish(text)
    for method in _DISTRIBUTION_METHODS:
        idx = folded.find(method)
        if idx != -1:
            snippet = text[idx : idx + len(method)]
            return snippet, snippet
    return None


def extract_capital_increase_shares(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _CAPITAL_INCREASE_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_capital_increase_ratio(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _CAPITAL_INCREASE_RATIO_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_secondary_sale_shares(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _SECONDARY_SALE_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_secondary_sale_ratio(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _SECONDARY_SALE_RATIO_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_total_offered_shares(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _TOTAL_OFFERED_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def _extract_section_items(
    text: str, headings: tuple[str, ...], *, max_items: int = 5, max_item_length: int = 300
) -> list[tuple[str, str]] | None:
    """Find the first matching heading, then split the following text into
    up to ``max_items`` short items (sentences), each ``(text, snippet)``."""
    folded = fold_turkish(text)
    heading_end = None
    for heading in headings:
        idx = folded.find(heading)
        if idx != -1:
            heading_end = idx + len(heading)
            break
    if heading_end is None:
        return None

    body = text[heading_end : heading_end + 4000]
    # Split on sentence-ish boundaries; drop empties and header noise.
    raw_sentences = re.split(r"(?<=[.;])\s+", body)
    items: list[tuple[str, str]] = []
    for sentence in raw_sentences:
        cleaned = sentence.strip()
        if len(cleaned) < 20:  # too short to be a real sentence (stray fragment/heading leftover)
            continue
        snippet = cleaned[:max_item_length]
        items.append((snippet, snippet))
        if len(items) >= max_items:
            break
    return items or None


def extract_use_of_proceeds(text: str) -> list[tuple[str, str]] | None:
    return _extract_section_items(text, _USE_OF_PROCEEDS_HEADINGS)


def extract_key_risk_items(text: str) -> list[tuple[str, str]] | None:
    return _extract_section_items(text, _RISK_HEADINGS)


# --------------------------------------------------------------------------
# Currency — inferred alongside offering_price, not searched independently
# --------------------------------------------------------------------------


def extract_currency(text: str) -> tuple[str, str] | None:
    """SPK/KAP IPO prices are consistently quoted in TL in every document
    observed; this only confirms "TL" appears near a price context rather
    than assuming it blindly."""
    price = extract_offering_price(text)
    if price is None:
        return None
    return "TRY", price[1]


# --------------------------------------------------------------------------
# Orchestration: pages -> per-field observations (with provenance) -> merge
# --------------------------------------------------------------------------

_SCALAR_EXTRACTORS: tuple[tuple[str, Callable[[str], tuple[object, str] | None]], ...] = (
    ("offering_price", extract_offering_price),
    ("currency", extract_currency),
    ("distribution_method", extract_distribution_method),
    ("capital_increase_shares", extract_capital_increase_shares),
    ("secondary_sale_shares", extract_secondary_sale_shares),
    ("total_offered_shares", extract_total_offered_shares),
    ("capital_increase_ratio", extract_capital_increase_ratio),
    ("secondary_sale_ratio", extract_secondary_sale_ratio),
)

_LIST_EXTRACTORS: tuple[tuple[str, Callable[[str], list[tuple[str, str]] | None]], ...] = (
    ("use_of_proceeds", extract_use_of_proceeds),
    ("key_risk_items", extract_key_risk_items),
)


def extract_observations_from_pages(
    pages: Sequence[PdfPage],
    *,
    document_type: str,
    disclosure_id: str,
    attachment_url: str,
    extraction_method: ExtractionMethod = "digital",
) -> dict[str, FieldObservation]:
    """Run every field extractor over ``pages`` (in page order), keeping
    the *first* match found for each field, with its page number.

    ``pages`` may come from the PDF's own digital text layer or from
    OCR (see :mod:`halka_arz_advisor.kap.ocr`) — the regex matching
    itself is identical either way; ``extraction_method`` only tags the
    resulting observations' provenance so a later conflict between a
    digital and an OCR'd observation (see
    :func:`merge_field_observations`) is distinguishable, never silently
    resolved by simply preferring one.
    """
    observations: dict[str, FieldObservation] = {}

    for page in pages:
        if "subscription_start_date" in observations and "subscription_end_date" in observations:
            break
        start, end = extract_subscription_dates(page.text)
        if start and "subscription_start_date" not in observations:
            value, snippet = start
            observations["subscription_start_date"] = FieldObservation(
                value=value,
                raw_snippet=snippet,
                source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method),
            )
        if end and "subscription_end_date" not in observations:
            value, snippet = end
            observations["subscription_end_date"] = FieldObservation(
                value=value,
                raw_snippet=snippet,
                source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method),
            )

    for field_name, extractor in _SCALAR_EXTRACTORS:
        for page in pages:
            found = extractor(page.text)
            if found:
                value, snippet = found
                observations[field_name] = FieldObservation(
                    value=value,
                    raw_snippet=snippet,
                    source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method),
                )
                break

    for field_name, list_extractor in _LIST_EXTRACTORS:
        for page in pages:
            found_items = list_extractor(page.text)
            if found_items:
                value = [item_text for item_text, _ in found_items]
                snippet = " | ".join(item_snippet for _, item_snippet in found_items)
                observations[field_name] = FieldObservation(
                    value=value,
                    raw_snippet=snippet,
                    source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method),
                )
                break

    return observations


def merge_field_observations(
    field_name: str,
    prospectus_observation: FieldObservation | None,
    announcement_observation: FieldObservation | None,
) -> ExtractedFact:
    """Combine what the prospectus and the investor sale announcement each
    said about one field into a single :class:`ExtractedFact`.

    - Neither has it -> ``not_found``.
    - Only one has it -> that one, ``extracted``.
    - Both have it and **agree** -> ``extracted``, using whichever
      document rule 8 of the brief prefers for this field, but keeping
      *both* observations for provenance.
    - Both have it and **disagree** -> ``conflicting``; no value is
      silently picked, both observations are kept.
    """
    if prospectus_observation is None and announcement_observation is None:
        return _not_found()

    if prospectus_observation is not None and announcement_observation is not None:
        observations = (prospectus_observation, announcement_observation)
        if prospectus_observation.value == announcement_observation.value:
            winner = (
                announcement_observation
                if field_name in ANNOUNCEMENT_PRIORITY_FIELDS
                else prospectus_observation
            )
            return ExtractedFact(
                status="extracted",
                value=winner.value,
                raw_snippet=winner.raw_snippet,
                source=winner.source,
                observations=observations,
            )
        return ExtractedFact(status="conflicting", value=None, raw_snippet=None, source=None, observations=observations)

    single = prospectus_observation or announcement_observation
    assert single is not None
    return ExtractedFact(
        status="extracted",
        value=single.value,
        raw_snippet=single.raw_snippet,
        source=single.source,
        observations=(single,),
    )


@dataclass(frozen=True, slots=True)
class ExtractedFacts:
    subscription_start_date: ExtractedFact
    subscription_end_date: ExtractedFact
    offering_price: ExtractedFact
    currency: ExtractedFact
    distribution_method: ExtractedFact
    capital_increase_shares: ExtractedFact
    secondary_sale_shares: ExtractedFact
    total_offered_shares: ExtractedFact
    capital_increase_ratio: ExtractedFact
    secondary_sale_ratio: ExtractedFact
    use_of_proceeds: ExtractedFact
    key_risk_items: ExtractedFact

    def as_dict(self) -> dict[str, ExtractedFact]:
        return {name: getattr(self, name) for name in FIELD_NAMES}


def build_extracted_facts(
    prospectus_observations: dict[str, FieldObservation] | None,
    announcement_observations: dict[str, FieldObservation] | None,
) -> ExtractedFacts:
    """Merge one document's worth of prospectus observations and one
    document's worth of announcement observations into the final,
    per-field :class:`ExtractedFacts` (see :func:`merge_field_observations`)."""
    prospectus_observations = prospectus_observations or {}
    announcement_observations = announcement_observations or {}
    merged = {
        field_name: merge_field_observations(
            field_name, prospectus_observations.get(field_name), announcement_observations.get(field_name)
        )
        for field_name in FIELD_NAMES
    }
    return ExtractedFacts(**merged)
