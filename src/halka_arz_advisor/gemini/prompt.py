"""Builds the Turkish-language prompt sent to Gemini.

Embeds the deterministic :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`
as authoritative ground truth (explicitly instructed not to be altered),
and the selected page-aware :class:`~halka_arz_advisor.gemini.context.ContextSection`
list as the *only* source of prose material — plus explicit instructions
matching every constraint in the brief (Turkish only, cite only supplied
disclosure IDs/pages, never invent facts, ``insufficient_data`` when
evidence is thin).
"""

from __future__ import annotations

from ..kap.extraction import FIELD_NAMES, ExtractedFacts
from .context import ContextSection
from .schema import PARTICIPATION_SIGNAL_VALUES

PROMPT_VERSION = "1"

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
    "use_of_proceeds": "Fon kullanım yeri (izahnameden)",
    "key_risk_items": "Risk faktörleri (izahnameden)",
    "total_participant_count": "Toplam yatırımcı sayısı (halka arz sonuçlarından)",
    "retail_participant_count": "Bireysel yatırımcı sayısı (halka arz sonuçlarından)",
    "total_demand_multiple": "Genel talep/arz katı (halka arz sonuçlarından)",
    "retail_demand_multiple": "Bireysel yatırımcı talep katı (halka arz sonuçlarından)",
    "retail_allocated_shares": "Bireysel yatırımcılara dağıtılan pay tutarı (halka arz sonuçlarından)",
    "institutional_allocated_shares": "Kurumsal yatırımcılara dağıtılan pay tutarı (halka arz sonuçlarından)",
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


def allowed_source_references(sections: list[ContextSection]) -> set[tuple[str, int]]:
    """The exact ``(disclosure_id, page_number)`` pairs the model is
    allowed to cite — every other section-derived reference is a hallucination."""
    return {(section.disclosure_id, section.page_number) for section in sections}


def build_prompt(*, company_name: str, ticker: str | None, facts: ExtractedFacts, sections: list[ContextSection]) -> str:
    facts_block = _format_facts(facts)
    sections_block = _format_sections(sections)
    signal_values = ", ".join(PARTICIPATION_SIGNAL_VALUES)

    return f"""Sen, Türkiye'deki halka arzlar (IPO) hakkında KAP (Kamuyu Aydınlatma Platformu) \
belgelerinden elde edilen bilgileri özetleyen bir karar destek analiz asistanısın.

ŞİRKET: {company_name} ({ticker or "bilinmiyor"})

AŞAĞIDAKİ VERİLER DETERMİNİSTİK OLARAK (regex ile) BELGELERDEN ÇIKARILMIŞTIR. \
BU VERİLERİ ASLA DEĞİŞTİRME, YENİDEN HESAPLAMA VEYA YUVARLAMA YAPMA — olduğu gibi kullan:
{facts_block}

AŞAĞIDA, İLGİLİ BELGELERDEN SEÇİLMİŞ METİN PARÇALARI YER ALMAKTADIR. \
Analizini YALNIZCA bu metin parçalarına ve yukarıdaki kesin verilere dayandır:
{sections_block}

KURALLAR (kesinlikle uyulmalıdır):
1. Yanıtının tamamını Türkçe ver.
2. Yalnızca yukarıda sağlanan kesin verileri ve belge metin parçalarını kullan. \
Başka hiçbir dış bilgi, varsayım veya genel bilgi kullanma.
3. Belgelenmiş gerçekler ile senin kendi yorumun/değerlendirmen arasında açık bir ayrım yap \
(örn. "İzahnameye göre..." / "Bu, ... anlamına gelebilir").
4. "source_references" alanında YALNIZCA yukarıda verilen belge kimliklerine (disclosure_id) \
ve o belgede gerçekten gösterilen sayfa numaralarına atıfta bulun. Sağlanmamış bir belge veya \
sayfa numarasına ASLA atıfta bulunma — bu geçersiz sayılır ve reddedilir.
5. Tarihleri, fiyatları, oranları, pay sayılarını veya dağıtım yöntemini ASLA değiştirme, \
yuvarlama veya uydurma. Yukarıda "bilinmiyor" olarak işaretlenmiş bir veriyi ASLA tahmin etme; \
bunun yerine "missing_information" alanına ekle.
6. Kanıtlar katılım kararı vermek için yetersizse "participation_signal" alanını \
"insufficient_data" olarak işaretle.
7. "participation_signal" alanı şu değerlerden yalnızca biri olmalıdır: {signal_values}.
8. "confidence" alanı 0 ile 1 arasında bir sayı olmalıdır (kanıtların gücünü yansıtır).
9. Yalnızca istenen şemaya uygun geçerli JSON döndür; şema dışında hiçbir alan ekleme, \
açıklama metni ekleme.

Şimdi yukarıdaki bilgilere dayanarak yapılandırılmış analiz JSON'unu üret."""
