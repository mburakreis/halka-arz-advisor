# Scoped deep-OCR recovery + corrected `AllocationScenario` math (2026-08-08)

Two changes, both continuing the `OfferingTerms`/`allocation_scenario` work
from `79999fe`/`7abe33a`. `expert_v1`/participant-count forecasting are
still **not** implemented; nothing here touches `expert_v0`, scoring,
`decision/`, Gemini's analysis logic, Telegram, `ipo_outcomes`, or exit
logic.

## 1. Scoped, on-demand deep-OCR fallback (`kap/allocation_ocr.py`)

**Why not just raise `OCR_MAX_PAGES`.** `kap.ocr`'s 30-page default is
shared infrastructure also tuned for `price_determination_report`
financial-statement extraction (`58e483d`); raising it globally would
slow every OCR run project-wide to chase one late-document section in a
minority of companies.

**What was built instead.** `kap.ocr.ocr_pdf_extend(pdf_bytes, config,
cache, target_page_count)` — a new function alongside the existing
`ocr_pdf` — deepens a document's OCR to `target_page_count`, reusing any
page already cached by an earlier, shallower run (`OcrCache`'s per-page
entries are keyed by `(content_hash, page_number, languages, dpi)`, never
by how many pages a given run asked for) and only rendering+recognizing
pages that are genuinely still missing. The manifest is extended (never
regressed), so a later plain `ocr_pdf()`/`lookup_ocr_result()` call
against the same document transparently benefits too.

`kap.allocation_ocr.recover_allocation_sections(record_id, disclosures,
pdf_cache, ocr_cache, ...)` is the scoped trigger built on top of it:

- Runs only when one of `investor_group_allocations`/
  `retail_allocation_percentage`/`retail_offered_shares` is still
  `not_found` on the company's already-built `OfferingTerms` (checked
  first — does nothing at all if already resolved).
- Only considers that company's `approved_prospectus` disclosures
  already classified `classify_prospectus_document_role(...) ==
  "base_document"` (never `investor_sale_announcement`/`ipo_results`/
  `price_determination_report` — allocation-critical evidence must stay
  pre-offer-safe) and only those with `pdf_status in {"scanned",
  "empty"}` — a digitally-readable ("ok") document is never OCR'd at
  all; a missing field there is an extraction-pattern gap deep-OCR can't
  fix.
- New `kap.classification.extract_prospectus_part_number` (generic
  "İzahname N. Bölüm"/"N. Kısım" ordinal parsing, not per-issuer) sorts
  candidates highest-part-number-first — the best generic proxy this
  project has for "closest to the end of the document", where a tahsisat
  section tends to sit.
- Deepens one candidate at a time in bounded `DEFAULT_PAGE_STEP=30`-page
  steps up to `DEFAULT_MAX_DEEP_PAGES=240` (comfortably above a real
  204-page prospectus this project has already seen live), re-extracting
  and rebuilding `OfferingTerms` after every step, and stops the moment
  the target fields resolve (`extracted` *or* `conflicting` — a genuine
  conflict is a resolved investigation outcome, not something more OCR
  would fix) or every candidate is exhausted.
- Cache-only: reads already-cached PDF bytes via `PdfCache`, never
  triggers a fresh KAP download.
- No ticker-specific page ranges or document IDs anywhere in the logic —
  page budgets and the part-number heuristic are generic.

### Real-data validation

KAP's own API was rate-limited for the second half of this session
(confirmed directly: `curl -X POST .../api/disclosure/list/main` → `429`,
consistent with prior sessions' documented 429/500 issues), which blocked
a full live 23-company "after" coverage re-run via
`scripts/audit_offering_terms_coverage.py --deep-ocr-allocation` (4
consecutive attempts failed on the same live KAP disclosure-list call
this script needs even in cache-only mode).

Rather than leave the new mechanism unverified against real data, it was
exercised directly against real cached GOLDA documents (no live KAP
calls — only already-cached `PdfCache`/`OcrCache` bytes), the same
company identified in the prior session as having a real, confirmed
OCR-page-budget truncation. Locating GOLDA's obj_ids required scanning
`data/cache/kap_pdfs` (132 real cached PDFs) for "GOLDA" text (digital
layer via `pypdf`, and already-cached OCR text) since the disclosure
→obj_id mapping is itself only resolved by a live KAP call this session
couldn't make. Found:

- One fully-digital 45-page İzahname part (`pdf_status="ok"`, never
  needed OCR) — confirmed **not** to contain the tahsisat table (`"tahsis"`
  does not appear anywhere in its extracted text), so this part alone
  was never going to resolve the field regardless of OCR.
- Three real scanned İzahname parts (44, 40, and 44 real total pages
  each), each previously OCR'd only to the default 30-page cap
  (`ocr_partial` in the existing cache).

Running `recover_allocation_sections` directly against these three real
parts (real `PdfCache`/`OcrCache` directories, real local Tesseract, zero
network calls) correctly deepened each one to its true full extent —
30→44, 30→40, 30→44 pages — reusing the already-cached first 30 pages of
each (confirmed by wall-clock time: only the genuinely new ~10-14 pages
per document were actually rendered and OCR'd). This directly confirms
the core mechanism (bounded step-deepening, cache reuse, stopping
correctly at each document's true page count) works correctly against
real scanned government filings, not just the synthetic test fixture.

**However, none of these three particular parts turned out to contain
GOLDA's actual §25 tahsisat section** — so `investor_group_allocations`
et al. remain `not_found` for GOLDA specifically after this run. GOLDA's
real bundle has 7 total parts (confirmed via its `kap_backfill` seed
cache); the digital part plus these three scanned parts accounts for 4,
leaving 3 more real parts whose content is currently unknown (their
`obj_id`s were never independently resolved this session, and they don't
appear in the existing OCR cache since they were never previously
OCR'd at all — finding them would require either a live KAP attachment-
metadata call, currently blocked, or a broad speculative first-page OCR
sweep across every uncached PDF in the shared cache, which was judged
out of scope for a targeted verification pass). This is a genuine,
still-open document-inventory gap for this one company, distinct from
(and not evidence against) the deep-OCR mechanism itself, which behaved
exactly as designed on the three real parts it was pointed at.

### Coverage

The confirmed **baseline** (re-verified live this session, matching the
prior session's number exactly): `investor_group_allocations`/
`retail_allocation_percentage`/`retail_offered_shares`/
`institutional_allocation_percentage`/`institutional_offered_shares` are
all **1/23 (4.3%)** across the readable cohort; `retail_distribution_rule`
is 4/23 (17.4%). A full-cohort **after** re-run could not be completed
this session due to the confirmed KAP outage above — this should be
re-run (`uv run python scripts/audit_offering_terms_coverage.py
--deep-ocr-allocation`) once KAP is reachable again to get real
before/after numbers across the whole cohort. Exactly **1/23** companies
(EKDMR) currently resolve `retail_distribution_rule == "equal"` with
`retail_offered_shares` and `offer_price` also resolved — i.e., can
currently produce a `"computed"` `AllocationScenario` — confirmed against
this session's live baseline re-run.

## 2. Corrected `AllocationScenario` semantics (`kap/allocation_scenario.py`)

The previous version's `lots_per_investor`/`tl_allocation_per_investor`
were a plain float average (`retail_offered_shares /
hypothetical_retail_participant_count`) — misleading, since a real
per-investor allocation is an integer and rarely divides evenly.

Replaced with an explicit floor/remainder baseline:

- `average_shares_per_participant` — the plain ratio, informational only.
- `base_integer_allocation = floor(retail_offered_shares /
  hypothetical_retail_participant_count)`.
- `remainder_shares = retail_offered_shares - base_integer_allocation *
  hypothetical_retail_participant_count`.
- `allocation_range_shares` — `(base, base)` if `remainder_shares == 0`,
  else `(base, base + 1)`.
- `tl_allocation_baseline`/`tl_allocation_range` — the same two figures
  priced at `offer_price` (independently gated: a scenario can report a
  share-count baseline with only a TL-figure caveat, never the reverse).

A **permanent** caveat (`_DEMAND_DISTRIBUTION_CAVEAT`) is appended to
every `"computed"` scenario, stating explicitly that
`hypothetical_retail_participant_count` alone cannot reproduce the real
final per-investor allocation — real equal-distribution mechanics
typically satisfy smaller/fully-satisfiable orders first and redistribute
the remainder, which this calculator does not model. The figures are
labeled as a deterministic floor/remainder baseline, never a prediction.

All existing gating is unchanged: `retail_distribution_rule` must be
confirmed `"equal"` (not `"proportional"`, `not_found`, or
`conflicting`) and `retail_offered_shares` resolved for any output at
all; `offer_price` additionally gates the two TL fields.

No other in-repo caller of `build_allocation_scenario`/
`AllocationScenario` exists yet (grepped before changing the shape).

## Testing

`tests/test_kap_ocr.py`: 1 new test (`ocr_pdf_extend` reuses cached
pages and only OCRs the genuinely new ones — asserts an `AssertionError`
if a page already cached by a shallower run is re-rendered/re-OCR'd).

`tests/test_kap_allocation_ocr.py` (new, 2 tests): a 4-page synthetic
scanned prospectus whose tahsisat table sits on page 4, past a 2-page
test `OCR_MAX_PAGES` — confirms the fallback recovers it, that a repeat
pass does zero further OCR work (fields already resolved), and that a
digitally-readable (`pdf_status="ok"`) prospectus is never OCR'd by this
fallback at all.

`tests/test_kap_offering_terms.py`: updated `AllocationScenario` tests
for the new floor/remainder/range/caveat shape, plus a new uneven-division
test (1,000,000 shares / 300,000 participants → base=3, remainder=100,000,
range=(3,4), TL range=(135, 180) at a 45 TL offer price).

Full suite: `uv run python -m pytest` — **456 passed** (was 452 before
this change), no regressions.
