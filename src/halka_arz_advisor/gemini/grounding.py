"""Post-validates that Gemini's structured output stays within what the
deterministic decision result actually supports.

Complements :mod:`halka_arz_advisor.gemini.schema` (JSON shape and
citation validation) rather than replacing it: Gemini is only allowed to
paraphrase the deterministic signal, its triggered hard rules, and the
engine's own precomputed top positive/negative feature contributions
(see :func:`halka_arz_advisor.decision.engine.top_positive_contributions` /
:func:`~halka_arz_advisor.decision.engine.top_negative_contributions`).
It is never allowed to introduce a ``positive_factors``/``negative_factors``
item, or a ``decision_explanation``, that isn't traceable back to those —
an item that fails this check most likely drew on the raw document text
(or was invented outright) rather than the supplied deterministic
result, so the whole response is rejected here exactly like an invented
citation already is in schema.py: the caller
(:func:`halka_arz_advisor.gemini.analysis.analyze_company`) retries once,
then falls back to :func:`halka_arz_advisor.decision.explain.format_explanation`
— the deterministic formatter is always what's shown when this can't
prove grounding, never Gemini's unverified prose.
"""

from __future__ import annotations

from ..decision.catalog import get_feature
from ..decision.engine import DecisionResult, FeatureContribution, top_negative_contributions, top_positive_contributions
from ..kap.text import fold_turkish
from .exceptions import GeminiOutputError
from .schema import AnalysisOutput

_SIGNAL_LABELS_TR: dict[str, str] = {
    "participate": "Katıl",
    "limited_participation": "Sınırlı katılım",
    "skip": "Pas geç",
    "insufficient_data": "Yetersiz veri",
}


def _contribution_terms(contributions: tuple[FeatureContribution, ...]) -> set[str]:
    terms: set[str] = set()
    for contribution in contributions:
        terms.add(fold_turkish(contribution.feature_id))
        try:
            terms.add(fold_turkish(get_feature(contribution.feature_id).title))
        except KeyError:
            pass
    return terms


def _negative_terms(decision_result: DecisionResult) -> set[str]:
    terms = _contribution_terms(top_negative_contributions(decision_result))
    for rule in decision_result.hard_rules:
        if rule.triggered:
            terms.add(fold_turkish(rule.rule_id))
    return terms


def _grounded(item: str, vocabulary: set[str]) -> bool:
    folded_item = fold_turkish(item)
    return any(term and term in folded_item for term in vocabulary)


def validate_grounding(output: AnalysisOutput, decision_result: DecisionResult) -> None:
    """Raises :class:`~halka_arz_advisor.gemini.exceptions.GeminiOutputError`
    the moment any ``positive_factors``/``negative_factors`` item, or the
    ``decision_explanation``, can't be traced back to ``decision_result``.

    A category/vocabulary that's empty (e.g. no precomputed positive
    contributions at all) means the corresponding output list must be
    empty too — there is nothing deterministic for a non-empty list to
    have paraphrased.
    """
    positive_terms = _contribution_terms(top_positive_contributions(decision_result))
    for item in output.positive_factors:
        if not _grounded(item, positive_terms):
            raise GeminiOutputError(
                f"positive_factors item {item!r} is not grounded in the deterministic result's "
                "precomputed positive contributions — rejecting unsupported factor"
            )

    negative_terms = _negative_terms(decision_result)
    for item in output.negative_factors:
        if not _grounded(item, negative_terms):
            raise GeminiOutputError(
                f"negative_factors item {item!r} is not grounded in the deterministic result's "
                "precomputed negative contributions or triggered hard rules — rejecting unsupported factor"
            )

    signal_terms = {fold_turkish(decision_result.signal), fold_turkish(_SIGNAL_LABELS_TR.get(decision_result.signal, ""))}
    if not _grounded(output.decision_explanation, signal_terms):
        raise GeminiOutputError(
            "decision_explanation does not reference the deterministic signal it was given — "
            "rejecting ungrounded explanation"
        )
