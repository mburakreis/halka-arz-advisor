# Retail allocation mechanics for `AllocationEconomics` (2026-08-08)

Extends `halka_arz_advisor.kap.offering_terms.OfferingTerms` with the
pre-offer retail allocation *mechanics* needed for expected-lot
analysis, and adds a new, separate `halka_arz_advisor.kap.allocation_scenario`
scenario calculator. `expert_v1`/`AllocationEconomics` itself is **not**
implemented by this change; nothing here touches `expert_v0`, scoring,
Gemini's analysis logic, Telegram, `ipo_outcomes`, or exit logic.

## What changed

**`kap/extraction.py`** — two new extractors, using the same
`SourceRef`/`FieldObservation`/`ExtractedFact` provenance types and
`merge_field_observations` conflict-detection every other field already
uses:

- `investor_group_distribution_rules` (list): every `"<GROUP> Dağıtım:
  <Eşit|Oransal> Dağıtım Yöntemine göre yapılacaktır"` sentence in the
  base İzahname's own "Dağıtım Esasları" narrative — classified into the
  same closed `InvestorGroup` vocabulary `investor_group_allocations`
  already uses. The institutional tranches' real wording is a
  negotiated/discretionary process in free narrative text, not this
  fixed sentence shape, and is deliberately never force-classified as
  equal or proportional.
- `distribution_regulation_reference` (scalar): the SPK communiqué
  number cited as the regulatory basis for the tahsisat section (e.g.
  `"II-5.2"`, from `"II-5.2 sayılı Sermaye Piyasası Araçlarının Satışı
  Tebliği'nin 18'inci maddesi..."`) — captured generically (the number
  itself, not hardcoded), so a future filing citing a different or
  superseding communiqué is still read correctly.

**`kap/offering_terms.py`** — five new `OfferingTerms` fields:
`institutional_allocation_percentage`/`institutional_offered_shares`
(plain arithmetic sum of the `domestic_institutional` +
`foreign_institutional` lines of `investor_group_allocations` — the
same "sum of already-verified real values" spirit as `gross_offer_size`,
only summed when *every* matched institutional line itself has a value,
never a silently-partial sum), `investor_group_distribution_rules`
(passthrough), `retail_distribution_rule` (the `"retail"`-classified
entry of it), and `distribution_regulation_reference` (passthrough).
Every new field goes through the same `_pre_offer_safe_fact`
re-derivation the rest of `OfferingTerms` uses — never a raw
`facts.<field>.status` read — so a post-offer `ipo_results` observation
can never leak into one of these field slots either.

**`kap/allocation_scenario.py`** (new) — `AllocationScenario`/
`build_allocation_scenario(terms, hypothetical_retail_participant_count)`:
a pure, deterministic scenario calculator (no I/O, no forecasting) —
see "AllocationScenario calculator" below.

## Investigating the coverage blocker

Inspected real cached documents (offline: cached PDF bytes + cached OCR
text, no live KAP calls — KAP's disclosure-list/attachment-detail
endpoints were intermittently `429`/`500` for parts of this session,
consistent with the persistent-429 issue noted in this project's own
memory from a prior session) before writing any pattern, per this
thread's established practice.

**Confirmed: the short investor sale announcement never states retail
allocation percentages.** Read a real, fully-digital "Tasarruf
Sahiplerine Satış Duyurusu" (LUXERA, 4 pages) end to end — it states the
subscription window and price, then explicitly *defers* application-method
detail to "İzahname'nin 25 numaralı 'Halka Arza İlişkin Hususlar'
başlığının altında 25.1.3.2.a alt başlığında" rather than restating it.
So hypothesis "the announcement has a simpler recoverable form" from
this task's brief is **refuted** by direct evidence — the full base
İzahname's §25.2.3(a) section is the only pre-offer-safe source for this
fact, confirmed consistent (same section number, same "II-5.2 sayılı..."
citation) across every real full İzahname read this session.

**Also confirmed and explicitly avoided: `ipo_results` ("Halka Arzına
İlişkin Sonuçlar") notices restate a `"Planlanan Tahsisat"` (planned
allocation) column with the *same* pre-offer-determined percentages, in
clean, easy-to-parse text — a real temptation, since these notices are
far more cache-complete than the base İzahname (9 real ones sampled vs.
4 real usable full İzahnames). **Not used** — this is exactly the
leakage this task explicitly warns against ("do not infer a retail
percentage from post-offer allocation results"): the document itself is
published after subscription closes, `ipo_results` is excluded from
`_pre_offer_safe_fact`'s re-derivation, and its rich `"Talep"`/`"Dağıtım"`
demand/outcome columns are exactly the "final participant counts or
demand figures" the task says not to use.

**Real, evidence-based root cause for why raw allocation-percentage
coverage stays low even with correct extraction:** of the 132 cached
PDFs, only 4 are large, readable base İzahname bundles; only one of
those 4 (EKDMR) both contains and successfully OCRs/extracts the
§25.2.3(a) tahsisat table. For GOLDA specifically — whose backfill
history already shows all 7 real İzahname parts discovered
(`data/cache/kap_backfill`) — the relevant later part is a *scanned*
PDF whose OCR result is truncated at exactly 30 pages
(`kap.ocr.DEFAULT_MAX_PAGES = 30`, confirmed by direct cache inspection:
the OCR'd text for that part exists but never reaches the tahsisat
section, which falls later in the document). This is a genuine,
generic (not GOLDA-specific) document-processing constraint, not a
per-ticker quirk — but raising `OCR_MAX_PAGES` is shared infrastructure
also used by `price_determination_report` financial extraction
(carefully tuned in the `58e483d` session) and would meaningfully
increase OCR runtime project-wide. Per this task's "fix that narrowly"
instruction, **no change was made to `OCR_MAX_PAGES`** — it's already
configurable via the `OCR_MAX_PAGES` env var without any code change,
which is the safe, narrow lever if a future session wants to spend the
extra OCR runtime specifically to chase this. Documented here rather
than changed blind.

## Coverage before vs. after (23-company readable cohort, cache-only, 2026-08-08)

Regenerate with `uv run python scripts/audit_offering_terms_coverage.py`.

| Field | Before | After | Conflicts (after) |
|---|---|---|---|
| `distribution_method` | 7/23 (30.4%) | 7/23 (30.4%) — unchanged, out of scope this task | 0 |
| `retail_allocation_percentage` | 1/23 (4.3%) | 1/23 (4.3%) | 0 |
| `retail_offered_shares` | 1/23 (4.3%) | 1/23 (4.3%) | 0 |
| `investor_group_allocations` | 1/23 (4.3%) | 1/23 (4.3%) | 0 |
| `institutional_allocation_percentage` (new) | N/A | 1/23 (4.3%) | 0 |
| `institutional_offered_shares` (new) | N/A | 1/23 (4.3%) | 0 |
| `retail_distribution_rule` (new) | N/A | **4/23 (17.4%)** | 0 |
| `investor_group_distribution_rules` (new) | N/A | **4/23 (17.4%)** | 0 |
| `distribution_regulation_reference` (new) | N/A | **8/23 (34.8%)** | 0 |

The raw tahsisat-percentage fields (`retail_allocation_percentage`,
`investor_group_allocations`, and their institutional counterparts) did
**not** improve — as investigated above, this is a document-acquisition/
OCR-page-budget ceiling, not an extraction-quality gap; the extractor
itself is verified correct against EKDMR's real table and against
synthetic fixtures grounded in the other real documents read this
session. The two new equal/proportional and regulatory-citation fields,
however, are materially more recoverable: `distribution_regulation_reference`
resolves for 8/23 companies (including several — ALBTN, GOLDA, MASFN,
SOHOE — where the tahsisat percentage table itself is still not_found),
and `retail_distribution_rule` for 4/23, both because their source
sentences sit earlier in the document (closer to, but not inside, the
still-gated §25.2.3(a) table) and survive OCR/page-budget truncation
more often. No new conflicts were introduced on any field.

## `AllocationScenario` calculator

`kap.allocation_scenario.build_allocation_scenario(terms, hypothetical_retail_participant_count)`
— pure, deterministic, no I/O, no participant-count forecasting (the
count is always a caller-supplied what-if input, never predicted).

Only ever reads already-resolved (`status == "extracted"`) `OfferingTerms`
fields:

- Requires `retail_distribution_rule == "equal"` — a `"proportional"`,
  `"not_found"`, or `"conflicting"` rule blocks `lots_per_investor`
  entirely (a proportional/unknown rule depends on total retail demand
  at close, which this calculator deliberately never guesses), with an
  explicit caveat naming which precondition failed.
- Requires `retail_offered_shares` resolved for `lots_per_investor`;
  additionally requires `offer_price` resolved for
  `tl_allocation_per_investor` (the two can be independently blocked —
  a scenario can report a lot count with a TL-value caveat, but never
  the reverse).
- "Lot" is treated as one share at nominal (par) value — this project
  extracts no separate lot-size field. A real cached İzahname (EKDMR)
  does explicitly confirm this 1:1 mapping ("Beheri 1 TL nominal
  değerde 1 adet paya denk gelen bir lot payın satış fiyatı 45,00 TL
  olarak belirlenmiştir"), but that definition is not independently
  re-verified per company here — this is stated as an explicit
  `assumptions` entry on every computed scenario, never silently
  assumed.
- `assumptions` (always populated when a scenario computes) states the
  equal-split/no-rounding modeling choice, the lot=share assumption,
  and the up-front-order-capital assumption explicitly; `caveats`
  (always populated when a scenario cannot compute one or both outputs)
  names exactly which `OfferingTerms` precondition blocked it.

## Testing

`tests/test_kap_extraction.py`: 4 new tests for the two new extractors,
grounded in the real sentence shapes read this session (equal/proportional
per-group rule sentences, the negotiated-institutional-wording exclusion,
the Tebliğ citation).

`tests/test_kap_offering_terms.py`: 10 new tests — institutional-sum
derivation (including the "one line missing a percentage must not
silently under-sum" case), `retail_distribution_rule` passthrough/
not-found, `distribution_regulation_reference` passthrough, and 6
`AllocationScenario` tests (computed case, TL-blocked-without-price
case, proportional-blocks-lots case, not-found-blocks-everything case,
non-positive participant count).

Full suite: `uv run python -m pytest` — 452 passed (was 437 before this
change; +15 net), no regressions.
