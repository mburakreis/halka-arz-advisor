# `OfferingTerms`: a canonical pre-offer economic-terms model (2026-08-08)

Adds `halka_arz_advisor.kap.offering_terms.OfferingTerms` — one
normalized, provenance-preserving view of an IPO's core economic terms,
built strictly from pre-offer-safe official sources (the approved
İzahname and Tasarruf Sahiplerine Satış Duyurusu — never
`ipo_results`/`price_determination_report`, and never
`SpkIpoRecord`/SPK's completed-IPO fields). `expert_v1` is **not**
implemented by this change; nothing here touches `expert_v0`, scoring,
Gemini's analysis logic, Telegram, exit logic, or `historical_dataset`'s
outcome labels.

## What changed

**`kap/extraction.py`** — fixed the highest-impact real-document gaps
found by inspecting real cached prospectus/announcement text (see
"Real-document evidence" below), and added four new raw fields
(`par_value_per_share`, `pre_offer_capital`, `post_offer_capital`,
`investor_group_allocations`) using the exact same
`SourceRef`/`FieldObservation`/`ExtractedFact` provenance types and
`merge_field_observations` conflict-detection this module already used
for every other field — no new provenance scheme.

**`kap/offering_terms.py`** (new) — `OfferingTerms`, one
`OfferingTermField` per requested field (`offer_price`,
`subscription_start`/`subscription_end`, `total_offered_shares`,
`new_issue_shares`, `secondary_sale_shares`, `pre_offer_share_count`/
`post_offer_share_count`, `gross_offer_size`,
`implied_post_money_market_cap`, `distribution_method`,
`retail_allocation_percentage`, `retail_offered_shares`,
`investor_group_allocations`). Each field carries `status`
(`extracted`/`conflicting`/`not_found` — the same vocabulary
`ExtractedFact` already uses), `value`, `unit`, `derived` (bool), and
every backing `OfferingTermObservation` (value, snippet, document type,
disclosure id, **publication timestamp** — the one piece of provenance
`kap_extraction`'s own `SourceRef` doesn't carry, since `OfferingTerms`
needs it and `SourceRef` didn't).

Derived fields (`gross_offer_size = offer_price × total_offered_shares`;
`implied_post_money_market_cap = offer_price × post_offer_share_count`;
`pre_offer_share_count`/`post_offer_share_count = capital / par_value_per_share`;
`secondary_sale_shares` fallback `= total_offered_shares − new_issue_shares`)
are computed only when every input is itself resolved
(`_derive`/`_share_count_field`/`_secondary_sale_field` in
`offering_terms.py`) — a conflicting input propagates to a conflicting
output, a missing input to `not_found`; nothing is ever silently
arbitrated or guessed. `retail_allocation_percentage`/
`retail_offered_shares` read the `"retail"`-classified line of
`investor_group_allocations`, never inferred any other way.

**Leakage-safety fix caught by this change's own coverage run**:
`ExtractedFacts` is a *shared* model — `kap_extraction`'s
`_SCALAR_EXTRACTORS` isn't scoped per document type, so a field name
like `total_offered_shares` can legitimately also come from a matching
sentence in the (post-offer) `ipo_results` notice, and
`merge_field_observations` already allowed that observation into the
very same field slot a prospectus/announcement observation uses. This
is a defensible reading for other, non-`OfferingTerms` consumers, but
`OfferingTerms` is pre-offer-safe by contract — `build_offering_terms`
re-derives every direct field from only its `approved_prospectus`/
`investor_sale_announcement`-sourced observations
(`_pre_offer_safe_fact`) before ever reporting a status, discarding any
`ipo_results`/`price_determination_report` observation. Confirmed live
against a real conflict this introduced for ALBTN (a genuine 49M vs.
21M prospectus/announcement disagreement was previously being reported
alongside — and in one case masked by — a third, 70M `ipo_results`
observation); after the fix, `total_offered_shares`' conflict count
across the cohort dropped from 5 to 1, and several previously-conflicting
fields resolved cleanly.

## Real-document evidence (read live from cached documents, 2026-08-08)

- **`offering_price`** (confirmed root cause of the 1/20 (5%) rate in
  `docs/capability-audit-2026-08-08.md`): every real investor sale
  announcement sampled (ATATR, EMPAE, MEYSU, NETCD) states the price as
  *"Bir payın nominal değeri 1 TL olup, X TL fiyattan/'den satışa
  sunulacaktır"* — a sentence neither existing pattern
  (`belirlenen X TL` / `Halka Arz Fiyatı: X`) ever matched. Added as the
  primary pattern, plus a "Sulanma Etkisi" dilution-table fallback
  (`Halka Arz Fiyatı   45,00`, confirmed against EKDMR's real İzahname).
- **`secondary_sale_shares`** (confirmed root cause of the 3/20 (15%)
  rate): the old pattern searched for the literal phrase "ortak
  satışı", which does not appear in any of 6 real documents sampled
  (EKDMR, ATATR, EMPAE, MEYSU, NETCD, UCAYM) — real wording is "mevcut
  ortak(lar)... sahip olduğu"/"...'a/'ya/'ye ait". A document can name
  one seller (safe to extract directly) or several individually (EMPAE:
  8, EKDMR: 10, with no combined figure ever stated) — summing blindly
  risks double-counting the same paragraph's standard disclaimer
  repeat, so the new extractor only accepts an unambiguous single
  seller match within the capital-increase clause's own bounded region;
  a real multi-seller document falls back to
  `total_offered_shares − new_issue_shares`, verified live against two
  real cases (EMPAE: 9,000,000 = 38,000,000 − 29,000,000; EKDMR:
  12,000,000 = 52,000,000 − 40,000,000 — both match the sum of their
  individually named sellers exactly).
- **`total_offered_shares`**/**`capital_increase_shares`**: UCAYM's real
  announcement aggregates the secondary sale into the same clause via a
  parenthetical ("...artırılacak 50.000.000 TL (ve mevcut ortakların
  sahip olduğu 10.000.000 TL olmak üzere toplam 60.000.000 TL) nominal
  değerli..."), breaking both the old capital-increase pattern (needs
  "nominal değerli" immediately after the amount) and the old total
  pattern (needs "TL" immediately before "nominal değerli", not "TL)").
  Fixed with an anchor-only fallback and an optional trailing `)`.
- **`distribution_method`**: EKDMR's real İzahname states "'Sabit Fiyat
  ile Talep Toplama'" — a different real spacing from the existing
  "Sabit Fiyatla Talep Toplama" entry, added as a second recognized
  phrase (plus the same "ile" variant for the other two methods).
- **`investor_group_allocations`/`retail_allocation_percentage`/
  `retail_offered_shares`** (previously 0% implemented, per the
  capability audit): EKDMR's real İzahname §25.2.3(a) ("Yatırımcı grubu
  bazında tahsisat oranları", a numbered SPK-template heading) states a
  bulleted breakdown — "20.800.000 TL nominal değerdeki kısmı (40%)
  Yurt İçi Bireysel Yatırımcılara, ..." — matched by a new extractor and
  classified into a closed, SPK-defined investor-group vocabulary
  (retail / high_demand / domestic_institutional / foreign_institutional
  / other).
- **`pre_offer_share_count`/`post_offer_share_count`**: derived from two
  new raw fields, `pre_offer_capital`/`post_offer_capital` (the same
  before/after capital amounts the existing `capital_increase_ratio`
  fallback already read but never surfaced on their own — confirmed
  live in a "Sulanma Etkisi" dilution table too: "Ödenmiş Sermaye
  280.000.000 320.000.000", agreeing exactly with the announcement's own
  reading for EKDMR) divided by a newly extracted `par_value_per_share`
  (from the same sentence as `offering_price` — every real document
  sampled states 1 TL, but this is read explicitly, never assumed).

## Coverage before vs. after (23-company readable cohort, cache-only, 2026-08-08)

"Before" for the pre-existing `kap_extraction` fields is
`docs/capability-audit-2026-08-08.md`'s own committed 31-company
unconditional count (`Avail (31)`); this run's 23-company cohort is
narrower (only companies with a currently cache-readable, matched
pre-offer document at the moment of this run — the two counts aren't
directly comparable row-for-row, but the direction and scale of the
change are the point). Fields with no pre-existing extractor are
"before: N/A (no extractor existed)". Regenerate with
`uv run python scripts/audit_offering_terms_coverage.py`.

| `OfferingTerms` field | Underlying `kap_extraction` field(s) | Before | After (23) | Conflicts (after) |
|---|---|---|---|---|
| `offer_price` | `offering_price` | 1/31 (3.2%) | **17/23 (73.9%)** | 0 |
| `subscription_start`/`subscription_end` | `subscription_start_date`/`subscription_end_date` | part of `subscription_window`, 6/31 (19.4%) | 6/23 (26.1%) each | 0 |
| `total_offered_shares` | `total_offered_shares` | 14/31 (45.2%) | **17/23 (73.9%)** | 1 |
| `new_issue_shares` | `capital_increase_shares` | 19/31 (61.3%) | 20/23 (87.0%) | 1 |
| `secondary_sale_shares` | `secondary_sale_shares` (+ derived fallback) | 4/31 (12.9%) | **19/23 (82.6%)** (13 direct, 6 derived) | 1 |
| `pre_offer_share_count` | derived (`pre_offer_capital` / `par_value_per_share`) | N/A | 15/23 (65.2%) | 4 |
| `post_offer_share_count` | derived | N/A | 12/23 (52.2%) | 6 |
| `gross_offer_size` | derived (`offer_price × total_offered_shares`) | 1/31 (3.2%, `implied_offer_size_value`) | **14/23 (60.9%)** | 1 |
| `implied_post_money_market_cap` | derived | N/A | 12/23 (52.2%) | 6 |
| `distribution_method` | `distribution_method` | 5/31 (16.1%) | 7/23 (30.4%) | 0 |
| `retail_allocation_percentage`/`retail_offered_shares` | derived from `investor_group_allocations` | N/A (no catalog field) | 1/23 (4.3%) each | 0 |
| `investor_group_allocations` | `investor_group_allocations` | N/A | 1/23 (4.3%) | 0 |

The remaining `post_offer_share_count`/`implied_post_money_market_cap`
conflicts are concentrated in a handful of companies where the base
prospectus's own "Sulanma Etkisi"/"Ödenmiş Sermaye" table reading
disagrees with the announcement's narrative-sentence reading of the
same pre/post capital pair (e.g. QUICK, GOLDA — genuinely worth a future
session's targeted investigation into whether this is a real
cross-document disagreement or a table-linearization artifact in those
specific cached PDFs) — correctly reported `conflicting`, never
arbitrated. `retail_allocation_percentage`/`investor_group_allocations`
remain low because the §25.2.3(a) tahsisat table needs the *entire*,
long, fully digital base İzahname to be readable (confirmed in only
1/13 documents sampled during research) — a genuine document-acquisition
gap, not an extraction-quality one; the same regex correctly finds every
bullet whenever that section is reached.

## Testing

`tests/test_kap_extraction.py`: 22 new/updated tests, each grounded in
a real (paraphrased) document shape from the research above, including
the two previously-existing tests that validated the old, unconfirmed
"ortak satışı" wording (rewritten to the real shape) and a dedicated
multi-seller-returns-`None` regression test.

`tests/test_kap_offering_terms.py` (new, 17 tests): direct passthrough
+ provenance/timestamp attachment, conflict propagation (never
arbitrated), the `secondary_sale_shares` derivation fallback (and that
it doesn't override a genuine direct conflict), `par_value_per_share`
division (including the zero-par and missing-par cases — never assumes
1), `gross_offer_size`/`implied_post_money_market_cap` derivation and
their blocked/not_found cases, `retail_*` derivation from
`investor_group_allocations`, and the `ipo_results`-leakage exclusion
(both the "no pre-offer counterpart" case and the "a real
prospectus/announcement conflict must still surface" case).

Full suite: `uv run python -m pytest` — 437 passed (was 408 before this
change; +29 net — 22 new/updated `kap_extraction` tests, 17 new
`offering_terms` tests, minus 2 rewritten in place), no regressions.
