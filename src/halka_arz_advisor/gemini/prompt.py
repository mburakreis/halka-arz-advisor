"""Builds the Turkish-language prompt sent to Gemini.

Embeds the deterministic :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`
as authoritative ground truth (explicitly instructed not to be altered),
the selected page-aware :class:`~halka_arz_advisor.gemini.context.ContextSection`
list as the *only* source of prose material, and — as of prompt version
2 — the already-computed :class:`~halka_arz_advisor.decision.engine.DecisionResult`
as a second block of authoritative, unchangeable ground truth. Gemini's
job is narrower than it used to be: it no longer decides a participation
signal or a confidence level (see :mod:`halka_arz_advisor.gemini.schema` —
those fields were removed from its output schema entirely, a hard
technical guarantee, not just a prompt instruction); it only explains,
in Turkish prose, the signal/scores/rules the deterministic engine
already produced.
"""

from __future__ import annotations

from ..decision.catalog import get_feature
from ..decision.engine import (
    DecisionResult,
    displayable_category_score,
    top_negative_contributions,
    top_positive_contributions,
)
from ..kap.extraction import FIELD_NAMES, ExtractedFacts
from .context import ContextSection

# Bumped whenever this module's prompt text changes in a way that could
# change Gemini's output shape/content for previously-cached inputs
# (see halka_arz_advisor.gemini.cache's module docstring) — the cache
# key folds this in, so a bump alone invalidates every stale cached
# analysis without any separate migration step.
PROMPT_VERSION = "3"

_SIGNAL_LABELS_TR: dict[str, str] = {
    "participate": "Katıl",
    "limited_participation": "Sınırlı katılım",
    "skip": "Pas geç",
    "insufficient_data": "Yetersiz veri",
}

_CATEGORY_LABELS_TR: dict[str, str] = {
    "fundamental_quality": "Temel nitelik",
    "valuation": "Değerleme",
    "offering_structure": "Arz yapısı",
}

_FACT_LABELS_TR: dict[str, str] = {
    "subscription_start_date": "Talep toplama başlangıç tarihi",
    "subscription_end_date": "Talep toplama bitiş tarihi",
    "offering_price": "Halka arz fiyatı",
    "currency": "Para birimi",
    "distribution_method": "Dağıtım yöntemi",
    "capital_increase_shares": "Sermaye artırımı yoluyla ihraç edilen pay tutarı",
    "secondary_sale_shares": "Ortak satışı yoluyla satılan pay tutarı",
    "total_offered_shares": "Halka arz edilen toplam pay tutarı",
    "capital_increase_ratio": "Sermaye artırım oranı (%)",
    "secondary_sale_ratio": "Ortak satış oranı (%)",
    "par_value_per_share": "Pay başına nominal değer",
    "pre_offer_capital": "Halka arz öncesi ödenmiş sermaye",
    "post_offer_capital": "Halka arz sonrası ödenmiş sermaye",
    "investor_group_allocations": "Yatırımcı grubu bazında tahsisat",
    "use_of_proceeds": "Fon kullanım yeri (izahnameden)",
    "key_risk_items": "Risk faktörleri (izahnameden)",
    "total_participant_count": "Toplam yatırımcı sayısı (halka arz sonuçlarından)",
    "retail_participant_count": "Bireysel yatırımcı sayısı (halka arz sonuçlarından)",
    "total_demand_multiple": "Genel talep/arz katı (halka arz sonuçlarından)",
    "retail_demand_multiple": "Bireysel yatırımcı talep katı (halka arz sonuçlarından)",
    "retail_allocated_shares": "Bireysel yatırımcılara dağıtılan pay tutarı (halka arz sonuçlarından)",
    "institutional_allocated_shares": "Kurumsal yatırımcılara dağıtılan pay tutarı (halka arz sonuçlarından)",
    "reported_post_money_market_cap": "Fiyat tespit raporunda belirtilen nihai piyasa değeri (mn $)",
    "reported_enterprise_value": "Fiyat tespit raporunda belirtilen firma değeri (mn $)",
    "reported_net_debt": "Fiyat tespit raporunda belirtilen net borç (mn $)",
    "reported_pe": "Fiyat tespit raporunda belirtilen F/K çarpanı",
    "reported_ev_ebitda": "Fiyat tespit raporunda belirtilen EV/EBITDA çarpanı",
    "reported_ps": "Fiyat tespit raporunda belirtilen F/S (PD/S) çarpanı",
    "reported_pb": "Fiyat tespit raporunda belirtilen PD/DD çarpanı",
    "headline_discount_percentage": "Fiyat tespit raporunda belirtilen halka arz iskonto oranı (%)",
}


def _format_facts(facts: ExtractedFacts) -> str:
    lines = []
    for field_name in FIELD_NAMES:
        fact = getattr(facts, field_name)
        label = _FACT_LABELS_TR[field_name]
        if fact.status == "extracted":
            source_note = (
                f" [kaynak: {fact.source.disclosure_id}, sayfa {fact.source.page_number}]" if fact.source else ""
            )
            lines.append(f"- {label}: {fact.value}{source_note}")
        elif fact.status == "conflicting":
            details = "; ".join(
                f"{obs.value} ({obs.source.document_type}, sayfa {obs.source.page_number})"
                for obs in fact.observations
            )
            lines.append(f"- {label}: ÇELİŞKİLİ VERİ, kaynaklar birbirini tutmuyor — {details}")
        else:
            lines.append(f"- {label}: bilinmiyor (tedarik edilen belgelerde bulunamadı)")
    return "\n".join(lines)


def _format_sections(sections: list[ContextSection]) -> str:
    if not sections:
        return "(İlgili metin bölümü bulunamadı.)"
    blocks = []
    for section in sections:
        blocks.append(
            f"[Belge kimliği: {section.disclosure_id} | Belge türü: {section.document_type} | "
            f"Sayfa: {section.page_number} | Konu: {section.category}]\n{section.text}"
        )
    return "\n\n".join(blocks)


def _feature_label(feature_id: str) -> str:
    try:
        return get_feature(feature_id).title
    except KeyError:
        return feature_id


def _format_decision_result(decision_result: DecisionResult) -> str:
    signal_label = _SIGNAL_LABELS_TR.get(decision_result.signal, decision_result.signal)
    # A category below its mandatory coverage threshold ("durum:
    # INSUFFICIENT") never shows a partial numeric score here, even if
    # one was internally computed — see
    # halka_arz_advisor.decision.engine.displayable_category_score. The
    # total score itself is already None in that case (see
    # halka_arz_advisor.decision.engine.compute_total_score).
    total_str = f"{decision_result.total_score:.1f}" if decision_result.total_score is not None else "yok"
    lines = [
        f"- Sinyal: {signal_label} ({decision_result.signal})",
        f"- Toplam skor: {total_str} / 100",
        f"- Güven skoru: {decision_result.confidence_score:.1f} / 100",
    ]
    for category in decision_result.category_scores:
        label = _CATEGORY_LABELS_TR.get(category.category, category.category)
        display_score = displayable_category_score(category)
        score_str = f"{display_score:.1f}" if display_score is not None else "yok"
        lines.append(f"- {label} skoru: {score_str} / 100 (kapsam: %{category.coverage * 100:.0f}, durum: {category.status})")
    triggered_rules = [rule for rule in decision_result.hard_rules if rule.triggered]
    if triggered_rules:
        lines.append("- Tetiklenen kesin kurallar:")
        for rule in triggered_rules:
            lines.append(f"  - {rule.rule_id}: {rule.reason}")
    if decision_result.warnings:
        lines.append("- Uyarılar:")
        for warning in decision_result.warnings:
            lines.append(f"  - {warning}")

    positive = top_positive_contributions(decision_result)
    negative = top_negative_contributions(decision_result)
    lines.append("")
    lines.append(
        "AŞAĞIDAKİ İKİ LİSTE, motorun kendisinin önceden hesapladığı TEK olumlu/olumsuz katkı kaynağıdır — "
        "'positive_factors' ve 'negative_factors' alanların başka HİÇBİR kaynağı olamaz:"
    )
    if positive:
        lines.append("- Önceden hesaplanmış olumlu katkılar:")
        for contribution in positive:
            lines.append(f"  - {_feature_label(contribution.feature_id)} ({contribution.normalized_score:.0f}/100)")
    else:
        lines.append("- Önceden hesaplanmış olumlu katkı yok.")
    if negative:
        lines.append("- Önceden hesaplanmış olumsuz katkılar:")
        for contribution in negative:
            lines.append(f"  - {_feature_label(contribution.feature_id)} ({contribution.normalized_score:.0f}/100)")
    else:
        lines.append("- Önceden hesaplanmış olumsuz katkı yok (tetiklenen kesin kurallar hariç).")
    return "\n".join(lines)


def allowed_source_references(sections: list[ContextSection]) -> set[tuple[str, int]]:
    """The exact ``(disclosure_id, page_number)`` pairs the model is
    allowed to cite — every other section-derived reference is a hallucination."""
    return {(section.disclosure_id, section.page_number) for section in sections}


def build_prompt(
    *, company_name: str, ticker: str | None, facts: ExtractedFacts, sections: list[ContextSection], decision_result: DecisionResult
) -> str:
    facts_block = _format_facts(facts)
    sections_block = _format_sections(sections)
    decision_block = _format_decision_result(decision_result)

    return f"""Sen, Türkiye'deki halka arzlar (IPO) hakkında KAP (Kamuyu Aydınlatma Platformu) \
belgelerinden elde edilen bilgileri özetleyen bir karar destek analiz asistanısın.

ŞİRKET: {company_name} ({ticker or "bilinmiyor"})

AŞAĞIDAKİ VERİLER DETERMİNİSTİK OLARAK (regex ile) BELGELERDEN ÇIKARILMIŞTIR. \
BU VERİLERİ ASLA DEĞİŞTİRME, YENİDEN HESAPLAMA VEYA YUVARLAMA YAPMA — olduğu gibi kullan:
{facts_block}

AŞAĞIDA, BAĞIMSIZ BİR DETERMİNİSTİK KARAR MOTORU TARAFINDAN ZATEN HESAPLANMIŞ NİHAİ SONUÇ \
YER ALMAKTADIR. BU SONUCU DEĞİŞTİRMEK, YENİDEN HESAPLAMAK VEYA FARKLI BİR SİNYAL/SKOR/GÜVEN \
DEĞERİ ÖNERMEK SENİN GÖREVİN DEĞİLDİR — senin tek görevin bu sonucu Türkçe olarak açıklamaktır:
{decision_block}

AŞAĞIDA, İLGİLİ BELGELERDEN SEÇİLMİŞ METİN PARÇALARI YER ALMAKTADIR — YALNIZCA "company_summary", \
"offering_summary", "use_of_proceeds_summary", "key_risks", "missing_information" ve \
"data_conflicts" alanları için kullanılabilir. "positive_factors", "negative_factors" ve \
"decision_explanation" alanları bu metin parçalarına DEĞİL, YALNIZCA yukarıdaki karar motoru \
sonucuna (sinyal, skorlar, tetiklenen kurallar, uyarılar, önceden hesaplanmış katkılar) dayanmalıdır:
{sections_block}

KURALLAR (kesinlikle uyulmalıdır):
1. Yanıtının tamamını Türkçe ver.
2. Yalnızca yukarıda sağlanan kesin verileri, karar motoru sonucunu ve belge metin parçalarını \
kullan. Başka hiçbir dış bilgi, varsayım veya genel bilgi kullanma.
3. Belgelenmiş gerçekler ile senin kendi yorumun/değerlendirmen arasında açık bir ayrım yap \
(örn. "İzahnameye göre..." / "Bu, ... anlamına gelebilir").
4. "source_references" alanında YALNIZCA yukarıda verilen belge kimliklerine (disclosure_id) \
ve o belgede gerçekten gösterilen sayfa numaralarına atıfta bulun. Sağlanmamış bir belge veya \
sayfa numarasına ASLA atıfta bulunma — bu geçersiz sayılır ve reddedilir.
5. Tarihleri, fiyatları, oranları, pay sayılarını veya dağıtım yöntemini ASLA değiştirme, \
yuvarlama veya uydurma. Yukarıda "bilinmiyor" olarak işaretlenmiş bir veriyi ASLA tahmin etme; \
bunun yerine "missing_information" alanına ekle.
6. "decision_explanation" alanında YUKARIDA VERİLEN sinyal, skorlar ve güven değerini AÇIKLA \
— bunları asla değiştirme, yeniden hesaplama veya kendi görüşünle çelişecek şekilde yeniden \
yorumlama. Hangi verilerin bu sonuca katkıda bulunduğunu ve hangi tetiklenen kuralların/\
uyarıların önemli olduğunu özetle.
7. "positive_factors" alanı YALNIZCA yukarıda listelenen "önceden hesaplanmış olumlu katkılar" \
maddelerinin Türkçe yeniden ifadesinden oluşabilir — her maddede ilgili katkının başlığını \
(örn. "Sermaye artırım oranı") değiştirmeden kullan. "negative_factors" alanı YALNIZCA önceden \
hesaplanmış olumsuz katkılardan ve tetiklenen kesin kurallardan oluşabilir. Belge metin \
parçalarından, kendi genel bilginden veya talep/demand yorumundan YENİ bir olumlu ya da olumsuz \
faktör TÜRETME veya EKLEME — önceden hesaplanmış listede yoksa, o alana yazma.
8. "decision_explanation" da dahil olmak üzere hiçbir alanda bir YATIRIM ÖNERİSİ veya TAVSİYESİ \
verme ("almalısınız", "katılmanızı öneririm" gibi ifadeler YASAKTIR) — sadece yukarıdaki karar \
motoru sonucunu ve nedenlerini AÇIKLA.
9. Yalnızca istenen şemaya uygun geçerli JSON döndür; şema dışında hiçbir alan ekleme, \
açıklama metni ekleme.

Şimdi yukarıdaki bilgilere dayanarak yapılandırılmış analiz JSON'unu üret."""
