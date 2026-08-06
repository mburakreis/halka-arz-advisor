"""Builds a :class:`DecisionSnapshot` — a single, normalized bundle of
everything :mod:`halka_arz_advisor.decision.engine` scores a company
from. No new extraction, no new external data source: every field here
is either passed straight through from
:class:`~halka_arz_advisor.decision.audit.CompanyDecisionInputs` or
computed by calling the existing, already-tested
:func:`~halka_arz_advisor.decision.audit.audit_company` and
:func:`~halka_arz_advisor.kap.derived_financials.compute_derived_financial_features`.

Building the snapshot through :func:`audit_company` (rather than
re-deriving availability/conflict/sector-applicability logic in the
engine) is deliberate: that function is the project's single source of
truth for "is this feature available, conflicting, or genuinely not
applicable to this company's sector" — the engine only ever reads its
output, never re-implements it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..kap.derived_financials import DerivedFinancialFeatures, compute_derived_financial_features
from ..kap.extraction import ExtractedFacts
from ..kap.financials import FinancialObservation
from ..kap.models import KapDisclosure
from ..kap.sector import Sector
from .audit import CompanyDecisionInputs, FeatureAuditResult, audit_company


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """A normalized, point-in-time combination of every existing signal
    :mod:`halka_arz_advisor.decision.engine` scores a company from."""

    spk_record_id: str
    sector: Sector
    facts: ExtractedFacts | None
    financial_observations: tuple[FinancialObservation, ...]
    derived_features: DerivedFinancialFeatures
    audit_results: tuple[FeatureAuditResult, ...]
    disclosures: tuple[KapDisclosure, ...]
    generated_at: datetime

    def audit_result(self, feature_id: str) -> FeatureAuditResult | None:
        return next((r for r in self.audit_results if r.feature_id == feature_id), None)


def build_decision_snapshot(inputs: CompanyDecisionInputs, *, reference_date: datetime | None = None) -> DecisionSnapshot:
    """Combine ``inputs`` into a :class:`DecisionSnapshot` — the sole
    entry point :mod:`halka_arz_advisor.decision.engine` is meant to be
    called with. ``reference_date`` defaults to now; only ever
    overridden for reproducible testing of document-freshness scoring."""
    return DecisionSnapshot(
        spk_record_id=inputs.spk_record_id,
        sector=inputs.sector,
        facts=inputs.facts,
        financial_observations=inputs.financial_observations,
        derived_features=compute_derived_financial_features(inputs.financial_observations, inputs.facts, sector=inputs.sector),
        audit_results=audit_company(inputs),
        disclosures=inputs.disclosures,
        generated_at=reference_date or datetime.now(),
    )
