"""Leakage-free historical IPO evaluation dataset.

For each completed IPO, persists a point-in-time snapshot of what this
project's deterministic pipeline could actually have known **on or
before the final subscription day** (the decision cutoff — see
:mod:`halka_arz_advisor.historical_dataset.cutoff`), alongside the
IPO's later market outcome as a separate label. Built entirely from
this project's existing abstractions — no parallel extraction, scoring,
or provenance logic:

- :mod:`halka_arz_advisor.kap` for disclosures/facts/financials and
  their real ``published_at`` provenance,
- :mod:`halka_arz_advisor.decision` (``audit``/``snapshot``/``engine``)
  for feature availability and the ``expert_v0`` decision itself,
- :mod:`halka_arz_advisor.evds` for the macro market-context snapshot,
  sliced as-of the cutoff from whatever is already cached,
- :mod:`halka_arz_advisor.ipo_outcomes` for the later market-outcome
  label, attached separately.

Two leakage rules hold everywhere in this package:

1. **A fact only enters a snapshot's features/decision if its own
   provenance proves it existed on or before the cutoff.** A KAP
   disclosure's ``published_at`` is trusted (it's KAP's own
   ``publishDate``); an issuer-IR-sourced supplementary disclosure is
   not (stamped with crawl time, not a real publish date — see
   :mod:`halka_arz_advisor.issuer_ir.ingest`) and is never used as
   *feature* evidence here. :class:`halka_arz_advisor.spk.models.SpkIpoRecord`
   (SPK's *completed*-IPO record) is used only for canonical identity
   and for the outcome label's own IPO dates — never as a feature
   source for the reconstructed decision, since this project has no
   per-record publish timestamp to prove any of its fields were
   knowable before the offer completed (every catalog feature that
   reads from it is already tagged ``offer_timing="post_offer"`` — see
   :mod:`halka_arz_advisor.decision.catalog`). If a fact's availability
   can't be proven this way, it's excluded, never guessed.

   The **cutoff itself** is the one deliberate exception, and it is not
   a contradiction of this rule but a consequence of what the cutoff
   *is*: evaluation-boundary metadata, not a decision feature (see
   :mod:`halka_arz_advisor.historical_dataset.cutoff`). An official
   document published *after* the subscription window closed — a KAP
   IPO-results notice, or an issuer-IR document — may be used **only**
   to read off the window's own explicitly-stated closing date, via
   :mod:`halka_arz_advisor.historical_dataset.post_offer_evidence`,
   which reads raw document text directly and never routes through
   :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`. No other
   fact from that same document — and no other post-offer document at
   all — ever enters a snapshot's features.
2. **The outcome label never feeds feature generation or scoring.**
   :func:`halka_arz_advisor.historical_dataset.snapshot_builder.build_historical_snapshot`
   computes ``audit_results``/``decision_result`` entirely first, and
   only then attaches ``outcome`` to the result — no function upstream
   of that attachment ever receives it as an argument.

This package changes no scoring weight, threshold, rule, Gemini prompt,
Telegram output, or exit logic — it only reconstructs, with proof of
timing, what the existing engine already computes.
"""

from .cutoff import CutoffResolution, CutoffSource, PostOfferCutoffEvidence, resolve_decision_cutoff
from .dataset_store import dataset_path, read_dataset, write_dataset
from .filtering import application_record_before_cutoff, disclosures_before_cutoff, end_of_day_istanbul, market_context_as_of
from .models import HISTORICAL_DATASET_VERSION, HistoricalIpoSnapshot, snapshot_to_dict
from .post_offer_evidence import collect_post_offer_cutoff_evidence
from .snapshot_builder import build_historical_snapshot

__all__ = [
    "CutoffResolution",
    "CutoffSource",
    "PostOfferCutoffEvidence",
    "resolve_decision_cutoff",
    "dataset_path",
    "read_dataset",
    "write_dataset",
    "application_record_before_cutoff",
    "disclosures_before_cutoff",
    "end_of_day_istanbul",
    "market_context_as_of",
    "HISTORICAL_DATASET_VERSION",
    "HistoricalIpoSnapshot",
    "snapshot_to_dict",
    "collect_post_offer_cutoff_evidence",
    "build_historical_snapshot",
]
