"""Deterministic plain-text rendering of a
:class:`~halka_arz_advisor.decision.engine.DecisionResult` — string
formatting only, no new computation and no LLM call. Does not touch
:mod:`halka_arz_advisor.gemini` or :mod:`halka_arz_advisor.telegram`;
wiring this into either is a separate, later step.
"""

from __future__ import annotations

from .engine import DecisionResult, displayable_category_score

_SIGNAL_LABELS_TR: dict[str, str] = {
    "participate": "KATIL",
    "limited_participation": "SINIRLI KATILIM",
    "skip": "GEÇ",
    "insufficient_data": "YETERSİZ VERİ",
}


def format_explanation(result: DecisionResult) -> str:
    """A human-readable Turkish summary of ``result`` — every number
    and rule already computed by
    :func:`~halka_arz_advisor.decision.engine.evaluate_decision`,
    simply laid out for reading; nothing here recomputes or reinterprets
    a score."""
    lines: list[str] = []

    label = _SIGNAL_LABELS_TR.get(result.signal, result.signal)
    lines.append(f"Sinyal: {label} ({result.signal})")
    total_str = f"{result.total_score:.1f} / 100" if result.total_score is not None else "yetersiz veri"
    lines.append(f"Toplam skor: {total_str}")
    lines.append(f"Güven skoru: {result.confidence_score:.1f} / 100")

    lines.append("")
    lines.append("Kategori skorları:")
    for category in result.category_scores:
        # A category below its coverage threshold ("durum: INSUFFICIENT")
        # never shows a partial numeric score here, even if one was
        # internally computed — see
        # halka_arz_advisor.decision.engine.displayable_category_score.
        display_score = displayable_category_score(category)
        score_str = f"{display_score:.1f} / 100" if display_score is not None else "yok"
        lines.append(f"  - {category.category}: {score_str} (kapsam: {category.coverage:.0%}, durum: {category.status})")

    lines.append("")
    lines.append("Güven bileşenleri:")
    for component in result.confidence_components:
        lines.append(f"  - {component.name}: {component.score:.1f} / 100 (ağırlık: %{component.weight * 100:.0f})")

    triggered_rules = [rule for rule in result.hard_rules if rule.triggered]
    if triggered_rules:
        lines.append("")
        lines.append("Tetiklenen kesin kurallar:")
        for rule in triggered_rules:
            lines.append(f"  - {rule.rule_id} ({rule.target}): {rule.reason}")

    if result.warnings:
        lines.append("")
        lines.append("Uyarılar:")
        for warning in result.warnings:
            lines.append(f"  - {warning}")

    lines.append("")
    lines.append(f"Kural sürümü: {result.rule_version} | Ağırlık seti sürümü: {result.weight_set_version}")

    return "\n".join(lines)
