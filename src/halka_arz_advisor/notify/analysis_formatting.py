"""Plain, human-readable Telegram message for one company's Gemini
analysis — mirrors :mod:`halka_arz_advisor.notify.formatting`'s role
for SPK records.

Signal, total score, confidence, and category scores always come from
``record.decision_result`` — the deterministic
:class:`~halka_arz_advisor.decision.engine.DecisionResult`, the sole
source of truth for those numbers (see
:mod:`halka_arz_advisor.gemini.schema`'s module docstring: Gemini's own
output no longer even carries a signal or confidence). Gemini's
narrative (``record.llm_analysis``) is used for the explanation/positive-
factors/risks sections when available (``llm_status == "completed"``);
otherwise this falls back to
:func:`halka_arz_advisor.decision.explain.format_explanation` and the
deterministic top/bottom feature contributions — the message is never
just "no analysis available" as long as a decision could be computed.

Deliberately concise: never the raw JSON, never a whole prospectus
excerpt, never every source reference — a fixed handful of capped
sections plus up to 3 KAP notification URLs so the reader can go read
the primary source themselves.
"""

from __future__ import annotations

from ..decision.engine import displayable_category_score, top_negative_contributions, top_positive_contributions
from ..decision.explain import format_explanation
from ..gemini.models import AnalysisRecord
from ..kap.extraction import ExtractedFacts

MAX_POSITIVE_ITEMS = 3
MAX_RISK_ITEMS = 3
MAX_MISSING_ITEMS = 2
MAX_SOURCE_URLS = 3
MAX_RATIONALE_CHARS = 500
# Telegram's sendMessage text limit is 4096 UTF-16 code units; stay
# comfortably under it rather than relying on an exact boundary.
MAX_MESSAGE_CHARS = 3900

_SIGNAL_LABELS_TR: dict[str, str] = {
    "participate": "Katıl",
    "limited_participation": "Sınırlı katıl",
    "skip": "Pas geç",
    "insufficient_data": "Yetersiz veri",
}

_CATEGORY_LABELS_TR: dict[str, str] = {
    "fundamental_quality": "Temel nitelik",
    "valuation": "Değerleme",
    "offering_structure": "Arz yapısı",
}

_NO_ANALYSIS_RATIONALE_TR = (
    "Bu şirket için önbellekte yeterli belge metni bulunamadığından analiz yapılamadı."
)


def _sanitize(text: str) -> str:
    """Strip non-printable control characters and collapse whitespace —
    messages are sent as plain text (no Telegram parse_mode), so there's
    no markup to escape, but PDF-derived or model-generated text can
    still contain stray control characters or accidental newlines."""
    printable = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return " ".join(printable.split())


def _format_price(facts: ExtractedFacts) -> str:
    fact = facts.offering_price
    if fact.status != "extracted" or fact.value is None:
        return "Bilinmiyor"
    currency_fact = facts.currency
    currency = "TL" if currency_fact.status == "extracted" and currency_fact.value == "TRY" else ""
    return f"{fact.value:.2f} {currency}".strip()


def _format_dates(facts: ExtractedFacts) -> str:
    start_fact = facts.subscription_start_date
    end_fact = facts.subscription_end_date
    start = start_fact.value.strftime("%d.%m.%Y") if start_fact.status == "extracted" and start_fact.value else None
    end = end_fact.value.strftime("%d.%m.%Y") if end_fact.status == "extracted" and end_fact.value else None
    if start and end:
        return f"{start} - {end}"
    if start:
        return start
    if end:
        return end
    return "Bilinmiyor"


def _format_distribution(facts: ExtractedFacts) -> str:
    fact = facts.distribution_method
    if fact.status == "extracted" and fact.value:
        return str(fact.value)
    return "Bilinmiyor"


def format_analysis_notification(
    *,
    company_name: str,
    ticker: str | None,
    facts: ExtractedFacts,
    record: AnalysisRecord,
    disclosure_notification_urls: dict[str, str],
) -> str:
    """Build the Telegram message for one company's analysis.

    ``disclosure_notification_urls`` maps ``disclosure_id -> KAP
    notification URL`` for the company's matched disclosures — used to
    resolve the "Kaynaklar" section from ``record``'s
    ``source_references`` (when Gemini completed) or from every matched
    disclosure (otherwise, since there are no source_references to draw
    from).
    """
    decision = record.decision_result
    analysis = record.llm_analysis if record.llm_status == "completed" else None

    lines: list[str] = [f"📊 {company_name} ({ticker or 'bilinmiyor'})", ""]

    if decision is not None:
        signal_label = _SIGNAL_LABELS_TR.get(decision.signal, decision.signal)
        # A category below its mandatory coverage threshold can still
        # carry a thin partial average internally (see
        # halka_arz_advisor.decision.engine.compute_total_score) — never
        # shown here as a valid directional score, since that's exactly
        # what forces insufficient_data in the first place.
        score_part = f"skor: {decision.total_score:.0f}/100" if decision.total_score is not None else "skor: yetersiz veri"
        lines.append(f"Karar desteği: {signal_label}  ({score_part}, güven: %{decision.confidence_score:.0f})")
        lines.append("")
        lines.append("Kategori skorları:")
        for category in decision.category_scores:
            label = _CATEGORY_LABELS_TR.get(category.category, category.category)
            display_score = displayable_category_score(category)
            score_str = f"{display_score:.0f}/100" if display_score is not None else "yok"
            lines.append(f"  • {label}: {score_str} (kapsam %{category.coverage * 100:.0f})")
    else:
        lines.append("Karar desteği: Yetersiz veri")

    lines.append("")
    lines.append(f"Fiyat: {_format_price(facts)}")
    lines.append(f"Talep tarihleri: {_format_dates(facts)}")
    lines.append(f"Dağıtım: {_format_distribution(facts)}")

    lines.append("")
    lines.append("Gerekçe:")
    if analysis is not None:
        rationale = _sanitize(analysis.decision_explanation)
    elif decision is not None:
        # Gemini failed validation or had no document text — fall back
        # to the deterministic Turkish explanation of the same result.
        rationale = _sanitize(format_explanation(decision))
    else:
        rationale = _NO_ANALYSIS_RATIONALE_TR
    lines.append(rationale[:MAX_RATIONALE_CHARS])

    positive_items = list(analysis.positive_factors) if analysis is not None else []
    if not positive_items and decision is not None:
        positive_items = [
            f"{c.feature_id} ({c.category}): {c.normalized_score:.0f}/100"
            for c in top_positive_contributions(decision, MAX_POSITIVE_ITEMS)
        ]
    if positive_items:
        lines.append("")
        lines.append("Olumlu:")
        for item in positive_items[:MAX_POSITIVE_ITEMS]:
            lines.append(f"• {_sanitize(item)}")

    risk_items = list(analysis.key_risks) if analysis is not None else []
    if decision is not None:
        risk_items += [rule.reason for rule in decision.hard_rules if rule.triggered]
    if not risk_items and decision is not None:
        risk_items = [
            f"{c.feature_id} ({c.category}): {c.normalized_score:.0f}/100"
            for c in top_negative_contributions(decision, MAX_RISK_ITEMS)
        ]
    if risk_items:
        lines.append("")
        lines.append("Riskler / kısıtlayıcı kurallar:")
        for item in risk_items[:MAX_RISK_ITEMS]:
            lines.append(f"• {_sanitize(item)}")

    if analysis is not None:
        missing_and_conflicts = list(analysis.missing_information) + list(analysis.data_conflicts)
        if missing_and_conflicts:
            lines.append("")
            lines.append("Eksik/çelişkili bilgi:")
            for item in missing_and_conflicts[:MAX_MISSING_ITEMS]:
                lines.append(f"• {_sanitize(item)}")

    if analysis is not None:
        # dict.fromkeys: dedupe by disclosure_id while preserving first-seen order.
        disclosure_ids = list(dict.fromkeys(r.disclosure_id for r in analysis.source_references))
    else:
        disclosure_ids = list(disclosure_notification_urls.keys())

    urls = [disclosure_notification_urls[d] for d in disclosure_ids if d in disclosure_notification_urls]
    urls = urls[:MAX_SOURCE_URLS]
    if urls:
        lines.append("")
        lines.append("Kaynaklar:")
        for url in urls:
            lines.append(f"• {url}")

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return message
