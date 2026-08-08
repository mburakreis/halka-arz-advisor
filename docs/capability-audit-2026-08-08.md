# Capability audit: what the IPO data pipeline can actually obtain (2026-08-08)

Capability-first audit of every normalized fact / derived feature in
`halka_arz_advisor.decision.catalog.FEATURE_CATALOG` (66 entries), run
against every completed IPO this project's existing KAP/SPK pipeline
currently has cached documents for. `expert_v0` (`decision/scoring_config.py`)
is treated only as the current baseline model, not as the standard a
feature is judged against — a feature can be strong even if `expert_v0`
doesn't use it, and weak even if `expert_v0` does.

## Method

- **Cohort**: 31 completed IPOs (2024–2026, matched SPK completed-IPO
  records with at least one cached KAP disclosure): AAGYO, AKHAN, ALBTN,
  ALVES, ARFYE, ATATR, BESTE, BETAE, EKDMR, EKIM, EMPAE, ENTRA, FRMPL,
  GENKM, GOLDA, ISVEA, KARCL, LXGYO, MASFN, MCARD, METEN, MEYSU, NETCD,
  ORZAX, QUICK, SARAE, SOHOE, SSAAT, SVGYO, UCAYM, ZGYO. This is broader
  than the 9/14-company cohorts referenced in earlier session notes —
  those were scoped to leakage-free historical-decision reconstruction
  (`historical_dataset`, which additionally requires a resolved
  point-in-time cutoff); this audit is a raw capability count, not a
  leakage-safe evaluation, so it includes every company with any cached
  document at all.
- **Tooling**: new read-only script, `scripts/audit_capability_report.py`
  (committed). Reuses the existing, unmodified
  `decision.audit.audit_company` / `FEATURE_CATALOG` exactly as
  `scripts/audit_decision_coverage.py` does, but (a) covers the whole
  backfilled+recent-90-day cohort instead of just "recent 30 days", and
  (b) deliberately evaluates each company's **entire unfiltered**
  document set — no historical decision-cutoff filtering — since the
  question here is "can the pipeline obtain this fact at all", not "was
  it knowable before subscription closed". No scoring, weighting,
  extraction, Gemini, or Telegram code was touched.
- **Live calls made**: a small structured SPK completed-IPO/application
  API fetch (2024–2026) and KAP's per-disclosure attachment-metadata
  JSON call (always live even in "cache-only" mode, per
  `kap.documents.process_disclosure_documents`'s own docstring) plus one
  recent-90-day KAP disclosure-list search. No PDF was downloaded and no
  OCR was run that wasn't already cached.
- **Two coverage denominators are reported where they diverge
  materially**: *unconditional* (out of all 31 companies) and
  *conditional* (out of companies where the relevant source document is
  actually readable — 20 for prospectus/announcement-sourced fields, 18
  for price-determination-report fields, 9 for IPO-results fields).
  Conditional coverage isolates **extraction quality** from **document
  acquisition**, which the unconditional number conflates.

Raw output: `data/cache/audit_capability_report.json` is not committed
(31-company snapshot, regenerate with `uv run python
scripts/audit_capability_report.py`); the numbers below are taken from a
2026-08-08 run.

## Per-feature table

Legend — **Class**: `RELIABLE_NOW` / `USABLE_WITH_MISSINGNESS` /
`EXTRACTION_FRAGILE` / `SOURCE_LIMITED` / `POST_OFFER_ONLY`. **Extr.**:
`api` = structured API field, `pdf` = digital PDF text layer, `ocr` =
OCR text, `derived` = computed from other features, `none` = no
extractor exists at all. **Timing**: P = pre_offer, X = post_offer, M =
market-context (macro, company-agnostic).

### fundamental_quality

| Feature | Timing | Source | Avail (31) | Cond. | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|---|
| business_description | P | kap_extraction | 0 (0%) | — | 0 | none | n/a | No | **no extractor implemented at all** (21/31 have a readable doc) | EXTRACTION_FRAGILE |
| key_risk_factors | P | kap_extraction | 10 (32.3%) | 9/20 (45%) | 1 | ocr(6)/pdf(4) | Partial | No | not found in text / no readable doc | EXTRACTION_FRAGILE |
| use_of_proceeds_plan | P | kap_extraction | 4 (12.9%) | 3/20 (15%) | 0 | pdf(3)/ocr(1) | Partial | No | not found in text | EXTRACTION_FRAGILE |
| financial_statement_summary | P | financial_series (price det. report) | 1 (3.2%) | 1/18 (6%) | 0 | pdf | Low | No | table not matched / no readable doc | EXTRACTION_FRAGILE |
| revenue_growth_yoy | P | derived_financial | 3 (9.7%) | 3/18 (17%) | 0 | derived | Low | No | inputs missing/incompatible | EXTRACTION_FRAGILE |
| net_margin | P | derived_financial | 1 (3.2%) | 1/18 (6%) | 0 | derived | Low | No | inputs missing/incompatible | EXTRACTION_FRAGILE |
| debt_to_equity | P | derived_financial | 1 (3.2%) | 1/18 (6%) | 0 | derived | Low | No | inputs missing/incompatible | EXTRACTION_FRAGILE |
| current_ratio | P | derived_financial | 3 (9.7%) | 3/18 (17%) | 0 | derived | Low | No | inputs missing/incompatible | EXTRACTION_FRAGILE |
| operating_cash_flow_to_net_income | P | derived_financial | 0 (0%) | 0/18 (0%) | 0 | derived | n/a | No | inputs missing — **zero even with doc readable** | EXTRACTION_FRAGILE |
| interest_coverage | P | derived_financial | 1 (3.2%) | 1/18 (6%) | 0 | derived | Low | No | inputs missing/incompatible | EXTRACTION_FRAGILE |
| related_party_transactions_disclosure | P | kap_extraction | 0 (0%) | — | 0 | none | n/a | No | **no extractor implemented at all** | EXTRACTION_FRAGILE |
| litigation_exposure_disclosure | P | kap_extraction | 0 (0%) | — | 0 | none | n/a | No | **no extractor implemented at all** | EXTRACTION_FRAGILE |

**fundamental_quality as a whole is the weakest category in the system.** All
11 features are either unimplemented or extract from <45% of documents
that are actually readable. This is `expert_v0`'s single heaviest
category (50% of `total_score`).

### valuation

| Feature | Timing | Source | Avail (31) | Cond. | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|---|
| offering_price | P | kap_extraction | 1 (3.2%) | 1/20 (5%) | 0 | pdf | Low | No | not found in text (foundational field, near-zero extraction) | EXTRACTION_FRAGILE |
| capital_increase_ratio | P | kap_extraction | 20 (64.5%) | 18/20 (90%) | 2 | ocr(16)/pdf(4) | **High when present** | **Yes, when present** | document not readable/found | USABLE_WITH_MISSINGNESS |
| secondary_sale_ratio | P | kap_extraction | 2 (6.5%) | 2/20 (10%) | 0 | ocr/pdf | Low | No | not found in text | EXTRACTION_FRAGILE |
| implied_offer_size_value | P | derived (offering_price × total_offered_shares) | 1 (3.2%) | — | 5 | derived | Low | No | inherits offering_price's fragility | EXTRACTION_FRAGILE |
| post_offer_market_value_of_offering | X | spk_ipo_record (structured API) | 31 (100%) | — | 0 | api | **High** | Yes (post-offer use only) | — | POST_OFFER_ONLY |
| earnings_multiple_at_offer | P | kap_extraction (price det. report) | 2 (6.5%) | 2/18 (11%) | 0 | pdf | Low (correctness risk on multi-form reports — see below) | No | not found in text | EXTRACTION_FRAGILE |
| reported_post_money_market_cap | P | kap_extraction | 2 (6.5%) | 2/18 (11%) | 0 | pdf | Low | No | not found in text | EXTRACTION_FRAGILE |
| reported_enterprise_value | P | kap_extraction | 1 (3.2%) | 1/18 (6%) | 0 | pdf | Low | No | not found in text | EXTRACTION_FRAGILE |
| reported_net_debt | P | kap_extraction | 1 (3.2%) | 1/18 (6%) | 0 | pdf | Low | No | not found in text | EXTRACTION_FRAGILE |
| net_debt | P | derived_financial | 1 (3.2%) | — | 0 | derived | Low | No | inputs missing | EXTRACTION_FRAGILE |
| reported_ev_ebitda_multiple | P | kap_extraction | 2 (6.5%) | 2/18 (11%) | 0 | pdf/ocr | Low | No | not found in text | EXTRACTION_FRAGILE |
| reported_ps_multiple | P | kap_extraction | 0 (0%) | 0/18 (0%) | 0 | — | n/a | No | not found in text | EXTRACTION_FRAGILE |
| reported_pb_multiple | P | kap_extraction | 4 (12.9%) | 4/18 (22%) | 0 | pdf/ocr | Low | No | not found in text | EXTRACTION_FRAGILE |
| headline_discount_percentage | P | kap_extraction | 8 (25.8%) | 8/18 (44%) | 0 | pdf(6)/ocr(2) | Moderate | No | not found in text | EXTRACTION_FRAGILE |
| recalculated_pe | P | derived_financial | 0 (0%) | — | 0 | derived | n/a | No | inputs missing | EXTRACTION_FRAGILE |
| reported_pe_difference_percentage | P | derived_financial | 0 (0%) | — | 0 | derived | n/a | No | inputs missing | EXTRACTION_FRAGILE |

**Every price-determination-report-sourced field is weak even conditional
on the report being readable** (0–44%, best case `headline_discount_percentage`
at 44%). This is genuinely an extraction problem, not (mainly) a
document-acquisition one: 18/31 companies have a readable price
determination report, but almost none of its numeric fields extract.

### offering_structure

| Feature | Timing | Source | Avail (31) | Cond. | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|---|
| subscription_window | P | kap_extraction | 6 (19.4%) | 6/20 (30%) | 0 | ocr(10)/pdf(2) | Low | No | not found in text — *the multi-session cutoff-resolution fixes (tiers 2/3) deliberately bypass this field, see note below* | EXTRACTION_FRAGILE |
| distribution_method | P | kap_extraction | 5 (16.1%) | 4/20 (20%) | 1 | ocr/pdf | Low | No | not found in text | EXTRACTION_FRAGILE |
| total_offered_shares | P | kap_extraction | 14 (45.2%) | 12/20 (60%) | **5 (25% of conditional!)** | ocr(11)/pdf(3) | **Low — real cross-document ambiguity** | No | not found / component-vs-total conflicts | EXTRACTION_FRAGILE |
| capital_increase_shares | P | kap_extraction | 19 (61.3%) | 17/20 (85%) | 1 | ocr(16)/pdf(3) | **High when present** | **Yes, when present** | document not readable | USABLE_WITH_MISSINGNESS |
| secondary_sale_shares | P | kap_extraction | 4 (12.9%) | 3/20 (15%) | 1 | pdf/ocr | Low | No | not found in text | EXTRACTION_FRAGILE |
| over_allotment_greenshoe_amount | X | spk_ipo_record | 31 (100%) | — | 0 | api | High | Yes (post-offer use only) | — | POST_OFFER_ONLY |
| lead_intermediary_institution | X | spk_ipo_record | 31 (100%) | — | 0 | api | High | Yes (post-offer use only) | — | POST_OFFER_ONLY |
| listing_market_segment | X | spk_ipo_record | 31 (100%) | — | 0 | api | High | Yes (post-offer use only) | — | POST_OFFER_ONLY |

**Note on `subscription_window`:** four separate sessions fixed real bugs
in `kap.extraction`'s subscription-date regex (wrong anchor phrase →
wrong heading position → trailing-anchor fallback), which materially
improved **`historical_dataset.cutoff`'s** ability to establish a
decision-evaluation boundary (9/14 resolved in that narrower cohort).
But `historical_dataset.cutoff`'s tiers 2/3 read the date **directly
from raw post-offer document text**, structurally bypassing
`kap_extraction`/`ExtractedFacts` by design (so a resolved cutoff never
becomes a decision-input feature — correct for leakage prevention, but
it means those fixes never reach `subscription_window` here). Only
tier 1 (the actual `kap_extraction.subscription_end_date` regex) feeds
this catalog feature, which is why `subscription_window` is still only
30% even conditional on both base documents being readable.

### market_context

| Feature | Timing | Source | Avail (31) | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|
| sector_classification | P | kap_extraction.sector_code | 0 (0%) | 0 | none | n/a | No | **no extractor for this catalog field — but `kap.sector.classify_sector` already exists, is deterministic (name-based), and is already used internally** for NOT_APPLICABLE gating on financial ratios. Pure wiring gap. | ~~EXTRACTION_FRAGILE~~ → **wired, now `RELIABLE_NOW` — see "Update" section below** |
| peer_group_comparables | P | market_data (unimplemented) | 0 (0%) | 0 | — | n/a | No | no external market-data source is implemented — genuine gap, no free official cross-company feed integrated | SOURCE_LIMITED |
| broader_index_level_at_offer | P | market_data (unimplemented) | 0 (0%) | 0 | — | n/a | No | **the underlying BIST-100 index-level data this feature would need is already flowing through EVDS and populating `bist100_return_*` below — this specific feature_id was just never wired to it.** Not a true source limitation. | ~~SOURCE_LIMITED~~ → **wired, now `RELIABLE_NOW` — see "Update" section below** |
| recent_comparable_ipo_performance | P | market_data (unimplemented) | 0 (0%) | 0 | — | n/a | No | needs a cross-company comparison this project doesn't compute — **but `ipo_outcomes` already computes exactly this per-company** (first_day/5d/20d/3m returns for every completed IPO); only the cross-company "recent peers" aggregation is missing, not the underlying data | SOURCE_LIMITED — **deliberately left unwired**, see "Update" section (this is `pre_offer`-timed but `ipo_outcomes` is a post-offer result; wiring it would leak post-offer knowledge into an entry-decision feature, which the follow-up pass was explicitly told never to do) |
| application_pipeline_status | P | spk_application (structured API) | 1 (3.2%) | 0 | api | High | Yes, when relevant | most of this cohort has *already completed* its IPO, so an application record is structurally moot for them — not a real gap for the completed-IPO measurement, it would show high coverage for actual pending applications | USABLE_WITH_MISSINGNESS |
| bist100_return_20d | P (macro) | EVDS (structured API) | 31 (100%) | 0 | api | **High** | **Yes** | — | RELIABLE_NOW |
| bist100_return_60d | P (macro) | EVDS | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| bist100_return_120d | P (macro) | EVDS | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| bist100_volatility_20d | P (macro) | EVDS | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| bist100_max_drawdown_60d | P (macro) | EVDS | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| policy_rate | P (macro) | EVDS/TCMB | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| tlref_rate | P (macro) | EVDS/BIST | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| cpi_yoy | P (macro) | EVDS/TÜİK | 31 (100%) | 0 | api | High | Yes | — | RELIABLE_NOW |
| policy_rate_minus_cpi | P (macro) | derived (EVDS) | 31 (100%) | 0 | derived | High | Yes | — | RELIABLE_NOW |

### allocation_efficiency

| Feature | Timing | Source | Avail (31) | Cond. | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|---|
| oversubscription_ratio_overall | X | kap_extraction (ipo_results) | 2 (6.5%) | 2/9 (22%) | 0 | pdf | Low | No (fragile even post-offer) | not found / doc not readable (only 9/31 have a readable ipo_results notice) | POST_OFFER_ONLY |
| retail_allocated_shares | X | kap_extraction | 7 (22.6%) | 7/9 (78%) | 0 | pdf | **High when doc readable** | Yes, when present | doc not readable | POST_OFFER_ONLY |
| institutional_allocated_shares | X | kap_extraction | 7 (22.6%) | 7/9 (78%) | 0 | pdf | High when readable | Yes, when present | doc not readable | POST_OFFER_ONLY |
| allocation_by_investor_category | X | kap_extraction | 0 (0%) | — | 0 | none | n/a | No | **no extractor implemented** | POST_OFFER_ONLY |
| demand_to_supply_ratio_by_tranche | X | kap_extraction | 0 (0%) | — | 0 | none | n/a | No | **no extractor implemented** | POST_OFFER_ONLY |
| final_allocation_price | P | derived (=offering_price) | 1 (3.2%) | — | 0 | derived | Low | No | inherits offering_price's fragility | EXTRACTION_FRAGILE |
| ipo_results document itself | — | — | **9/31 (29%)** readable | — | — | — | — | — | mostly a **document-acquisition** gap (never found/backfilled), separate from the extraction issue above | — |

### demand_sentiment

| Feature | Timing | Source | Avail (31) | Cond. | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|---|
| total_participant_count | X | kap_extraction | 1 (3.2%) | 1/9 (11%) | 0 | pdf | Low | No | doc not readable / not found | POST_OFFER_ONLY |
| retail_participant_count | X | kap_extraction | 7 (22.6%) | 7/9 (78%) | 0 | pdf | High when readable | Yes, when present | doc not readable | POST_OFFER_ONLY |
| retail_investor_demand_signal | X | kap_extraction | 2 (6.5%) | 2/9 (22%) | 0 | pdf | Low | No | not found / doc not readable | POST_OFFER_ONLY |
| institutional_investor_demand_signal | X | kap_extraction | 0 (0%) | — | 0 | none | n/a | No | **no extractor implemented** | POST_OFFER_ONLY |
| analyst_or_broker_commentary_presence | P | kap_document (price_determination_review) | 0 (0%) | — | 0 | — | n/a | No | this document type is **deliberately excluded from the fetch pipeline's target document types** (`kap.classification.target_document_types`) — untested whether brokers actually file it, not a confirmed source absence | SOURCE_LIMITED (scope exclusion, not confirmed absence) |
| post_ipo_price_performance_signal | X | market_data (unimplemented) | 0 (0%) | — | 0 | — | n/a | No | **`ipo_outcomes.first_day_return`/`return_5d/20d/3m` already compute exactly this, for 29/29 completed IPOs in the outcomes store — this catalog feature was simply never wired to it.** | POST_OFFER_ONLY — **deliberately left unwired**, see "Update" section (`ipo_outcomes` must remain a retrospective label, never a decision input, regardless of its own timing) |

### data_confidence

| Feature | Timing | Source | Avail (31) | Conflict | Extr. | Provenance reliable | Deterministic-ready | Dominant unavailable reason | Class |
|---|---|---|---|---|---|---|---|---|---|
| document_completeness | P | kap_document (presence check) | 20 (64.5%) | 0 | derived (file-existence + pdf_status) | **High — deterministic presence check, no parsing involved** | Yes, when present | one or both base documents never acquired | USABLE_WITH_MISSINGNESS |
| cross_document_field_corroboration | P | derived (5 core fields) | 0 (0%) | 6 | derived | Low | No | inherits its dependencies' (offering_price, subscription_window, etc.) fragility | EXTRACTION_FRAGILE |
| ocr_reliance | P | derived (5 core fields) | 0 (0%) | 6 | derived | Low | No | inherits dependencies' fragility | EXTRACTION_FRAGILE |
| single_source_field_flag | P | derived (5 core fields) | 0 (0%) | 6 | derived | Low | No | inherits dependencies' fragility | EXTRACTION_FRAGILE |

## Coherent groups vs. the four use cases

**Entry decision (pre-offer, must be reliable before the subscription
window closes):** only two coherent blocks clear the bar today —
(1) the 9 EVDS macro/market-context features (rates, CPI, BIST-100
trailing return/vol/drawdown — `RELIABLE_NOW`, 100%, structured), which
describe the *regime* the IPO is entering but say nothing about the
company itself, and (2) `capital_increase_ratio`/`capital_increase_shares`
(`USABLE_WITH_MISSINGNESS`, ~85-90% correct when the base prospectus is
readable). Everything else needed for an entry decision — price,
subscription window, distribution method, any fundamental or valuation
number — is `EXTRACTION_FRAGILE` today. **There is currently no coherent
block of company-specific pre-offer facts a deterministic entry decision
could safely be built on beyond the offering's capital-increase
structure and the macro backdrop.**

**Allocation analysis (post-offer, "how was this IPO actually allocated"):**
weak. The IPO-results ("Halka Arzı Sonuçları") notice itself is only
recovered for 9/31 companies (29%) — a document-acquisition gap, not
extraction — but *when* it is present, `retail_allocated_shares`/
`institutional_allocated_shares`/`retail_participant_count` extract
reliably (7/9, 78%). `oversubscription_ratio_overall` (the one
allocation feature `expert_v0` scores) and tranche-level breakdowns
remain fragile or unimplemented even then. **Usable only for the ~9
companies with a recovered IPO-results notice, and even there only for
allocated-share/participant counts, not subscription-multiple detail.**

**Market-regime analysis (macro conditions independent of any one IPO):**
strong. All 9 EVDS features are `RELIABLE_NOW`. `post_offer_market_value_of_offering`/
`over_allotment_greenshoe_amount`/`lead_intermediary_institution`/
`listing_market_segment` (SPK's completed-record fields, 100%,
structured) add reliable per-offering context once completed. **This is
the system's single strongest coherent capability.**

**Retrospective outcome analysis (did this IPO perform well after
listing):** strong, but lives in a separate, already-validated module
(`ipo_outcomes`) that isn't wired into the feature catalog at all.
`first_day_return`/`return_5d` are ~97-100% covered across the 29-row
outcomes store; `return_20d`/`return_3m` degrade to 76%/52% simply
because not enough trading days have elapsed yet for recent listings —
an honest, time-bound gap, not a data-quality one. `trading_start_conflict`
is 0/29 — SPK's own completed-record trading-start date has never
disagreed with the market-price bulletin data in practice. **This is a
second strong coherent capability, but it's invisible to the catalog
audited above** (`post_ipo_price_performance_signal` is cataloged as
`market_data.first_day_trading_performance`/unimplemented, not as a
pointer to `ipo_outcomes`).

## Closing synthesis

**1. Strongest information already possessed.** Two things, both
structured-API-sourced with zero extraction risk: (a) the 9 EVDS
macro/market-context features (TCMB policy rate, TLREF, CPI, BIST-100
trailing return/volatility/drawdown) — 100% coverage, zero conflicts,
across every company in the cohort; and (b) SPK's own completed-IPO
record fields (`post_offer_market_value_of_offering`,
`over_allotment_greenshoe_amount`, `lead_intermediary_institution`,
`listing_market_segment`) — also 100%/zero-conflict, though
structurally post-offer-only. A close third: `ipo_outcomes`' market-price-based
return calculations (97-100% for 1d/5d) — reliable and already fixed
against a real baseline bug (SARAE validation, see prior session notes)
— but currently disconnected from the feature catalog.

**2. Weakest / most fragile information.** Everything sourced from
free-text extraction against the price determination report — every
`reported_*` multiple and every `derived_financial.*` ratio is
0–22% available *even conditional on the report being readable*
(18/31 companies have one). This is a genuine extraction-quality
problem (regex/table-matching), not a document-acquisition one, and it
is `expert_v0`'s second-heaviest category (30% of `total_score`,
`headline_discount_percentage` alone weighted 40 within it). Close
behind: `offering_price` itself, the single most foundational valuation
fact in the whole catalog, extracts in only 1/20 (5%) of companies with
both base documents readable — a severity not previously isolated in
prior sessions' per-feature blocker matrices (which focused on
`offering_structure`/`fundamental_quality`, not `valuation`'s core
price field).

**3. `expert_v0` requirements poorly matched to actual data availability.**
`fundamental_quality` (50% of `total_score`, coverage_threshold 60%) is
the worst-matched category outright: its weighted average conditional
coverage across the cohort is roughly 8%, an order of magnitude below
the gate, and two of its three `presence`-kind features
(`business_description`) plus two `related_party`/`litigation`
disclosure features have **no extractor implemented at all** — the
category cannot pass regardless of how much the *existing* regexes are
tuned. `valuation` (30% of `total_score`) is nearly as poorly matched:
its two heaviest-weighted scored features
(`headline_discount_percentage` weight 40, `earnings_multiple_at_offer`
weight 35) sit at 44%/11% conditional coverage. `offering_structure`
(20%) is the closest to viable — `capital_increase_ratio` (weight 40)
is genuinely strong post-fix — but `subscription_window`/
`distribution_method` (weight 15 each) remain weak specifically because
this session confirmed the multi-session cutoff-date regex fixes never
actually reach this catalog field (see the `subscription_window` note
above) — a real, previously-undocumented distinction between "the
cutoff-boundary problem is fixed" and "the feature itself is fixed."

**4. Missing information genuinely worth future engineering work,**
ranked by value-for-effort:
   - **Near-zero cost, real value — wiring, not new extraction:**
     `sector_classification` (an existing deterministic name-based
     classifier, `kap.sector.classify_sector`, is already used
     internally but was never exposed as this catalog fact) and
     `broader_index_level_at_offer` (the same EVDS BIST-100 series
     already powering `bist100_return_*` was never also exposed at this
     feature_id) — **both wired in the same-day follow-up pass below,
     now `RELIABLE_NOW`, 100% coverage.** `post_ipo_price_performance_signal`/
     `recent_comparable_ipo_performance` were *also* flagged here
     originally as "the data already exists in `ipo_outcomes`" — on
     reflection (and per explicit instruction in the follow-up pass)
     that framing was wrong: `ipo_outcomes` is post-offer *outcome*
     data, and wiring it into either feature — one of which is
     `pre_offer`-timed — would leak post-offer knowledge into an
     entry-decision input. These two remain open, and are **not** the
     same zero-cost class as the two that were wired; see the "Update"
     section below for what closing them would actually require.
   - **Moderate cost, moderate value:** a `business_description`
     extractor (confirmed to have zero implementation, unlike every
     other `fundamental_quality` presence field, which at least attempt
     extraction) — most base prospectuses now recover cleanly after the
     `6df671a` document-selection fix, so the source text is usually
     there; and fixing `kap.financials`' first-vs-real "Bilanço" table
     match (a documented, real bug from a prior session, not a new
     finding) which likely explains a meaningful share of the
     near-zero `financial_series`/`derived_financial` coverage even
     when the price determination report is readable.
   - **High cost, deferred for a real reason (not neglect):** a safe
     `reported_pe`/multiple extractor was previously investigated and
     deliberately left alone because naive patterns risk silently
     grabbing a **peer company's** multiple instead of the issuer's own
     from comparison tables — a correctness risk judged worse than the
     current honest gap; this audit did not re-litigate that judgment.
   - **Not worth pursuing under current constraints:** true
     `peer_group_comparables` (would need a genuinely new external
     cross-company valuation-multiples feed — no free official BIST/SPK
     source for this was identified in any prior probe) and
     `allocation_by_investor_category`/`demand_to_supply_ratio_by_tranche`/
     `institutional_investor_demand_signal` (no extractor exists and the
     source IPO-results notice itself is only recovered for 29% of
     companies to begin with — document acquisition would need to
     improve first for a new extractor here to matter).

## Update: closed the near-zero-cost wiring gaps (same day, follow-up to `8f2888c`)

Wired the two §4 "near-zero cost, real value" findings that had an
*existing* catalog entry to attach to (no new `FeatureSpec` was added —
see below for why the third finding, `ipo_outcomes`-backed features,
was explicitly **not** wired). No extractor, scoring weight, threshold,
category definition, or coverage gate was touched.

**`sector_classification`** (`decision/catalog.py`, `market_context`,
mandatory): `required_source_fields` changed from
`("kap_extraction.sector_code",)` — a field with no extractor — to a
new `("kap_sector.classification",)`, resolved by a new
`decision/audit.py::_resolve_kap_sector_field`, which reads
`CompanyDecisionInputs.sector` — already `kap.sector.classify_sector`
applied to the same company-name resolution every other part of the
audit already used internally for `SECTOR_INAPPLICABLE_METRICS` gating.
No new extraction logic; one new resolver dispatch entry.

**`broader_index_level_at_offer`** (`decision/catalog.py`,
`market_context`, optional): its `required_source_fields`
(`market_data.bist_index_level`) was already correctly named — nothing
was populating that key. `evds/features.py::build_market_context_snapshot`
now also computes it via the pre-existing `latest_value()` helper (the
same one already used for `policy_rate`/`tlref_rate`) applied to the
same `bist100_index` series already powering `bist100_return_20d/60d/120d`
— one new `if` block, no new series, no new fetch.

### Leakage-safety verification (offline, no live KAP call needed)

`historical_dataset.filtering.market_context_as_of` already slices
`bist100_index` to `observation_date <= cutoff_date` before calling
`build_market_context_snapshot` — since the new feature reuses that same
sliced input, it inherited cutoff-safety automatically, with no change
needed in `historical_dataset` at all. Verified directly against the
real cached EVDS series (`data/cache/evds`, cached through 2026-08-06)
and the 12 real historical cutoffs already resolved in
`data/cache/historical_dataset/v1/dataset.jsonl`: for every one, the
resolved `bist_index_level.as_of_date` matches its own cutoff date
exactly, never later — e.g. `QUICK` cutoff `2026-07-31` →
`as_of_date=2026-07-31` (not `2026-08-06`, despite the cache holding
five more weeks of data). `sector_classification` needs no cutoff logic
at all (company legal name doesn't change across the offering; this
exact company-name flow already predated this change) — cross-checked
`kap.sector.classify_sector` offline against all 29 real cached company
names, all classify as expected (4 REITs, 1 insurer `QUICK`, 24
`standard`).

A live rerun of `scripts/build_historical_ipo_dataset.py` to double-check
end-to-end was attempted but hit KAP's rate limit (HTTP 429, from this
session's earlier live audit runs) partway through an unrelated code
path (`post_offer_evidence`'s cutoff-tier-2/3 attachment fetch) — not
something this change touches. The offline verification above is the
stronger check for this specific property anyway (it directly compares
`as_of_date` against real historical cutoffs against a cache that
provably contains post-cutoff data, which a live rerun wouldn't add).

### Re-run capability audit results (same 31-company cohort, `scripts/audit_capability_report.py`)

| Feature | Before | After | Conflicts | New class |
|---|---|---|---|---|
| `sector_classification` | 0/31 (0%) | **31/31 (100%)** | 0 | `RELIABLE_NOW` |
| `broader_index_level_at_offer` | 0/31 (0%) | **31/31 (100%)** | 0 | `RELIABLE_NOW` |

Every other one of the 66 catalog features' `available_count` is
byte-for-byte unchanged (diffed programmatically against the
pre-wiring JSON snapshot) — this was a purely additive, isolated change.
Both newly-wired features are outside every `expert_v0`-scored category
(confirmed in `scoring_config.py`: the 17 scored features span only
`fundamental_quality`/`valuation`/`offering_structure`), so `expert_v0`'s
`total_score`, `confidence_score`, category coverage gates, and the
historical cohort's decision signals (still 0/9 usable, per the
unresolved `fundamental_quality`/`valuation` gaps documented above) are
**provably unchanged** by this pass — not just "not intended to
change," but structurally incapable of changing, since neither feature
appears in any scored category's feature list.

### Which reliable existing data is still unused

- **`bist100_volume`** — an EVDS series this project already fetches
  and caches (`evds/registry.py`, `TP.MK.ISL.HC`, alongside
  `bist100_index`) but `build_market_context_snapshot` has never
  computed anything from it, and no catalog feature references it.
  Genuinely idle, reliable, already-cached data — but there is no
  existing catalog entry to wire it into (unlike the two features
  above), so adding one was out of scope for this pass (would be a new
  feature, not a wiring fix).
- **Most of `SpkIpoRecord`'s remaining fields** (`halka_arz_orani`,
  `ortak_satis_bin_tl`, `nakit_sermaye_artisi_bin_tl`,
  `satisa_hazir_bekletilen_pay_tutari_bin_tl`,
  `satisa_sunulan_toplam_tutar_bin_tl`/`_bin_abd_dolari`,
  `mevcut_sermaye_bin_tl`, `yeni_sermaye_bin_tl`, `halka_arz_sekli`,
  `halka_arz_fiyati_tl`) — inspected all 19 fields on the model.
  4 were already wired pre-existing (`post_offer_market_value_of_offering`,
  `over_allotment_greenshoe_amount`, `lead_intermediary_institution`,
  `listing_market_segment`), 2 are already used by `ipo_outcomes`
  (`halka_arz_fiyati_tl`, `borsada_islem_gorme_tarihi` — correctly, as
  retrospective-label inputs, not decision features). The other 9 are
  each the **post-offer, SPK-published counterpart of an existing
  pre_offer catalog feature** — e.g. `halka_arz_fiyati_tl` vs.
  `offering_price`, `halka_arz_orani` vs. `capital_increase_ratio`,
  `ortak_satis_bin_tl` vs. `secondary_sale_shares`/`secondary_sale_ratio`,
  `halka_arz_sekli` vs. `distribution_method`. Wiring any of these into
  their pre-offer sibling feature would be exactly the leakage this
  task's brief explicitly forbids ("do not use post-offer SPK/KAP
  knowledge as historical entry features unless independently proven
  available by the cutoff") — SPK only publishes `IlkHalkaArzVerileriBilgi`
  once the offering is complete, so none of these 9 are available by
  any pre-offer cutoff. Deliberately left unwired.
- **`ipo_outcomes`'s full return/drawdown/relative-performance series**
  (97-100% coverage for `first_day_return`/`return_5d` across 29
  companies, already validated against a real baseline bug in a prior
  session) remains, per this task's explicit instruction, retrospective-label-only
  — not wired into `post_ipo_price_performance_signal` or
  `recent_comparable_ipo_performance` despite both being flagged in the
  original audit as "the data already exists." Closing those two
  specifically would need either a genuinely new external peer-multiples
  feed (`peer_group_comparables`) or a new cross-company as-of-cutoff
  aggregation layer (for `recent_comparable_ipo_performance` — "which
  *other* IPOs had already priced and started trading, as of *this*
  IPO's own cutoff" is a real, unbuilt capability, not a simple wire),
  not the same zero-cost pattern as `sector_classification`/
  `broader_index_level_at_offer`. Left as an open, correctly-scoped-out
  gap.

### `expert_v0` requirements still fundamentally mismatched with real data availability

Unchanged by this pass, since neither wired feature is scored:
`fundamental_quality` (50% of `total_score`) still has two
zero-extractor presence fields (`business_description`,
`related_party_transactions_disclosure`/`litigation_exposure_disclosure`
are catalog-only, not scored, but the same "no extractor" pattern
recurs in scored `business_description`) and near-zero conditional
financial-ratio coverage; `valuation` (30%) still has
`headline_discount_percentage`/`earnings_multiple_at_offer` at
44%/11% conditional; `offering_structure` (20%) still has
`subscription_window`/`distribution_method` weak for the same reason
documented above (the cutoff-regex fixes intentionally never reach this
catalog field). The historical cohort's `expert_v0` decisions remain
0/9 usable. This pass improved the coverage *audit*'s honesty and
completeness, not `expert_v0`'s own scored inputs — exactly the scope
the task asked for.
