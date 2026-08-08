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
    "par_value_per_share",
    "pre_offer_capital",
    "post_offer_capital",
    "investor_group_allocations",
    "investor_group_distribution_rules",
    "distribution_regulation_reference",
    "use_of_proceeds",
    "key_risk_items",
    # Post-offer fields, sourced from the IPO results disclosure
    # ("Halka Arzına İlişkin Sonuçlar") — never from the prospectus or
    # investor sale announcement, so they're outside
    # ANNOUNCEMENT_PRIORITY_FIELDS/PROSPECTUS_PRIORITY_FIELDS below (no
    # two-document priority rule applies; see merge_field_observations's
    # docstring for how a *third* source is merged).
    "total_participant_count",
    "retail_participant_count",
    "total_demand_multiple",
    "retail_demand_multiple",
    "retail_allocated_shares",
    "institutional_allocated_shares",
    # Valuation-summary fields, sourced from the price determination
    # report ("Fiyat Tespit Raporu") — never from the prospectus,
    # announcement, or IPO results disclosure, so (like the ipo_results
    # fields above) they never participate in the two-document priority
    # rule and are outside ANNOUNCEMENT_PRIORITY_FIELDS/PROSPECTUS_PRIORITY_FIELDS.
    "reported_post_money_market_cap",
    "reported_enterprise_value",
    "reported_net_debt",
    "reported_pe",
    "reported_ev_ebitda",
    "reported_ps",
    "reported_pb",
    "headline_discount_percentage",
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
        "par_value_per_share",
        "pre_offer_capital",
        "post_offer_capital",
        "use_of_proceeds",
        "key_risk_items",
    }
)


ExtractionMethod = Literal["digital", "ocr"]

# Where the *document* this observation came from was itself published
# — "kap" (the default everywhere in this module) for a KAP disclosure,
# "issuer_ir" for a PDF discovered on the issuer's own investor-relations
# site (see :mod:`halka_arz_advisor.issuer_ir`). Purely provenance
# metadata: it plays no role in extraction itself, only in how two
# disagreeing observations are later prioritized (KAP is always
# authoritative — see
# :func:`halka_arz_advisor.kap.extraction.apply_lower_authority_fallback`).
SourceSystem = Literal["kap", "issuer_ir"]


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where one observed value came from."""

    document_type: str  # e.g. "approved_prospectus" | "investor_sale_announcement"
    disclosure_id: str
    attachment_url: str
    page_number: int | None
    extraction_method: ExtractionMethod = "digital"
    source_system: SourceSystem = "kap"


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


# "22.07.2026 - 24.07.2026", "22.07.2026 ile 24.07.2026 tarihleri arasında".
#
# Anchor phrase confirmed live on 2026-08-08 against real 2026 investor
# sale announcements (QUICK, MASFN — OCR'd text, since both are scanned
# PDFs): the real heading used is "Halka Arz Süresi" ("Offering
# Duration"), e.g. QUICK's actual sentence — "Halka Arz Süresi: Halka
# arz edilecek olan ... paylar 19.01.2026 ile31.07.2026 tarihleri
# arasında 3 iş günü süreyle satışa sunulacaktır." — never "talep
# toplama" in either sample. "talep toplama" (this project's original,
# untested assumption) is kept as a second recognized anchor in case
# some other document phrases it that way, but is no longer the primary
# real-world match.
#
# The gap between the anchor and the date range spans a real line wrap
# in both live samples above ("...adet\nnama yazılı paylar
# 19.01.2026...") — unlike every other narrative-sentence pattern in
# this module (each confirmed only on a single visual line so far), so
# this is the one pattern here where the gap must cross newlines:
# ``[\s\S]`` (not ``[^\n]``) matches any character including ``\n``,
# scoped to just this pattern rather than changing this module's
# shared ``_re()`` flags.
_SUBSCRIPTION_DATE_ANCHOR = r"(?:halka\s+arz\s+suresi|talep\s+toplama)"
_SUBSCRIPTION_DATE_RANGE_RE = _re(rf"{_SUBSCRIPTION_DATE_ANCHOR}[\s\S]{{0,160}}?({_DATE})\s*(?:-|ile)\s*({_DATE})")

# Second, trailing-anchored form — confirmed live on 2026-08-08 against
# GOLDA's real 2026 announcement, where OCR dropped the "Halka Arz
# Süresi" heading text itself (only "...adet\nB Grubu hamiline yazılı
# paylar 01/07/2026 ile 02/07/2026 tarihleri arasında 2 iş günü süreyle
# \nsatışa sunulacaktır." survived — no heading at all before it), so
# the heading-anchored pattern above never gets a chance to match even
# though the date range itself OCR'd cleanly. Anchoring on the date
# range's own trailing grammar instead ("tarihleri arasında" then,
# within a further short gap, "satışa sunulacaktır") needs no heading.
# This trailing phrase combination is deliberately specific enough not
# to false-match an unrelated "DATE ile DATE tarihleri arasında" elsewhere
# in a long prospectus (confirmed against a real false-positive case in
# METEN's own 44-page prospectus — an EPDK electricity-tariff sentence,
# "06.01.2017 ile 01.03.2017 tarihleri arasında 500 TL/MWh olarak
# belirlenmiştir" — which has no nearby "satışa sunulacaktır" and so
# correctly does not match this pattern).
_SUBSCRIPTION_DATE_RANGE_TRAILING_RE = _re(
    rf"({_DATE})\s*(?:-|ile)\s*({_DATE})\s*tarihleri\s+arasinda[\s\S]{{0,80}}?satisa\s+sunulacaktir"
)

# --------------------------------------------------------------------------
# Subscription end date as *restated* in an official post-offer document
# (KAP "Halka Arzı Sonuçları" / IPO-results disclosure, or an issuer-IR
# copy of one) — cutoff-boundary evidence only, never a scored decision
# feature. See :func:`extract_subscription_end_date_from_result_text`'s
# own docstring for why this is deliberately kept out of FIELD_NAMES/
# ExtractedFacts entirely, unlike every extractor above.
#
# Confirmed live on 2026-08-08 against four real 2026 IPO-results
# disclosures (ALBTN, METEN, ORZAX, SOHOE) — each states the closing day
# of the subscription period as the last entry of a Turkish
# calendar-date list, immediately followed by "tarihleri arasında"/
# "tarihlerinde" and (nearby) "talep top-", e.g.:
#   ORZAX: "...halka arzında 29 - 30 Haziran, 1 Temmuz 2026 tarihleri
#           arasında talep toplanmıştır."      -> 2026-07-01
#   ALBTN: "...liderliğinde 22-23 Temmuz 2026 tarihlerinde Sabit
#           Fiyatla Talep Toplama ve Satış Yöntemi ile gerçekleşmiştir."
#                                                -> 2026-07-23
#   METEN: "...halka arzında 20-21-22 Temmuz 2026 tarihleri arasında
#           talep toplanmıştır."                -> 2026-07-22
#   SOHOE: "...ile 30 Haziran – 1 Temmuz 2026 tarihlerinde ... talep
#           toplanmıştır."                      -> 2026-07-01
#
# Each of these lists several days (sometimes spanning two different
# Turkish month names) before the final "<day> <ay adı> <yıl>" — this
# pattern deliberately captures only that *last* day/month/year triple
# (the one immediately adjacent to "tarihleri arasında"/"tarihlerinde"),
# which is always the period's closing date regardless of how many
# earlier days/months are listed before it; it never attempts to parse
# the full list or recover a start date.
_TURKISH_MONTHS: dict[str, int] = {
    "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
    "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12,
}
_TURKISH_MONTH_NAME_ALTERNATION = "|".join(_TURKISH_MONTHS)
_SUBSCRIPTION_RESULT_END_DATE_RE = _re(
    rf"(\d{{1,2}})\s+({_TURKISH_MONTH_NAME_ALTERNATION})\s+(\d{{4}})\s+tarihleri?\s*(?:arasinda|inde)"
    rf"[\s\S]{{0,120}}?talep\s+topla"
)


def parse_turkish_month_date(day_text: str, month_name: str, year_text: str) -> date | None:
    month = _TURKISH_MONTHS.get(fold_turkish(month_name).strip())
    if month is None:
        return None
    try:
        return date(int(year_text), month, int(day_text))
    except ValueError:
        return None


def extract_subscription_end_date_from_result_text(text: str) -> tuple[date, str] | None:
    """The subscription period's closing date, as explicitly *restated*
    in an official post-offer document (a KAP "Halka Arzı Sonuçları"
    IPO-results disclosure, or — per the same rule — an issuer-IR copy
    of one).

    Deliberately **not** part of :data:`FIELD_NAMES`/:class:`ExtractedFacts`/
    :func:`merge_field_observations`/:func:`extract_observations_from_pages`:
    unlike every other field in this module, nothing this function finds
    is ever meant to become a ``kap_extraction.*`` fact — that would risk
    it being read as evidence for the scored, mandatory, pre-offer
    ``subscription_window`` catalog feature (live or historical), which
    is exactly the leak this function exists to avoid. Callers (see
    :mod:`halka_arz_advisor.historical_dataset.post_offer_evidence`) use
    this only to establish where a historical decision cutoff falls —
    evaluation-boundary metadata, not a predictive feature — and must
    keep the result structurally separate from any
    :class:`ExtractedFacts` built for that same company.
    """
    folded = fold_turkish(text)
    match = _SUBSCRIPTION_RESULT_END_DATE_RE.search(folded)
    if not match:
        return None
    day_text = text[match.start(1) : match.end(1)]
    month_text = text[match.start(2) : match.end(2)]
    year_text = text[match.start(3) : match.end(3)]
    value = parse_turkish_month_date(day_text, month_text, year_text)
    if value is None:
        return None
    return value, text[match.start() : match.end()]


# "Bir payın nominal değeri 1 TL olup, 7,50 TL fiyattan satışa
# sunulacaktır." / "...1 TL olup 22,00 TL fiyattan satışa sunulacaktır."
# / "...1 TL olup, 46,00 TL'den satışa sunulacaktır." — confirmed live on
# 2026-08-08 against four real 2026 investor sale announcements (ATATR,
# EMPAE, MEYSU, NETCD): this fixed sentence, immediately after the
# "Halka Arz Süresi" date-range sentence (see
# _SUBSCRIPTION_DATE_RANGE_TRAILING_RE above), is where every one of them
# actually states the offer price — never "belirlenen X TL" or a
# "Halka Arz Fiyatı: X" label, which is why offering_price extracted in
# only 1/20 companies with a readable base document before this pattern
# existed (see docs/capability-audit-2026-08-08.md). Tried first, since
# it's the dominant real shape; group 1 is the per-share nominal (par)
# value, group 2 is the offer price itself. The gap between "olup" and
# the price is a lazy, unbounded-character (not digit-excluding) match:
# EMPAE's real sentence repeats the par value as an adjective clause in
# between ("...olup 1 TL nominal değerli paylar, 22,00 TL fiyattan
# satışa sunulacaktır"), so a gap that merely excludes digits would stop
# at that repeated "1" and never reach the real price. "TL'den satışa"
# (NETCD) has no "fiyat(tan)" at all, so that whole clause is optional.
_PRICE_PAR_VALUE_SENTENCE_RE = _re(
    rf"nominal\s+degeri\s+({_NUM})\s*tl\s+olup[\s\S]{{0,60}}?({_NUM})\s*tl['’]?(?:den|dan|ten|tan)?\s*(?:fiyat(?:tan)?\s+)?satisa\s+sunulacaktir"
)
# "Halka arz satış fiyatı olarak belirlenen 76,60 TL" — observed live in a
# real Fiyat Tespit Raporu.
_PRICE_NARRATIVE_RE = _re(rf"belirlenen\s+({_NUM})\s*tl")
# "Halka Arz Fiyatı (TL) : 76,60" / "Halka Arz Fiyatı: 76,60 TL" — a more
# direct label:value form, tried if the narrative sentence isn't found.
_PRICE_LABEL_RE = _re(rf"halka\s+arz\s+fiyati\s*\(?\s*tl\s*\)?\s*[:\-]?\s*({_NUM})")
# "Halka Arz Fiyatı   45,00" — the base prospectus's own "Sulanma Etkisi"
# (dilution-effect analysis) table row, no "TL" unit token nearby (the
# unit is only stated once, in the table's own column header) —
# confirmed live on 2026-08-08 against EKDMR's real, fully digital
# İzahname (section 29, "SULANMA ETKİSİ"). A distinct, independent
# source from the announcement's narrative sentence above (same field,
# cross-checked via the normal conflict-detection merge, never assumed
# to agree). Tried last since a bare "Halka Arz Fiyatı <number>" with no
# unit token is a weaker anchor than the two patterns above.
_PRICE_DILUTION_TABLE_RE = _re(rf"halka\s+arz\s+fiyati\s+({_NUM})(?!\s*%)")

# Controlled vocabulary of SPK-defined IPO distribution methods — checked
# in this order (first match wins, most-specific first). Each also has an
# "X ile Y" spacing variant (confirmed live for "sabit fiyat ile talep
# toplama" — EKDMR's prospectus states it this way twice, e.g. "'Sabit
# Fiyat ile Talep Toplama' ve 'En İyi Gayret Aracılığı' yöntemi ile
# gerçekleştirilecektir" — distinct from, and not matched by, "sabit
# fiyatla talep toplama").
_DISTRIBUTION_METHODS: tuple[str, ...] = (
    "fiyat araligi ile talep toplama",
    "sabit fiyatla talep toplama",
    "sabit fiyat ile talep toplama",
    "degisken fiyatla talep toplama",
    "degisken fiyat ile talep toplama",
    "borsada satis yontemi",
    "borsada satis",
)

# "artırılacak 2.380.000.000 TL nominal değerli" — the capital-increase
# nominal amount, observed live in a real prospectus.
_CAPITAL_INCREASE_RE = _re(rf"artirilacak\s+({_NUM})\s*tl\s+nominal\s+degerli")
# "...nedeniyle artırılacak 50.000.000 TL (ve mevcut ortakların sahip
# olduğu 10.000.000 TL olmak üzere toplam 60.000.000 TL) nominal
# değerli..." — confirmed live on 2026-08-08 against UCAYM's real 2026
# announcement: when a secondary sale is aggregated into the same
# clause rather than restated per-component, "nominal değerli" no
# longer immediately follows the capital-increase amount (a whole
# parenthetical intervenes), so _CAPITAL_INCREASE_RE above never
# matches. Anchored on the same "çıkarılması nedeniyle artırılacak"
# phrase _CAPITAL_AMOUNTS_RE already relies on, this fallback only
# requires the number to be immediately followed by "TL" — not by
# "nominal değerli" too — since "nedeniyle artırılacak <N> TL" always
# names the newly issued nominal amount in every real sample checked
# (EKDMR, ATATR, EMPAE, NETCD, UCAYM, MEYSU).
_CAPITAL_INCREASE_AFTER_AMOUNTS_RE = _re(rf"cikarilmasi\s+nedeniyle\s+artirilacak\s+({_NUM})\s*tl")
# "%170 oranında" / "% 25,03" near a capital-increase context.
_CAPITAL_INCREASE_RATIO_RE = _re(rf"%\s*({_NUM})\s+oraninda[^\n]{{0,60}}artir")

# "Ortaklığımızın çıkarılmış sermayesinin 201.000.000 TL'den 250.000.000
# TL'ye çıkarılması" — confirmed live on 2026-08-08 against all 9 of the
# real 2026 investor sale announcements sampled (ALBTN, EKDMR, GOLDA,
# MASFN, METEN, ORZAX, QUICK, SARAE, SOHOE): every one states the
# capital increase as this exact before/after absolute-TL-amount pair,
# never as an explicit percentage — so _CAPITAL_INCREASE_RATIO_RE above
# (which requires a stated "% ... oranında") essentially never matches
# any of them live. Used as a fallback: the ratio is computed from the
# two stated amounts, (new - old) / old * 100, rather than searched for
# as an already-stated number. Group 1 is the pre-offer capital, group 2
# the post-offer capital — also reused directly by
# extract_pre_offer_capital/extract_post_offer_capital below.
_CAPITAL_AMOUNTS_RE = _re(rf"sermayesinin\s+({_NUM})\s*tl.{{0,3}}den\s+({_NUM})\s*tl.{{0,3}}ye\s+cikarilmasi")

# "Ödenmiş Sermaye 280.000.000 320.000.000" — the base prospectus's own
# "Sulanma Etkisi" table restates the same pre/post paid-in capital pair
# as a "Halka Arz Öncesi" / "Halka Arz Sonrası" table row (confirmed
# live against EKDMR's real İzahname, where it agrees exactly with
# _CAPITAL_AMOUNTS_RE's own reading of the same company's announcement:
# 280.000.000 -> 320.000.000 both ways) — a second, independent source
# for the same fact, used as a fallback when the narrative sentence
# isn't found (e.g. a scanned/OCR'd document where the table survived
# better than the sentence, or vice versa).
_ODENMIS_SERMAYE_TABLE_RE = _re(rf"odenmis\s+sermaye\s+({_NUM})\s+({_NUM})")

# "Bir payın nominal değeri 1 TL olup" / "beher payın nominal değeri 1
# TL olup" — the per-share par value, stated in the same sentence as
# the offer price itself (see _PRICE_PAR_VALUE_SENTENCE_RE above);
# extracted as its own field since converting a nominal-TL capital
# amount into a share count requires dividing by this value, never
# assuming it's 1 without checking (every real sample seen states 1
# TL, but nothing here hardcodes that).
#
# Requires an explicit per-share qualifier ("bir payın"/"beher
# payın"/"her bir payın"/bare "payın") immediately before "nominal
# değeri" — confirmed live (2026-08-08) against a real, distinct
# EKDMR İzahname bug this narrower anchor fixes: a *different*, earlier
# real sentence in the same document, "Halka arz edilecek payların
# toplam nominal değeri 52.000.000 TL olup; bu payların 40.000.000 TL
# nominal değerli kısmı sermaye artırımı suretiyle...", states the
# *aggregate* nominal value of the whole offered-share block (plural
# "payların", "toplam" = total) in the exact same "nominal değeri <N>
# TL olup" shape the old, unanchored pattern accepted — silently
# overriding the real per-share value (1 TL, from the same document's
# own "beher payın nominal değeri 1 TL olup" a few pages later) because
# this field is a PROSPECTUS_PRIORITY_FIELDS entry, so no cross-
# document conflict with the announcement's correct "1 TL" was ever
# surfaced either. The resulting ~52,000,000x error propagated into
# post_offer_share_count/implied_post_money_market_cap (kap.offering_terms)
# and, from there, into kap.valuation's implied market cap.
#
# "olu p" (a stray inserted space before the final letter) tolerated in
# "olup" — confirmed live in this exact same EKDMR İzahname page (196):
# "beher payın nominal değeri 1 TL olu p, Şirket'in..." is otherwise the
# *only* clean per-share-qualified occurrence in the whole 269-page
# document (the announcement's own clean "Bir payın nominal değeri 1 TL
# olup" restates the same fact, but EKDMR's investor_sale_announcement
# happens not to be in this project's currently matched/backfilled
# disclosure set — a separate, real document-acquisition gap, not an
# extraction one) — the same category of PDF-glyph-spacing artifact
# already handled elsewhere in this project (see kap.financials's own
# "ç"/"ı" glyph-substitution notes).
_PAR_VALUE_RE = _re(rf"(?:bir|beher|her\s+bir)?\s*payin\s+nominal\s+degeri\s+({_NUM})\s*tl\s+olu\s*p\b")

# "mevcut ortak Tan Turizm ...'nin sahip olduğu 40.000.000 TL nominal
# değerli" (single named seller) / "mevcut ortaklardan Gülsan Gıda
# ...'ye ait 55.000.000 TL nominal değerli" / "mevcut ortak Vural
# AKMAN'a ait 3.864.000 TL nominal değerli" (per-seller, EMPAE-style) /
# "mevcut ortakların sahip olduğu 10.000.000 TL" (aggregated, UCAYM-style)
# — confirmed live on 2026-08-08 against six real 2026 documents
# (EKDMR, ATATR, EMPAE, MEYSU, NETCD, UCAYM): every one uses "mevcut
# ortak(lar)... sahip olduğu"/"...'a/'ya/'ye ait", never the literal
# phrase "ortak satışı" the previous pattern searched for (which never
# matched a single one of them) — this is the confirmed root cause of
# secondary_sale_shares' near-zero extraction rate.
#
# A document can restate a *single* named seller (ATATR, MEYSU, UCAYM's
# aggregate form) — safe to extract directly — or list *several*
# individually named sellers, each with their own amount (EMPAE: 8
# sellers, EKDMR: 10) with no single combined figure stated anywhere;
# summing per-seller matches blindly would risk double-counting (the
# same paragraph is restated verbatim later on the same page, in every
# sample seen, as part of the standard "sorumluluk" disclaimer repeat).
# So the seller pattern is only searched for within the single bounded
# region between the capital-increase clause and its own closing
# "...olmak üzere toplam" (never across the whole page) — one seller
# match there is accepted; zero means no secondary sale in this
# region (correctly not_found, not a false zero — see
# kap.offering_terms's total_offered − capital_increase fallback for
# the genuine no-value case); more than one is a real multi-seller
# document this function deliberately does not attempt to sum — see
# build_offering_terms's total_offered − capital_increase derivation
# (verified live against EMPAE: 9,000,000 = 38,000,000 − 29,000,000,
# matching the sum of its 8 sellers exactly; and EKDMR: 12,000,000 =
# 52,000,000 − 40,000,000, matching the sum of its 10 sellers exactly),
# which needs no seller-level parsing at all.
_SECONDARY_SALE_REGION_RE = _re(rf"cikarilmasi\s+nedeniyle\s+artirilacak[\s\S]{{0,2000}}?uzere\s+toplam")
_SECONDARY_SALE_SELLER_RE = _re(rf"mevcut\s+ortak\w*[^\n]{{0,200}}?(?:sahip\s+oldugu|ait)\s+({_NUM})\s*tl")
_SECONDARY_SALE_RATIO_RE = _re(rf"ortak\s+satisi[^\n]{{0,60}}?%\s*({_NUM})")

# "satışa sunulan toplam ... TL" / "toplam ... TL nominal değerli
# paylarının halka arzı" / "...toplam 60.000.000 TL) nominal değerli"
# (UCAYM's real wording has a stray ")" between the amount and "nominal
# değerli" when the secondary sale is aggregated into the same clause —
# confirmed live 2026-08-08 — hence the optional ``\)?`` below) — the
# combined total offered amount.
_TOTAL_OFFERED_RE = _re(rf"toplam\s+({_NUM})\s*tl\)?\s+nominal\s+degerli")

# "Halka arz edilecek toplam 52.000.000 TL nominal değerli payların;
# • 20.800.000 TL nominal değerdeki kısmı (40%) Yurt İçi Bireysel
#   Yatırımcılara, • 5.200.000 TL nominal değerdeki kısmı (10%) Yüksek
#   Talepte Bulunacak Yatırımcı Grubu'na, • 15.600.000 TL nominal
#   değerdeki kısmı (30%) Yurt İçi Kurumsal Yatırımcılara, • 10.400.000
#   TL nominal değerdeki kısmı (20%) Yurt Dışı Kurumsal Yatırımcılara
#   gerçekleştirilecek satışlar için tahsis edilmiştir." — confirmed
# live on 2026-08-08 against EKDMR's real İzahname, §25.2.3(a)
# "Yatırımcı grubu bazında tahsisat oranları" (a numbered heading from
# SPK's own standard İzahname template, not free narrative text — the
# regulatory citation immediately above it, "II-5.2 sayılı Sermaye
# Piyasası Araçlarının Satışı Tebliği", confirms this is a fixed,
# closed vocabulary of investor-group names, not something a per-issuer
# rule would need to vary). Each bullet is
# "<AMOUNT> TL nominal değerdeki kısmı (<PCT>%) <GROUP NAME>[suffix],".
_ALLOCATION_LINE_RE = _re(
    rf"({_NUM})\s*tl\s+nominal\s+degerdeki\s+kismi\s*\(?\s*({_NUM})\s*%\)?\s+([^,.\n]+?)(?:['’](?:a|e|ya|ye|na|ne)|dan|den)?\s*[,.]"
)
_INVESTOR_GROUP_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("yurt ici bireysel", "retail"),
    ("bireysel yatirimci", "retail"),
    ("yuksek talepte bulunacak", "high_demand"),
    ("yurt ici kurumsal", "domestic_institutional"),
    ("yurt disi kurumsal", "foreign_institutional"),
)

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
    match = _SUBSCRIPTION_DATE_RANGE_RE.search(folded) or _SUBSCRIPTION_DATE_RANGE_TRAILING_RE.search(folded)
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
    found = (
        _search(folded, text, _PRICE_PAR_VALUE_SENTENCE_RE, group=2)
        or _search(folded, text, _PRICE_NARRATIVE_RE)
        or _search(folded, text, _PRICE_LABEL_RE)
        or _search(folded, text, _PRICE_DILUTION_TABLE_RE)
    )
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_par_value_per_share(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _PAR_VALUE_RE)
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
    found = _search(folded, text, _CAPITAL_INCREASE_RE) or _search(folded, text, _CAPITAL_INCREASE_AFTER_AMOUNTS_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_pre_offer_capital(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _CAPITAL_AMOUNTS_RE, group=1) or _search(folded, text, _ODENMIS_SERMAYE_TABLE_RE, group=1)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_post_offer_capital(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _CAPITAL_AMOUNTS_RE, group=2) or _search(folded, text, _ODENMIS_SERMAYE_TABLE_RE, group=2)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_capital_increase_ratio(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _CAPITAL_INCREASE_RATIO_RE)
    if found:
        value_text, snippet = found
        value = parse_turkish_number(value_text)
        if value is not None:
            return value, snippet

    match = _CAPITAL_AMOUNTS_RE.search(folded)
    if not match:
        return None
    old_value = parse_turkish_number(text[match.start(1) : match.end(1)])
    new_value = parse_turkish_number(text[match.start(2) : match.end(2)])
    if old_value is None or new_value is None or old_value == 0:
        return None
    ratio = (new_value - old_value) / old_value * 100.0
    return ratio, text[match.start() : match.end()]


def extract_secondary_sale_shares(text: str) -> tuple[float, str] | None:
    """See :data:`_SECONDARY_SALE_REGION_RE`'s docstring: only extracts
    when exactly one named/aggregated seller is stated within the
    capital-increase clause's own region — a real multi-seller document
    correctly returns ``None`` here rather than a wrong partial sum."""
    folded = fold_turkish(text)
    region_match = _SECONDARY_SALE_REGION_RE.search(folded)
    if region_match is None:
        return None
    region_folded = folded[region_match.start() : region_match.end()]
    region_original = text[region_match.start() : region_match.end()]
    sellers = list(_SECONDARY_SALE_SELLER_RE.finditer(region_folded))
    if len(sellers) != 1:
        return None
    match = sellers[0]
    value = parse_turkish_number(region_original[match.start(1) : match.end(1)])
    return (value, region_original[match.start() : match.end()]) if value is not None else None


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


InvestorGroup = Literal["retail", "high_demand", "domestic_institutional", "foreign_institutional", "other"]


@dataclass(frozen=True, slots=True)
class AllocationLineItem:
    """One bullet of a prospectus's investor-group tahsisat table (see
    :data:`_ALLOCATION_LINE_RE`) — ``group`` is this project's own
    normalized, closed-vocabulary classification of ``group_label_raw``
    (the verbatim Turkish group name), never invented for a label that
    doesn't match a known SPK-defined group."""

    group: InvestorGroup
    group_label_raw: str
    amount_try: float | None
    percentage: float | None


def _classify_investor_group(folded_label: str) -> InvestorGroup:
    for keyword, group in _INVESTOR_GROUP_KEYWORDS:
        if keyword in folded_label:
            return group  # type: ignore[return-value]
    return "other"


def extract_investor_group_allocations(text: str) -> list[tuple[AllocationLineItem, str]] | None:
    """Every ``"<AMOUNT> TL nominal değerdeki kısmı (<PCT>%) <GROUP>"``
    bullet found (see :data:`_ALLOCATION_LINE_RE`'s docstring for the
    real table shape this matches) — every bullet in the document is
    kept, not just the first, since the whole point is the full
    per-group breakdown."""
    folded = fold_turkish(text)
    items: list[tuple[AllocationLineItem, str]] = []
    for match in _ALLOCATION_LINE_RE.finditer(folded):
        amount = parse_turkish_number(text[match.start(1) : match.end(1)])
        percentage = parse_turkish_number(text[match.start(2) : match.end(2)])
        label_raw = text[match.start(3) : match.end(3)].strip()
        group = _classify_investor_group(fold_turkish(label_raw))
        snippet = text[match.start() : match.end()]
        items.append((AllocationLineItem(group=group, group_label_raw=label_raw, amount_try=amount, percentage=percentage), snippet))
        if len(items) >= 10:
            break
    return items or None


# "Yurt İçi Bireysel Yatırımcılara Dağıtım: Eşit Dağıtım Yöntemine göre
# yapılacaktır." / "Grup Çalışanları'na Dağıtım:  Eşit Dağıtım
# Yöntemine göre yapılacaktır." / "Yüksek Talepte Bulunacak Yatırımcı
# Grubu(na) Dağıtım:  Oransal Dağıtım Yöntemine göre yapılacaktır." —
# confirmed live on 2026-08-08 against real İzahname text (EKDMR's own
# base document, plus two further large base İzahname documents cached
# this session): each investor group's own within-tranche distribution
# rule is stated as a "<GROUP> Dağıtım: <Yöntem> Dağıtım Yöntemine göre
# yapılacaktır." sentence, immediately below the same §25.2.3 tahsisat
# section :data:`_ALLOCATION_LINE_RE` reads — "Eşit Dağıtım" (equal:
# split evenly per investor, used for the retail/"Grup Çalışanları"
# tranches in every sample) or "Oransal Dağıtım" (pro-rata by requested
# amount, used for the "Yüksek Talepte Bulunacak Yatırımcı Grubu"
# tranche in every sample). The institutional tranches use neither
# word — their real wording is a negotiated/discretionary process
# ("Her bir Yurt İçi Kurumsal Yatırımcıya verilecek pay miktarına ...
# ile görüşülerek belirlenecektir") in free narrative text, not this
# fixed sentence shape — deliberately not matched here, never
# force-classified as equal or proportional.
_DISTRIBUTION_RULE_LINE_RE = _re(r"([^:\n]{4,70}?)\s*dagitim\s*:\s*(esit|oransal)\s+dagitim\s+yontemine")

DistributionRuleMethod = Literal["equal", "proportional"]
_DISTRIBUTION_RULE_METHOD_MAP: dict[str, DistributionRuleMethod] = {"esit": "equal", "oransal": "proportional"}


@dataclass(frozen=True, slots=True)
class DistributionRuleLineItem:
    """One investor group's own within-tranche distribution rule (see
    :data:`_DISTRIBUTION_RULE_LINE_RE`) — ``group`` reuses the same
    closed-vocabulary classification :class:`AllocationLineItem` uses,
    never invented for an unrecognized label."""

    group: InvestorGroup
    group_label_raw: str
    method: DistributionRuleMethod


def extract_investor_group_distribution_rules(text: str) -> list[tuple[DistributionRuleLineItem, str]] | None:
    """Every ``"<GROUP> Dağıtım: <Eşit|Oransal> Dağıtım Yöntemine göre
    yapılacaktır"`` sentence found (see :data:`_DISTRIBUTION_RULE_LINE_RE`'s
    docstring) — every group stated in the document is kept, not just
    the first."""
    folded = fold_turkish(text)
    items: list[tuple[DistributionRuleLineItem, str]] = []
    for match in _DISTRIBUTION_RULE_LINE_RE.finditer(folded):
        label_raw = text[match.start(1) : match.end(1)].strip(" -•\t")
        group = _classify_investor_group(fold_turkish(label_raw))
        method = _DISTRIBUTION_RULE_METHOD_MAP[match.group(2)]
        snippet = text[match.start() : match.end()]
        items.append((DistributionRuleLineItem(group=group, group_label_raw=label_raw, method=method), snippet))
        if len(items) >= 10:
            break
    return items or None


# "II-5.2 sayılı Sermaye Piyasası Araçlarının Satışı Tebliği'nin
# 18'inci maddesinin dördüncü fıkrası hükmü uyarınca ..." — confirmed
# live on 2026-08-08 against three real İzahname's own §25.2.3(a)
# "Yatırımcı grubu bazında tahsisat oranları" section, each citing the
# exact same communiqué number ("II-5.2") as the regulatory basis for
# the allocation-group minimums that section states. The communiqué
# *number* itself is captured generically (not hardcoded as "II-5.2"),
# in case a future filing cites a different or superseding communiqué
# — this is a deterministic citation lookup, not a fixed constant.
_DISTRIBUTION_REGULATION_RE = _re(r"([\w./\-]+)\s+sayili\s+sermaye\s+piyasasi\s+araclarinin\s+satisi\s+tebligi")


def extract_distribution_regulation_reference(text: str) -> tuple[str, str] | None:
    folded = fold_turkish(text)
    return _search(folded, text, _DISTRIBUTION_REGULATION_RE)


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
# IPO results ("Halka Arzına İlişkin Sonuçlar") — post-offer fields.
#
# Patterns below are confirmed against two real, independently-brokered
# results filings (QUICK/Garanti Yatırım, MASFN/Deniz Yatırım) — the
# narrative sentences are worded near-identically across both ("payların
# N katına denk gelen ... talebi gelmiştir", "Bireysel Yatırımcılara
# (planlanan) tahsisat tutarının (yaklaşık) N katı"), which is why those
# fields are matched from narrative text.
#
# retail_participant_count/retail_allocated_shares/
# institutional_allocated_shares instead come from the per-investor-group
# results table. That table's *column labels* are not standardized
# across brokerages (QUICK: "Yatırımcı Sayısı" / "Nominal Değer (TL)";
# MASFN: "Başvuru Sayısı" / "Pay Adedi" / "Talep Edilen Nominal Tutar
# (TL)") but pypdf's linear text extraction of both real tables reduces
# each investor-group row to the *same* fixed shape once the group label
# is matched: 8 numeric/percent tokens in "Planlanan Tahsisat, Talep,
# Dağıtım" order. Position 2 (the row's 3rd token) was confirmed against
# QUICK's own narrative ("toplam 589.692 yatırımcıdan") to equal the
# *combined* investor headcount across every row, and independently
# against MASFN's table arithmetic (1.120.538 + 4.235 + 72 = 1.124.845,
# the "Toplam" row's own position-2 value) — so it's each row's
# investor/application headcount, not a guess. Position 6 (the 7th
# token, immediately before the row's closing percentage) is the
# "Dağıtım" (final allocated) nominal amount in both samples. A results
# table with a different row shape (a different number of tokens between
# the group label and the next one) simply won't match — never guessed.
_TABLE_TOKEN = r"[%\d][\d.,%]*"


def extract_total_participant_count(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _re(rf"toplam\s+({_NUM})\s+yatirimcidan"))
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_total_demand_multiple(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _re(rf"({_NUM})\s*katina\s+denk\s+gelen"))
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_retail_demand_multiple(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(
        folded,
        text,
        _re(rf"bireysel\s+yatirimcilara\s+(?:planlanan\s+)?tahsisat\s+tutarinin\s+(?:yaklasik\s+)?({_NUM})\s*kati"),
    )
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def _extract_investor_group_row(text: str, folded_group_label: str) -> tuple[float | None, float | None, str] | None:
    """Match one investor-group row in the results table; returns
    ``(participant_count, allocated_shares, snippet)`` — either count may
    individually be ``None`` if its token didn't parse as a number, but
    the row match itself (the label followed by exactly this token
    shape) either succeeds or doesn't."""
    folded = fold_turkish(text)
    pattern = re.compile(
        rf"{folded_group_label}\s+yatirimcilar\w*\s+{_TABLE_TOKEN}\s+{_TABLE_TOKEN}\s+({_NUM})\s+"
        rf"{_TABLE_TOKEN}\s+{_TABLE_TOKEN}\s+{_TABLE_TOKEN}\s+({_NUM})\s+{_TABLE_TOKEN}",
        re.IGNORECASE,
    )
    match = pattern.search(folded)
    if not match:
        return None
    snippet = text[match.start() : match.end()]
    participant_count = parse_turkish_number(text[match.start(1) : match.end(1)])
    allocated_shares = parse_turkish_number(text[match.start(2) : match.end(2)])
    return participant_count, allocated_shares, snippet


def extract_retail_participant_count(text: str) -> tuple[float, str] | None:
    row = _extract_investor_group_row(text, "bireysel")
    if row is None or row[0] is None:
        return None
    return row[0], row[2]


def extract_retail_allocated_shares(text: str) -> tuple[float, str] | None:
    row = _extract_investor_group_row(text, "bireysel")
    if row is None or row[1] is None:
        return None
    return row[1], row[2]


def extract_institutional_allocated_shares(text: str) -> tuple[float, str] | None:
    row = _extract_investor_group_row(text, "kurumsal")
    if row is None or row[1] is None:
        return None
    return row[1], row[2]


# --------------------------------------------------------------------------
# Price determination report ("Fiyat Tespit Raporu") — valuation summary.
#
# Patterns confirmed against two real, independently-brokered reports
# (QUICK/Garanti Yatırım, a sum-of-parts insurance holding valuation;
# METEN/İnfo Yatırım, a single-business EV/EBITDA valuation). Only values
# stated explicitly in the report's own summary tables/sentences are
# matched — no multiple is computed, no peer is chosen, no DCF input is
# read. Some fields are genuinely absent from a given report's summary
# (e.g. QUICK never states a P/E or EV/EBITDA at the consolidated level,
# since it's a sum-of-parts valuation) and correctly extract as not_found.
#
# "Halka arz satış fiyatı olarak belirlenen 76,60 TL, hesaplanan pay
# başına fiyat olan 102,17 TL'ye göre %25,03 iskontoludur." (QUICK) and
# "Halka Arz İskontosu -20%" (METEN) are the two headline-discount forms
# observed; tried in that order.
_DISCOUNT_NARRATIVE_RE = _re(rf"gore\s+%\s*({_NUM})\s*iskontoludur")
_DISCOUNT_LABEL_RE = _re(rf"iskontosu\s*-?({_NUM})%")

# "Nihai Değer 236,2" (METEN p.8) — the cleanest single statement of the
# final post-discount market cap, tried first.
_NIHAI_DEGER_RE = _re(rf"nihai\s+deger\s+({_NUM})")
# "Piyasa Değeri 236,2 Net Borç 106,6 Firma Değeri 342,8" (METEN p.6) — a
# report may restate this triple once per balance-sheet date it
# considered (an earlier provisional one, then a final one); only the
# LAST occurrence is used, confirmed against the surrounding "Bilanço
# (m$) 2025/09" -> "2026/03" date labels and against page 8's
# independent "Nihai Değer" statement (identical value).
_TRIPLE_RE = _re(rf"piyasa\s+degeri\s+({_NUM})\s+net\s+borc\s+({_NUM})\s+firma\s+degeri\s+({_NUM})")

# "F/K 29,4" (METEN p.8); negative lookbehind defensively excludes an
# "EV/K"-shaped false positive (not observed live, but not a real ratio
# either way).
_PE_RE = _re(rf"(?<!ev\s)f\s*/\s*k\s+({_NUM})")
_EV_EBITDA_RE = _re(rf"ev\s*/\s*ebitda\s+({_NUM})")
# Deliberately narrow: must not match "EV/Net Satış" (EV/Sales, a
# different ratio) — both real samples only ever state EV/Sales, never
# P/S, so this correctly extracts not_found for both.
_PS_RE = _re(rf"(?:f|pd)\s*/\s*s\s+({_NUM})")
_PB_RE = _re(rf"pd\s*/\s*dd\s+({_NUM})")


def extract_headline_discount_percentage(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _DISCOUNT_NARRATIVE_RE) or _search(folded, text, _DISCOUNT_LABEL_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def _find_valuation_triple_last(folded: str, text: str) -> tuple[str, str, str, str] | None:
    """The LAST ``(market_cap_text, net_debt_text, enterprise_value_text,
    snippet)`` match of :data:`_TRIPLE_RE`, or ``None``."""
    matches = list(_TRIPLE_RE.finditer(folded))
    if not matches:
        return None
    match = matches[-1]
    snippet = text[match.start() : match.end()]
    return (
        text[match.start(1) : match.end(1)],
        text[match.start(2) : match.end(2)],
        text[match.start(3) : match.end(3)],
        snippet,
    )


def extract_reported_post_money_market_cap(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _NIHAI_DEGER_RE)
    if found:
        value_text, snippet = found
        value = parse_turkish_number(value_text)
        if value is not None:
            return value, snippet
    triple = _find_valuation_triple_last(folded, text)
    if triple is None:
        return None
    market_cap_text, _net_debt_text, _ev_text, snippet = triple
    value = parse_turkish_number(market_cap_text)
    return (value, snippet) if value is not None else None


def extract_reported_enterprise_value(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    triple = _find_valuation_triple_last(folded, text)
    if triple is None:
        return None
    _market_cap_text, _net_debt_text, ev_text, snippet = triple
    value = parse_turkish_number(ev_text)
    return (value, snippet) if value is not None else None


def extract_reported_net_debt(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    triple = _find_valuation_triple_last(folded, text)
    if triple is None:
        return None
    _market_cap_text, net_debt_text, _ev_text, snippet = triple
    value = parse_turkish_number(net_debt_text)
    return (value, snippet) if value is not None else None


def extract_reported_pe(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _PE_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_reported_ev_ebitda(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _EV_EBITDA_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_reported_ps(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _PS_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


def extract_reported_pb(text: str) -> tuple[float, str] | None:
    folded = fold_turkish(text)
    found = _search(folded, text, _PB_RE)
    if not found:
        return None
    value_text, snippet = found
    value = parse_turkish_number(value_text)
    return (value, snippet) if value is not None else None


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
    ("par_value_per_share", extract_par_value_per_share),
    ("pre_offer_capital", extract_pre_offer_capital),
    ("post_offer_capital", extract_post_offer_capital),
    ("distribution_regulation_reference", extract_distribution_regulation_reference),
    ("total_participant_count", extract_total_participant_count),
    ("retail_participant_count", extract_retail_participant_count),
    ("total_demand_multiple", extract_total_demand_multiple),
    ("retail_demand_multiple", extract_retail_demand_multiple),
    ("retail_allocated_shares", extract_retail_allocated_shares),
    ("institutional_allocated_shares", extract_institutional_allocated_shares),
    ("reported_post_money_market_cap", extract_reported_post_money_market_cap),
    ("reported_enterprise_value", extract_reported_enterprise_value),
    ("reported_net_debt", extract_reported_net_debt),
    ("reported_pe", extract_reported_pe),
    ("reported_ev_ebitda", extract_reported_ev_ebitda),
    ("reported_ps", extract_reported_ps),
    ("reported_pb", extract_reported_pb),
    ("headline_discount_percentage", extract_headline_discount_percentage),
)

_LIST_EXTRACTORS: tuple[tuple[str, Callable[[str], list[tuple[object, str]] | None]], ...] = (
    ("use_of_proceeds", extract_use_of_proceeds),
    ("key_risk_items", extract_key_risk_items),
    ("investor_group_allocations", extract_investor_group_allocations),
    ("investor_group_distribution_rules", extract_investor_group_distribution_rules),
)


def extract_observations_from_pages(
    pages: Sequence[PdfPage],
    *,
    document_type: str,
    disclosure_id: str,
    attachment_url: str,
    extraction_method: ExtractionMethod = "digital",
    source_system: SourceSystem = "kap",
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
                source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method, source_system),
            )
        if end and "subscription_end_date" not in observations:
            value, snippet = end
            observations["subscription_end_date"] = FieldObservation(
                value=value,
                raw_snippet=snippet,
                source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method, source_system),
            )

    for field_name, extractor in _SCALAR_EXTRACTORS:
        for page in pages:
            found = extractor(page.text)
            if found:
                value, snippet = found
                observations[field_name] = FieldObservation(
                    value=value,
                    raw_snippet=snippet,
                    source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method, source_system),
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
                    source=SourceRef(document_type, disclosure_id, attachment_url, page.number, extraction_method, source_system),
                )
                break

    return observations


def merge_field_observations(
    field_name: str,
    prospectus_observation: FieldObservation | None,
    announcement_observation: FieldObservation | None,
    ipo_results_observation: FieldObservation | None = None,
    price_determination_report_observation: FieldObservation | None = None,
) -> ExtractedFact:
    """Combine what each source document said about one field into a
    single :class:`ExtractedFact`.

    - None have it -> ``not_found``.
    - Exactly one has it -> that one, ``extracted``.
    - More than one has it and they **agree** -> ``extracted``, using
      whichever document rule 8 of the brief prefers for this field
      (only meaningful when both the prospectus and the announcement
      have it — ``ipo_results_observation``/``price_determination_report_observation``
      have no such priority rule, since they never overlap with the
      other sources in practice: each source's own fields are never
      extracted from any other document type), but keeping *every*
      observation for provenance.
    - More than one has it and they **disagree** -> ``conflicting``; no
      value is silently picked, every observation is kept.
    """
    observations = tuple(
        obs
        for obs in (
            prospectus_observation,
            announcement_observation,
            ipo_results_observation,
            price_determination_report_observation,
        )
        if obs is not None
    )
    if not observations:
        return _not_found()

    if len(observations) == 1:
        single = observations[0]
        return ExtractedFact(
            status="extracted", value=single.value, raw_snippet=single.raw_snippet, source=single.source,
            observations=observations,
        )

    # Compared with == (not a set/hash) since a value can be an
    # unhashable list (use_of_proceeds/key_risk_items).
    if any(obs.value != observations[0].value for obs in observations[1:]):
        return ExtractedFact(status="conflicting", value=None, raw_snippet=None, source=None, observations=observations)

    # Every present observation agrees on the value.
    if announcement_observation is not None and field_name in ANNOUNCEMENT_PRIORITY_FIELDS:
        winner = announcement_observation
    elif prospectus_observation is not None:
        winner = prospectus_observation
    else:
        winner = observations[0]
    return ExtractedFact(
        status="extracted", value=winner.value, raw_snippet=winner.raw_snippet, source=winner.source,
        observations=observations,
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
    par_value_per_share: ExtractedFact
    pre_offer_capital: ExtractedFact
    post_offer_capital: ExtractedFact
    investor_group_allocations: ExtractedFact
    investor_group_distribution_rules: ExtractedFact
    distribution_regulation_reference: ExtractedFact
    use_of_proceeds: ExtractedFact
    key_risk_items: ExtractedFact
    total_participant_count: ExtractedFact
    retail_participant_count: ExtractedFact
    total_demand_multiple: ExtractedFact
    retail_demand_multiple: ExtractedFact
    retail_allocated_shares: ExtractedFact
    institutional_allocated_shares: ExtractedFact
    reported_post_money_market_cap: ExtractedFact
    reported_enterprise_value: ExtractedFact
    reported_net_debt: ExtractedFact
    reported_pe: ExtractedFact
    reported_ev_ebitda: ExtractedFact
    reported_ps: ExtractedFact
    reported_pb: ExtractedFact
    headline_discount_percentage: ExtractedFact

    def as_dict(self) -> dict[str, ExtractedFact]:
        return {name: getattr(self, name) for name in FIELD_NAMES}


def build_extracted_facts(
    prospectus_observations: dict[str, FieldObservation] | None,
    announcement_observations: dict[str, FieldObservation] | None,
    ipo_results_observations: dict[str, FieldObservation] | None = None,
    price_determination_report_observations: dict[str, FieldObservation] | None = None,
) -> ExtractedFacts:
    """Merge one document's worth of prospectus observations, one
    document's worth of announcement observations, and (optional) one
    document's worth each of IPO results and price determination report
    observations into the final, per-field :class:`ExtractedFacts` (see
    :func:`merge_field_observations`)."""
    prospectus_observations = prospectus_observations or {}
    announcement_observations = announcement_observations or {}
    ipo_results_observations = ipo_results_observations or {}
    price_determination_report_observations = price_determination_report_observations or {}
    merged = {
        field_name: merge_field_observations(
            field_name,
            prospectus_observations.get(field_name),
            announcement_observations.get(field_name),
            ipo_results_observations.get(field_name),
            price_determination_report_observations.get(field_name),
        )
        for field_name in FIELD_NAMES
    }
    return ExtractedFacts(**merged)


def apply_lower_authority_fallback(primary: ExtractedFacts | None, fallback: ExtractedFacts | None) -> ExtractedFacts | None:
    """Combine two already-built :class:`ExtractedFacts` — typically
    KAP's own (``primary``) and a lower-authority source's (``fallback``,
    e.g. :mod:`halka_arz_advisor.issuer_ir`) — field by field, with
    ``primary`` always winning.

    A field only ever comes from ``fallback`` when ``primary`` has
    genuinely nothing for it (``status == "not_found"``) — a ``primary``
    field that's ``"extracted"`` *or* ``"conflicting"`` is returned
    completely unchanged, so a fallback source can never silently
    override, "resolve", or get averaged into an authoritative or
    already-flagged-conflicting value. Once ``primary`` gains a value
    for a field a fallback previously filled, ``primary`` takes back
    over automatically on the next call — there's no separate "locked
    in" fallback state to update.
    """
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    merged = {
        field_name: (primary_fact if (primary_fact := getattr(primary, field_name)).status != "not_found" else getattr(fallback, field_name))
        for field_name in FIELD_NAMES
    }
    return ExtractedFacts(**merged)
