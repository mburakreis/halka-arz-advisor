"""Plain, human-readable Telegram message for one company's
:class:`~halka_arz_advisor.decision.subscription_v1.SubscriptionDecisionV1`
— mirrors :mod:`halka_arz_advisor.notify.analysis_formatting`'s role for
``expert_v0``'s :class:`~halka_arz_advisor.decision.engine.DecisionResult`,
but for the separate, non-scored subscription decision.

Pure formatting: every value shown here comes from the caller's already-
built :class:`~halka_arz_advisor.kap.offering_terms.OfferingTerms`,
:class:`~halka_arz_advisor.kap.manual_confirmation.CompletedOfferingTerms`,
:class:`~halka_arz_advisor.decision.subscription_v1.SubscriptionDecisionV1`,
and :class:`~halka_arz_advisor.evds.models.MarketContextSnapshot` — this
module does no extraction, no decision logic, and no I/O.

When ``decision.action == "CANNOT_ASSESS_SUBSCRIPTION"``, the card shows
exactly which fields still need a manual confirmation instead of any
recommendation-shaped text — never a signal that looks like advice when
the system genuinely doesn't know enough yet.
"""

from __future__ import annotations

from ..decision.subscription_v1 import SubscriptionDecisionV1
from ..evds.models import MarketContextSnapshot
from ..kap.allocation_scenario import AllocationScenario
from ..kap.manual_confirmation import CompletedOfferingTerms, effective_offering_terms
from ..kap.offering_terms import OfferingTerms
from ..kap.valuation import ValuationEvidence, ValuationFeature

MAX_SOURCE_URLS = 3
MAX_REASON_ITEMS = 3
MAX_RISK_ITEMS = 3
# Telegram's sendMessage text limit is 4096 UTF-16 code units; stay
# comfortably under it rather than relying on an exact boundary (same
# convention as notify.analysis_formatting).
MAX_MESSAGE_CHARS = 3900

_ACTION_LABELS_TR: dict[str, str] = {
    "SUBSCRIBE_FOR_LISTING_TRADE": "Katıl (kısa vadeli işlem)",
    "SUBSCRIBE_WITH_HOLD_OPTION": "Katıl (tutma seçeneğiyle)",
    "WATCH_SUBSCRIPTION": "İzle — henüz net bir avantaj yok",
    "PASS_SUBSCRIPTION": "Katılma",
    "PASS_AND_REASSESS_AFTER_LISTING": "Katılma — işlem görmeye başladıktan sonra yeniden değerlendir",
    "CANNOT_ASSESS_SUBSCRIPTION": "Değerlendirilemiyor — eksik bilgi",
}
_EDGE_LABELS_TR: dict[str, str] = {
    "FAVORABLE": "Olumlu (yakın dönem halka arzları)",
    "NEUTRAL": "Nötr (yakın dönem halka arzları)",
    "UNFAVORABLE": "Olumsuz (yakın dönem halka arzları)",
    "UNKNOWN": "Bilinmiyor — yeterli karşılaştırma yok",
}
_MECHANICS_LABELS_TR: dict[str, str] = {
    "SUPPORTIVE": "Destekleyici",
    "NEUTRAL": "Nötr",
    "CONSTRAINED": "Kısıtlı",
    "UNKNOWN": "Bilinmiyor",
}
_FINANCIAL_QUALITY_LABELS_TR: dict[str, str] = {
    "POSITIVE": "Olumlu",
    "MIXED": "Karışık",
    "NEGATIVE": "Olumsuz",
    "UNKNOWN": "Bilinmiyor",
}
_OWNERSHIP_LABELS_TR: dict[str, str] = {
    "HOLD_CANDIDATE": "Tutma adayı",
    "WATCH": "İzlemeye değer",
    "AVOID_LONG_TERM": "Uzun vadede kaçının",
    "NOT_ASSESSABLE": "Değerlendirilemiyor",
}
_EVIDENCE_GRADE_LABELS_TR: dict[str, str] = {
    "STRONG": "Güçlü",
    "MODERATE": "Orta",
    "WEAK": "Zayıf",
    "NONE": "Yok",
}
_HORIZON_LABELS_TR: dict[str, str] = {
    "5D_LISTING_TRADE": "İlk 5 işlem günü (kesin bir çıkış garantisi değildir)",
    "5D_LISTING_TRADE_OR_HOLD": "İlk 5 işlem günü ya da tutma",
    "watch_pending_edge": "Şartlar tamam, avantaj netleşene kadar izle",
    "watch_post_listing": "İşlem görmeye başladıktan sonra izle",
    "not_applicable": "Uygulanamaz",
}
_FIELD_LABELS_TR: dict[str, str] = {
    "offer_price": "Halka arz fiyatı",
    "subscription_start": "Talep toplama başlangıcı",
    "subscription_end": "Talep toplama bitişi",
    "retail_offered_shares": "Bireysel yatırımcı pay adedi",
    "retail_allocation_percentage": "Bireysel yatırımcı tahsisat oranı",
    "retail_offered_shares_or_retail_allocation_percentage": "Bireysel yatırımcı pay adedi veya tahsisat oranı",
    "retail_distribution_rule": "Bireysel dağıtım yöntemi (eşit/oransal)",
    "distribution_method": "Dağıtım yöntemi",
    "total_offered_shares": "Toplam halka arz edilen pay adedi",
}


def _sanitize(text: str) -> str:
    printable = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return " ".join(printable.split())


def _field_label(field_name: str) -> str:
    base_name = field_name.replace(" (conflicting)", "")
    label = _FIELD_LABELS_TR.get(base_name, base_name)
    return f"{label} (çelişkili)" if base_name != field_name else label


def _format_price(terms: OfferingTerms) -> str:
    field = terms.offer_price
    if field.status != "extracted" or field.value is None:
        return "Bilinmiyor"
    return f"{field.value:.2f} TL"


def _format_dates(terms: OfferingTerms) -> str:
    start = terms.subscription_start
    end = terms.subscription_end
    start_str = start.value.strftime("%d.%m.%Y") if start.status == "extracted" and start.value else None
    end_str = end.value.strftime("%d.%m.%Y") if end.status == "extracted" and end.value else None
    if start_str and end_str:
        return f"{start_str} - {end_str}"
    return start_str or end_str or "Bilinmiyor"


def _format_distribution(terms: OfferingTerms) -> str:
    field = terms.distribution_method
    return str(field.value) if field.status == "extracted" and field.value else "Bilinmiyor"


def _format_retail_tranche(terms: OfferingTerms) -> str:
    pct = terms.retail_allocation_percentage
    shares = terms.retail_offered_shares
    rule = terms.retail_distribution_rule
    parts: list[str] = []
    if pct.status == "extracted" and pct.value is not None:
        parts.append(f"%{pct.value:.1f}")
    if shares.status == "extracted" and shares.value is not None:
        parts.append(f"{shares.value:,.0f} pay".replace(",", "."))
    rule_str = {"equal": "eşit dağıtım", "proportional": "oransal dağıtım"}.get(rule.value) if rule.status == "extracted" else None
    if rule_str:
        parts.append(rule_str)
    return ", ".join(parts) if parts else "Bilinmiyor"


def _format_allocation_scenario(scenario: AllocationScenario) -> str:
    count_str = f"{scenario.hypothetical_retail_participant_count:,}".replace(",", ".")
    if scenario.status != "computed":
        return f"  • {count_str} katılımcı varsayımı: hesaplanamıyor"
    base = scenario.base_integer_allocation
    range_shares = scenario.allocation_range_shares
    range_str = f"{range_shares[0]}-{range_shares[1]} pay" if range_shares and range_shares[0] != range_shares[1] else f"{base} pay"
    tl_str = ""
    if scenario.tl_allocation_range is not None:
        low, high = scenario.tl_allocation_range
        tl_str = f" (~{low:,.0f}-{high:,.0f} TL)".replace(",", ".") if low != high else f" (~{low:,.0f} TL)".replace(",", ".")
    return f"  • {count_str} katılımcı varsayımı: {range_str}{tl_str}"


def _format_multiple(feature: ValuationFeature, label: str) -> str:
    if feature.status == "computed":
        period = f" ({feature.period_end.isoformat()})" if feature.period_end else ""
        return f"{label}: {feature.value:.1f}x{period}"
    if feature.status == "not_applicable":
        return f"{label}: uygulanamaz"
    return f"{label}: bilinmiyor"


def _format_valuation(evidence: ValuationEvidence) -> list[str]:
    lines = ["", "Değerleme (halka arz fiyatına göre — bir ucuz/pahalı hükmü değildir):"]
    cap = evidence.implied_market_cap
    if cap.status == "computed":
        lines.append(f"  • Halka arz sonrası piyasa değeri: {cap.value:,.0f} TL".replace(",", "."))
    else:
        lines.append(f"  • Halka arz sonrası piyasa değeri: bilinmiyor ({cap.unavailable_reason})")
    lines.append(
        "  • "
        + "  |  ".join(
            (_format_multiple(evidence.pe_at_offer, "F/K"), _format_multiple(evidence.ps_at_offer, "F/S"), _format_multiple(evidence.pb_at_offer, "PD/DD"))
        )
    )
    if evidence.ev_ebitda_at_offer.status != "computed":
        lines.append(f"  • FD/FAVÖK: bilinmiyor ({evidence.ev_ebitda_at_offer.unavailable_reason})")
    else:
        lines.append(_format_multiple(evidence.ev_ebitda_at_offer, "  • FD/FAVÖK"))
    sufficiency_label = "Yeterli" if evidence.sufficiency == "SUFFICIENT" else "Yetersiz"
    lines.append(f"  • Fiyat sağlaması için kanıt: {sufficiency_label} — {evidence.sufficiency_reason}")
    return lines


def _format_recent_ipo_regime(decision: SubscriptionDecisionV1) -> list[str]:
    regime = decision.recent_ipo_regime
    lines = ["", "Yakın dönem halka arz rejimi (subscription_edge'in tek kaynağı):"]
    lines.append(f"  • Olgun karşılaştırma sayısı: {regime.mature_ipo_count} (son {regime.window_days} gün)")
    if regime.median_bist_relative_return_5d is not None:
        lines.append(f"  • Medyan 5g BIST-göreli getiri: %{regime.median_bist_relative_return_5d:.1f}")
    if regime.positive_bist_relative_share_5d is not None:
        lines.append(f"  • Pozitif 5g BIST-göreli getiri oranı: %{regime.positive_bist_relative_share_5d * 100:.0f}")
    return lines


def _format_market_context(market: MarketContextSnapshot | None) -> list[str]:
    if market is None:
        return []
    lines = ["", "Piyasa rejimi (bağlam amaçlı, karara dahil edilmez):"]
    labels = {
        "bist_index_level": "BIST 100 seviyesi",
        "bist100_return_20d": "BIST 100 20g getiri",
        "bist100_return_60d": "BIST 100 60g getiri",
        "policy_rate": "Politika faizi",
        "cpi_yoy": "TÜFE yıllık",
    }
    shown = False
    for key, label in labels.items():
        feature = market.get(key)
        if feature is not None:
            unit = "%" if "return" in key or key in ("policy_rate", "cpi_yoy") else ""
            lines.append(f"  • {label}: {unit}{feature.value:.1f}")
            shown = True
    return lines if shown else []


def format_subscription_card(
    *,
    company_name: str,
    ticker: str | None,
    offering_terms: OfferingTerms,
    completed_terms: CompletedOfferingTerms,
    decision: SubscriptionDecisionV1,
    market_context: MarketContextSnapshot | None = None,
    disclosure_notification_urls: dict[str, str] | None = None,
) -> str:
    disclosure_notification_urls = disclosure_notification_urls or {}
    lines: list[str] = [f"🎯 {company_name} ({ticker or 'bilinmiyor'})", ""]

    lines.append(f"Karar: {_ACTION_LABELS_TR.get(decision.action, decision.action)}")

    if decision.action == "CANNOT_ASSESS_SUBSCRIPTION":
        lines.append("")
        lines.append("Bu karar için önce şu alanların elle onaylanması gerekiyor:")
        for field_name in decision.missing_critical_evidence:
            lines.append(f"  • {_field_label(field_name)}")
        message = "\n".join(lines)
        return message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…" if len(message) > MAX_MESSAGE_CHARS else message

    lines.append(f"Beklenen ufuk: {_HORIZON_LABELS_TR.get(decision.intended_horizon, decision.intended_horizon)}")
    lines.append(
        f"Talep avantajı: {_EDGE_LABELS_TR.get(decision.subscription_edge, decision.subscription_edge)}  |  "
        f"Arz mekaniği: {_MECHANICS_LABELS_TR.get(decision.mechanics_state, decision.mechanics_state)}"
    )
    lines.append(
        f"Finansal kalite: {_FINANCIAL_QUALITY_LABELS_TR.get(decision.financial_quality, decision.financial_quality)}  |  "
        f"Sahiplik görünümü: {_OWNERSHIP_LABELS_TR.get(decision.ownership_view, decision.ownership_view)}"
    )
    lines.append(
        f"Kanıt derecesi — talep: {_EVIDENCE_GRADE_LABELS_TR.get(decision.subscription_evidence_grade, decision.subscription_evidence_grade)}  |  "
        f"sahiplik: {_EVIDENCE_GRADE_LABELS_TR.get(decision.ownership_evidence_grade, decision.ownership_evidence_grade)}"
    )

    # Display the *effective* view (automatic where resolved, else a
    # manual confirmation if one is in effect) — never the raw
    # automatic-only terms, which would show a field as "Bilinmiyor"
    # even when a manual confirmation (visibly marked below) already
    # made it known and is what the decision above was actually based
    # on. A "conflicting" field still displays as unresolved here too,
    # exactly like it does everywhere else in this project.
    display_terms = effective_offering_terms(offering_terms, completed_terms)
    lines.append("")
    lines.append(f"Fiyat: {_format_price(display_terms)}")
    lines.append(f"Talep tarihleri: {_format_dates(display_terms)}")
    lines.append(f"Dağıtım: {_format_distribution(display_terms)}")
    lines.append(f"Bireysel tahsisat: {_format_retail_tranche(display_terms)}")

    if decision.allocation_scenarios:
        lines.append("")
        lines.append("Tahsisat senaryoları (varsayımsal katılımcı sayıları — bir talep tahmini değildir):")
        for scenario in decision.allocation_scenarios:
            lines.append(_format_allocation_scenario(scenario))

    lines.extend(_format_valuation(decision.valuation_evidence))
    lines.extend(_format_recent_ipo_regime(decision))

    if decision.reasons:
        lines.append("")
        lines.append("Gerekçe:")
        for reason in decision.reasons[:MAX_REASON_ITEMS]:
            lines.append(f"• {_sanitize(reason)}")

    if decision.strongest_positive_evidence:
        lines.append("")
        lines.append("Olumlu kanıt:")
        for item in decision.strongest_positive_evidence[:MAX_REASON_ITEMS]:
            lines.append(f"• {_sanitize(item)}")

    if decision.strongest_risks:
        lines.append("")
        lines.append("Riskler:")
        for item in decision.strongest_risks[:MAX_RISK_ITEMS]:
            lines.append(f"• {_sanitize(item)}")

    manual_fields = [
        (name, field) for name, field in completed_terms.as_dict().items() if field.source == "user_confirmed"
    ]
    if manual_fields:
        lines.append("")
        lines.append("Elle onaylanan alanlar (resmi kaynak yerine kullanıcı girdisi):")
        for name, field in manual_fields:
            lines.append(f"  • {_field_label(name)}: {field.effective_value} ({field.manual.confirmed_by})")

    lines.extend(_format_market_context(market_context))

    disclosure_ids = list(
        dict.fromkeys(
            obs.source_disclosure_id
            for name in ("offer_price", "subscription_start", "subscription_end", "distribution_method")
            for obs in getattr(offering_terms, name).observations
            if obs.source_disclosure_id
        )
    )
    urls = [disclosure_notification_urls[d] for d in disclosure_ids if d in disclosure_notification_urls][:MAX_SOURCE_URLS]
    if urls:
        lines.append("")
        lines.append("Kaynaklar:")
        for url in urls:
            lines.append(f"• {url}")

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return message


__all__ = ["format_subscription_card"]
