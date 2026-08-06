"""Evaluate the decision-feature catalog (:mod:`halka_arz_advisor.decision.catalog`)
against one company's currently available data.

Purely a reporting pass over data this project's existing KAP/SPK
pipeline already produced: no new fetch, no new regex extractor, no
scoring/weighting/normalization, and no conflict *resolution* — a field
the deterministic extraction layer already marked ``"conflicting"``
(see :func:`halka_arz_advisor.kap.extraction.merge_field_observations`)
is reported ``CONFLICTED`` here with both observations preserved, never
picked between.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from ..kap.derived_financials import DERIVED_FINANCIAL_FEATURE_NAMES, compute_derived_financial_features
from ..kap.extraction import ExtractedFacts, FIELD_NAMES
from ..kap.financials import FINANCIAL_METRIC_NAMES, FinancialObservation
from ..kap.models import KapDisclosure
from ..kap.sector import SECTOR_INAPPLICABLE_METRICS, Sector, classify_sector
from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord
from .catalog import get_feature
from .models import FeatureSpec

FeatureStatus = Literal[
    "AVAILABLE",
    "DERIVABLE",
    "MISSING_FIELD",
    "MISSING_DOCUMENT",
    "CONFLICTED",
    "POST_OFFER_ONLY",
    "NOT_APPLICABLE",
]

# Lower number = more "blocking"/actionable; used to combine several
# sub-resolutions (a feature can require more than one source field, or
# a derived feature can have several dependencies) into a single status
# — the worst one wins. AVAILABLE/DERIVABLE (fully resolved) always
# lose to anything else being combined with them.
_STATUS_PRIORITY: dict[FeatureStatus, int] = {
    "CONFLICTED": 0,
    "MISSING_DOCUMENT": 1,
    "MISSING_FIELD": 2,
    "POST_OFFER_ONLY": 3,
    "NOT_APPLICABLE": 4,
    "DERIVABLE": 5,
    "AVAILABLE": 6,
}

# SPK completed-IPO-record fields where a null value plausibly means
# "this deal structurally had none of this" rather than "unreported" —
# kept to an explicit, narrow allowlist rather than assumed for every
# nullable field, since most of this schema's nullability is undocumented
# as to *why* (see spk.models's own module docstring).
_SPK_FIELDS_WHERE_NULL_MEANS_NOT_APPLICABLE = frozenset({"ek_satis_tutari_bin_tl"})

# The handful of core pre-offer fields the data_confidence meta-features
# inspect directly (see catalog.py's data_confidence entries) — chosen
# as the fields a directional decision is most likely to hinge on.
_CORE_FIELDS_FOR_CONFIDENCE: tuple[str, ...] = (
    "offering_price",
    "distribution_method",
    "subscription_start_date",
    "subscription_end_date",
    "total_offered_shares",
    "capital_increase_ratio",
)


def _json_safe(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class FeatureEvidence:
    """One piece of evidence backing (or explaining the absence of) a
    feature's value — JSON-safe (see :meth:`as_dict`)."""

    field_name: str  # the namespaced required_source_fields entry this evidence answers
    value: object
    status: str  # underlying FactStatus, or a short reason string for non-KAP-extraction fields
    disclosure_id: str | None = None
    document_type: str | None = None
    page_number: int | None = None
    extraction_method: str | None = None

    def as_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "value": _json_safe(self.value),
            "status": self.status,
            "disclosure_id": self.disclosure_id,
            "document_type": self.document_type,
            "page_number": self.page_number,
            "extraction_method": self.extraction_method,
        }


@dataclass(frozen=True, slots=True)
class FeatureAuditResult:
    feature_id: str
    category: str
    status: FeatureStatus
    evidence: tuple[FeatureEvidence, ...] = field(default_factory=tuple)
    missing_dependencies: tuple[str, ...] = field(default_factory=tuple)
    notes: str | None = None

    def as_dict(self) -> dict:
        return {
            "feature_id": self.feature_id,
            "category": self.category,
            "status": self.status,
            "evidence": [e.as_dict() for e in self.evidence],
            "missing_dependencies": list(self.missing_dependencies),
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class CompanyDecisionInputs:
    """Everything the audit is allowed to look at for one company — all
    of it already produced by the existing pipeline (nothing is fetched
    here)."""

    spk_record_id: str
    spk_record: SpkIpoRecord | None
    application_record: SpkIpoApplicationRecord | None
    facts: ExtractedFacts | None
    disclosures: tuple[KapDisclosure, ...]
    financial_observations: tuple[FinancialObservation, ...] = ()
    # The company's registered legal name — used only for
    # halka_arz_advisor.kap.sector's deterministic, name-based sector
    # classification (never inferred from PDF text). Falls back to the
    # first disclosure's own company_name when not given explicitly.
    company_name: str | None = None

    @property
    def sector(self) -> Sector:
        name = self.company_name or next((d.company_name for d in self.disclosures if d.company_name), None)
        return classify_sector(name)


def _combine(statuses: list[FeatureStatus]) -> FeatureStatus:
    return min(statuses, key=lambda s: _STATUS_PRIORITY[s])


def _document_readable(document_type: str, disclosures: tuple[KapDisclosure, ...]) -> KapDisclosure | None:
    for d in disclosures:
        if d.document_type != document_type:
            continue
        if d.pdf_status == "ok" or d.ocr_status in ("ocr_ok", "ocr_partial"):
            return d
    return None


def _document_exists(document_type: str, disclosures: tuple[KapDisclosure, ...]) -> bool:
    return any(d.document_type == document_type for d in disclosures)


def _resolve_kap_extraction_field(
    field_ref: str, name: str, inputs: CompanyDecisionInputs, acceptable_sources: tuple[str, ...]
) -> tuple[FeatureStatus, FeatureEvidence]:
    if name not in FIELD_NAMES:
        readable = any(_document_readable(doc_type, inputs.disclosures) for doc_type in acceptable_sources)
        note = (
            "no extractor implemented for this field yet"
            + ("; a readable source document does exist" if readable else "; no readable source document either")
        )
        return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status=note)

    if inputs.facts is None:
        if any(_document_readable(doc_type, inputs.disclosures) for doc_type in acceptable_sources):
            return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status="not_found")
        return "MISSING_DOCUMENT", FeatureEvidence(
            field_name=field_ref, value=None, status="no readable source document"
        )

    fact = getattr(inputs.facts, name)

    if fact.status == "extracted":
        source = fact.source
        return "AVAILABLE", FeatureEvidence(
            field_name=field_ref,
            value=fact.value,
            status="extracted",
            disclosure_id=source.disclosure_id if source else None,
            document_type=source.document_type if source else None,
            page_number=source.page_number if source else None,
            extraction_method=source.extraction_method if source else None,
        )

    if fact.status == "conflicting":
        # Evidence carries every disagreeing observation — never picks one.
        return "CONFLICTED", FeatureEvidence(
            field_name=field_ref,
            value=[obs.value for obs in fact.observations],
            status="conflicting",
            document_type=",".join(sorted({obs.source.document_type for obs in fact.observations})),
            extraction_method=",".join(sorted({obs.source.extraction_method for obs in fact.observations})),
        )

    # "not_found"
    if any(_document_readable(doc_type, inputs.disclosures) for doc_type in acceptable_sources):
        return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status="not_found")
    return "MISSING_DOCUMENT", FeatureEvidence(field_name=field_ref, value=None, status="no readable source document")


def _resolve_spk_ipo_record_field(field_ref: str, name: str, inputs: CompanyDecisionInputs) -> tuple[FeatureStatus, FeatureEvidence]:
    if inputs.spk_record is None:
        return "POST_OFFER_ONLY", FeatureEvidence(
            field_name=field_ref, value=None, status="no completed SPK IPO record available yet"
        )
    if not hasattr(inputs.spk_record, name):
        raise AttributeError(f"SpkIpoRecord has no field {name!r} (check catalog.py's required_source_fields)")
    value = getattr(inputs.spk_record, name)
    if value is None:
        status: FeatureStatus = (
            "NOT_APPLICABLE" if name in _SPK_FIELDS_WHERE_NULL_MEANS_NOT_APPLICABLE else "MISSING_FIELD"
        )
        return status, FeatureEvidence(field_name=field_ref, value=None, status="null on the SPK record")
    return "AVAILABLE", FeatureEvidence(field_name=field_ref, value=value, status="spk_ipo_record")


def _resolve_spk_application_field(field_ref: str, name: str, inputs: CompanyDecisionInputs) -> tuple[FeatureStatus, FeatureEvidence]:
    if inputs.application_record is None:
        return "MISSING_DOCUMENT", FeatureEvidence(
            field_name=field_ref, value=None, status="no SPK IPO application record found"
        )
    if not hasattr(inputs.application_record, name):
        raise AttributeError(f"SpkIpoApplicationRecord has no field {name!r} (check catalog.py)")
    value = getattr(inputs.application_record, name)
    if value is None:
        return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status="null on the application record")
    return "AVAILABLE", FeatureEvidence(field_name=field_ref, value=value, status="spk_application")


def _resolve_kap_document_field(field_ref: str, document_type: str, inputs: CompanyDecisionInputs) -> tuple[FeatureStatus, FeatureEvidence]:
    disclosure = _document_readable(document_type, inputs.disclosures)
    if disclosure is not None:
        return "AVAILABLE", FeatureEvidence(
            field_name=field_ref,
            value=disclosure.notification_url,
            status="document readable",
            disclosure_id=disclosure.disclosure_id,
            document_type=disclosure.document_type,
        )
    exists_but_unreadable = _document_exists(document_type, inputs.disclosures)
    note = "document found but not readable (pdf_status/ocr_status)" if exists_but_unreadable else "no such document found"
    return "MISSING_DOCUMENT", FeatureEvidence(field_name=field_ref, value=None, status=note)


def _resolve_financial_series_field(
    field_ref: str, name: str, inputs: CompanyDecisionInputs, acceptable_sources: tuple[str, ...]
) -> tuple[FeatureStatus, FeatureEvidence]:
    if name not in FINANCIAL_METRIC_NAMES:
        readable = any(_document_readable(doc_type, inputs.disclosures) for doc_type in acceptable_sources)
        note = (
            "no extractor implemented for this metric yet"
            + ("; a readable source document does exist" if readable else "; no readable source document either")
        )
        return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status=note)

    if name in SECTOR_INAPPLICABLE_METRICS.get(inputs.sector, frozenset()):
        return "NOT_APPLICABLE", FeatureEvidence(
            field_name=field_ref, value=None, status=f"not a meaningful concept for sector={inputs.sector!r}"
        )

    observations = tuple(obs for obs in inputs.financial_observations if obs.metric_name == name)
    if not observations:
        if any(_document_readable(doc_type, inputs.disclosures) for doc_type in acceptable_sources):
            return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status="not_found")
        return "MISSING_DOCUMENT", FeatureEvidence(
            field_name=field_ref, value=None, status="no readable source document"
        )

    # Every explicitly labelled period found is kept as evidence — never
    # collapsed to a single "the" value, since there isn't one (see
    # halka_arz_advisor.kap.financials's module docstring).
    first = observations[0]
    return "AVAILABLE", FeatureEvidence(
        field_name=field_ref,
        value=tuple((obs.period_end, obs.value) for obs in observations),
        status=f"{len(observations)} period(s) extracted",
        disclosure_id=first.source.disclosure_id,
        document_type=first.source.document_type,
        page_number=first.source.page_number,
        extraction_method=first.source.extraction_method,
    )


def _resolve_derived_financial_field(
    field_ref: str, name: str, inputs: CompanyDecisionInputs, acceptable_sources: tuple[str, ...]
) -> tuple[FeatureStatus, FeatureEvidence]:
    if name not in DERIVED_FINANCIAL_FEATURE_NAMES:
        return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status="no such derived financial feature")

    derived = compute_derived_financial_features(inputs.financial_observations, inputs.facts, sector=inputs.sector)
    result = getattr(derived, name)

    if result.status == "computed":
        return "DERIVABLE", FeatureEvidence(
            field_name=field_ref,
            value=result.value,
            status=f"computed (formula v{result.formula_version})",
        )

    if result.status == "not_applicable":
        return "NOT_APPLICABLE", FeatureEvidence(field_name=field_ref, value=None, status=result.unavailable_reason or "not applicable")

    # "unavailable" — distinguished the same way every other resolver
    # distinguishes "field genuinely missing" from "no source document
    # was even readable" (see _resolve_kap_extraction_field).
    if any(_document_readable(doc_type, inputs.disclosures) for doc_type in acceptable_sources):
        return "MISSING_FIELD", FeatureEvidence(field_name=field_ref, value=None, status=result.unavailable_reason or "unavailable")
    return "MISSING_DOCUMENT", FeatureEvidence(
        field_name=field_ref, value=None, status=result.unavailable_reason or "unavailable"
    )


def _resolve_market_data_field(field_ref: str, name: str) -> tuple[FeatureStatus, FeatureEvidence]:
    return "MISSING_DOCUMENT", FeatureEvidence(
        field_name=field_ref, value=None, status="no external market-data source is implemented in this project"
    )


def _resolve_field(field_ref: str, inputs: CompanyDecisionInputs, acceptable_sources: tuple[str, ...]) -> tuple[FeatureStatus, FeatureEvidence]:
    namespace, _, name = field_ref.partition(".")
    if namespace == "kap_extraction":
        return _resolve_kap_extraction_field(field_ref, name, inputs, acceptable_sources)
    if namespace == "spk_ipo_record":
        return _resolve_spk_ipo_record_field(field_ref, name, inputs)
    if namespace == "spk_application":
        return _resolve_spk_application_field(field_ref, name, inputs)
    if namespace == "kap_document":
        return _resolve_kap_document_field(field_ref, name, inputs)
    if namespace == "financial_series":
        return _resolve_financial_series_field(field_ref, name, inputs, acceptable_sources)
    if namespace == "derived_financial":
        return _resolve_derived_financial_field(field_ref, name, inputs, acceptable_sources)
    if namespace == "market_data":
        return _resolve_market_data_field(field_ref, name)
    raise ValueError(f"unrecognized required_source_fields namespace in {field_ref!r}")


def _core_field_facts(inputs: CompanyDecisionInputs):
    if inputs.facts is None:
        return
    for name in _CORE_FIELDS_FOR_CONFIDENCE:
        fact = getattr(inputs.facts, name, None)
        if fact is not None and fact.status in ("extracted", "conflicting"):
            yield name, fact


_DERIVED_COMPUTE = {}


def _register(feature_id):
    def _wrap(fn):
        _DERIVED_COMPUTE[feature_id] = fn
        return fn

    return _wrap


@_register("implied_offer_size_value")
def _compute_implied_offer_size_value(dep_results: dict[str, FeatureAuditResult], inputs: CompanyDecisionInputs):
    price = next(e.value for e in dep_results["offering_price"].evidence if e.field_name == "kap_extraction.offering_price")
    shares = next(
        e.value for e in dep_results["total_offered_shares"].evidence if e.field_name == "kap_extraction.total_offered_shares"
    )
    return float(price) * float(shares)


@_register("final_allocation_price")
def _compute_final_allocation_price(dep_results: dict[str, FeatureAuditResult], inputs: CompanyDecisionInputs):
    return next(e.value for e in dep_results["offering_price"].evidence if e.field_name == "kap_extraction.offering_price")


@_register("cross_document_field_corroboration")
def _compute_cross_document_field_corroboration(dep_results, inputs: CompanyDecisionInputs):
    corroborated = [name for name, fact in _core_field_facts(inputs) if len(fact.observations) > 1]
    single_source = [name for name, fact in _core_field_facts(inputs) if len(fact.observations) <= 1]
    return {"corroborated_fields": corroborated, "single_source_fields": single_source}


@_register("ocr_reliance")
def _compute_ocr_reliance(dep_results, inputs: CompanyDecisionInputs):
    ocr_fields = [
        name
        for name, fact in _core_field_facts(inputs)
        if any(obs.source.extraction_method == "ocr" for obs in fact.observations)
    ]
    return {"ocr_reliant_fields": ocr_fields, "relies_on_ocr": bool(ocr_fields)}


@_register("single_source_field_flag")
def _compute_single_source_field_flag(dep_results, inputs: CompanyDecisionInputs):
    single_source = [name for name, fact in _core_field_facts(inputs) if len(fact.observations) == 1]
    return {"single_source_fields": single_source, "has_single_source_field": bool(single_source)}


def _evaluate_direct(spec: FeatureSpec, inputs: CompanyDecisionInputs) -> FeatureAuditResult:
    resolutions = [_resolve_field(ref, inputs, spec.acceptable_sources) for ref in spec.required_source_fields]
    statuses = [status for status, _evidence in resolutions]
    evidence = tuple(evidence for _status, evidence in resolutions)
    combined = _combine(statuses)
    missing = tuple(e.field_name for s, e in resolutions if s != "AVAILABLE") if combined != "AVAILABLE" else ()
    return FeatureAuditResult(
        feature_id=spec.feature_id, category=spec.category, status=combined, evidence=evidence, missing_dependencies=missing
    )


def _evaluate_derived(spec: FeatureSpec, inputs: CompanyDecisionInputs, memo: dict[str, FeatureAuditResult]) -> FeatureAuditResult:
    dep_results = {dep_id: _audit_feature(dep_id, inputs, memo) for dep_id in spec.derivation_dependencies}
    worst = _combine([r.status for r in dep_results.values()])

    if worst not in ("AVAILABLE", "DERIVABLE"):
        failed = tuple(dep_id for dep_id, r in dep_results.items() if r.status not in ("AVAILABLE", "DERIVABLE"))
        return FeatureAuditResult(
            feature_id=spec.feature_id, category=spec.category, status=worst, missing_dependencies=failed
        )

    compute = _DERIVED_COMPUTE.get(spec.feature_id)
    if compute is None:
        raise NotImplementedError(f"no derivation implemented for derived feature {spec.feature_id!r}")
    value = compute(dep_results, inputs)
    evidence = (
        FeatureEvidence(field_name=f"derived:{spec.feature_id}", value=value, status="computed"),
    )
    return FeatureAuditResult(feature_id=spec.feature_id, category=spec.category, status="DERIVABLE", evidence=evidence)


def _audit_feature(feature_id: str, inputs: CompanyDecisionInputs, memo: dict[str, FeatureAuditResult]) -> FeatureAuditResult:
    if feature_id in memo:
        return memo[feature_id]
    spec = get_feature(feature_id)
    if spec.availability_kind == "direct":
        result = _evaluate_direct(spec, inputs)
    else:
        result = _evaluate_derived(spec, inputs, memo)
    memo[feature_id] = result
    return result


def audit_company(inputs: CompanyDecisionInputs) -> tuple[FeatureAuditResult, ...]:
    """Evaluate every feature in :data:`halka_arz_advisor.decision.catalog.FEATURE_CATALOG`
    against ``inputs``, in catalog order."""
    from .catalog import FEATURE_CATALOG

    memo: dict[str, FeatureAuditResult] = {}
    return tuple(_audit_feature(spec.feature_id, inputs, memo) for spec in FEATURE_CATALOG)
