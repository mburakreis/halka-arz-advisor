from datetime import UTC, date, datetime

from halka_arz_advisor.decision.engine import CategoryScoreResult, DecisionResult, HardRuleResult
from halka_arz_advisor.gemini.models import AnalysisRecord
from halka_arz_advisor.gemini.schema import AnalysisOutput, SourceReference
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.notify.analysis_formatting import (
    MAX_MESSAGE_CHARS,
    MAX_MISSING_ITEMS,
    MAX_POSITIVE_ITEMS,
    MAX_RISK_ITEMS,
    MAX_SOURCE_URLS,
    format_analysis_notification,
)

SRC = SourceRef("investor_sale_announcement", "d1", "url", 1)


def _facts(**overrides):
    prospectus = {}
    announcement = {}
    if "offering_price" in overrides:
        announcement["offering_price"] = FieldObservation(overrides["offering_price"], "76,60 TL", SRC)
        announcement["currency"] = FieldObservation("TRY", "76,60 TL", SRC)
    if "subscription_start_date" in overrides:
        announcement["subscription_start_date"] = FieldObservation(overrides["subscription_start_date"], "01.08.2026", SRC)
    if "subscription_end_date" in overrides:
        announcement["subscription_end_date"] = FieldObservation(overrides["subscription_end_date"], "03.08.2026", SRC)
    if "distribution_method" in overrides:
        prospectus["distribution_method"] = FieldObservation(overrides["distribution_method"], "x", SRC)
    return build_extracted_facts(prospectus, announcement)


def _decision_result(**overrides) -> DecisionResult:
    defaults = dict(
        signal="participate",
        total_score=72.0,
        confidence_score=82.0,
        category_scores=(
            CategoryScoreResult("fundamental_quality", 70.0, 0.8, "OK", ()),
            CategoryScoreResult("valuation", 65.0, 0.75, "OK", ()),
            CategoryScoreResult("offering_structure", 80.0, 1.0, "OK", ()),
        ),
        feature_contributions=(),
        confidence_components=(),
        hard_rules=(
            HardRuleResult("missing_mandatory_documents", "insufficient_data", False, "every mandatory pre-offer feature has a readable source document"),
        ),
        warnings=(),
        evidence_references=(),
        rule_version="expert_v0",
        weight_set_version="expert_v0",
    )
    defaults.update(overrides)
    return DecisionResult(**defaults)


def _analysis(**overrides) -> AnalysisOutput:
    defaults = dict(
        company_summary="Şirket özeti.",
        offering_summary="Halka arz özeti.",
        use_of_proceeds_summary="Fon kullanım özeti.",
        key_risks=("Risk 1", "Risk 2"),
        positive_factors=("Olumlu 1", "Olumlu 2"),
        negative_factors=("Olumsuz 1",),
        missing_information=("Eksik 1",),
        data_conflicts=("Çelişki 1",),
        decision_explanation="Kısa gerekçe metni.",
        source_references=(SourceReference("d1", 1), SourceReference("d2", 2)),
    )
    defaults.update(overrides)
    return AnalysisOutput(**defaults)


def _completed_record(**overrides) -> AnalysisRecord:
    analysis = overrides.pop("llm_analysis", _analysis())
    defaults = dict(
        spk_record_id="ipo:QUICK:2026 / 7",
        llm_status="completed",
        llm_model="gemini-3.5-flash",
        llm_analysis=analysis,
        llm_warnings=(),
        analyzed_at=datetime(2026, 8, 6, tzinfo=UTC),
        decision_result=_decision_result(),
    )
    defaults.update(overrides)
    return AnalysisRecord(**defaults)


def _insufficient_data_record(**overrides) -> AnalysisRecord:
    defaults = dict(
        spk_record_id="ipo:QUICK:2026 / 7",
        llm_status="insufficient_data",
        llm_model="gemini-3.5-flash",
        llm_analysis=None,
        llm_warnings=("no extractable PDF text available in the cache for this company's documents",),
        analyzed_at=datetime(2026, 8, 6, tzinfo=UTC),
        decision_result=_decision_result(),
    )
    defaults.update(overrides)
    return AnalysisRecord(**defaults)


# --------------------------------------------------------------------------
# completed: full render
# --------------------------------------------------------------------------


def test_completed_message_contains_all_sections():
    facts = _facts(
        offering_price=76.6,
        subscription_start_date=date(2026, 8, 1),
        subscription_end_date=date(2026, 8, 3),
        distribution_method="Oransal Dağıtım",
    )
    record = _completed_record()
    urls = {"d1": "https://www.kap.org.tr/tr/Bildirim/1", "d2": "https://www.kap.org.tr/tr/Bildirim/2"}

    message = format_analysis_notification(
        company_name="QUİCK SİGORTA A.Ş.", ticker="QUICK", facts=facts, record=record, disclosure_notification_urls=urls
    )

    assert message.startswith("📊 QUİCK SİGORTA A.Ş. (QUICK)")
    assert "Karar desteği: Katıl" in message
    assert "skor: 72/100" in message
    assert "güven: %82" in message
    assert "Kategori skorları:" in message
    assert "Temel nitelik: 70/100" in message
    assert "Değerleme: 65/100" in message
    assert "Arz yapısı: 80/100" in message
    assert "Fiyat: 76.60 TL" in message
    assert "Talep tarihleri: 01.08.2026 - 03.08.2026" in message
    assert "Dağıtım: Oransal Dağıtım" in message
    assert "Gerekçe:\nKısa gerekçe metni." in message
    assert "Olumlu:\n• Olumlu 1\n• Olumlu 2" in message
    assert "Risk 1" in message and "Risk 2" in message
    assert "Eksik/çelişkili bilgi:\n• Eksik 1\n• Çelişki 1" in message
    assert "Kaynaklar:\n• https://www.kap.org.tr/tr/Bildirim/1\n• https://www.kap.org.tr/tr/Bildirim/2" in message
    # never the raw JSON / full schema dump
    assert "source_references" not in message


def test_insufficient_total_score_shows_yetersiz_veri_not_a_number():
    """A category can carry a real partial score internally even while
    flagged INSUFFICIENT (see halka_arz_advisor.decision.engine.score_category);
    neither that per-category number nor a total_score built from it may
    reach the user as if it were a valid score — see
    halka_arz_advisor.decision.engine.compute_total_score /
    displayable_category_score, which this message must respect."""
    decision = _decision_result(
        signal="insufficient_data",
        total_score=None,
        category_scores=(
            CategoryScoreResult("fundamental_quality", 70.0, 0.8, "OK", ()),
            # A perfect partial average, but below the coverage
            # threshold — must render as "yok", never "100/100".
            CategoryScoreResult("valuation", 100.0, 0.40, "INSUFFICIENT", ()),
            CategoryScoreResult("offering_structure", 80.0, 1.0, "OK", ()),
        ),
    )
    record = _completed_record(decision_result=decision)

    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
    )

    assert "skor: yetersiz veri" in message
    assert "100/100" not in message
    assert "Değerleme: yok (kapsam %40)" in message
    assert "Temel nitelik: 70/100" in message


def test_signal_labels_are_turkish():
    for signal, label in [
        ("participate", "Katıl"),
        ("limited_participation", "Sınırlı katıl"),
        ("skip", "Pas geç"),
        ("insufficient_data", "Yetersiz veri"),
    ]:
        record = _completed_record(decision_result=_decision_result(signal=signal))
        message = format_analysis_notification(
            company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
        )
        assert f"Karar desteği: {label}" in message


# --------------------------------------------------------------------------
# insufficient_data (system-level: no llm_analysis at all, but a real
# decision_result still exists — the deterministic explanation is used).
# --------------------------------------------------------------------------


def test_insufficient_data_record_renders_deterministic_fallback():
    record = _insufficient_data_record()
    urls = {"d1": "https://www.kap.org.tr/tr/Bildirim/1"}

    message = format_analysis_notification(
        company_name="QUİCK SİGORTA A.Ş.", ticker="QUICK", facts=_facts(), record=record, disclosure_notification_urls=urls
    )

    assert "Karar desteği: Katıl" in message  # from decision_result, not Gemini
    assert "Sinyal: KATIL" in message  # from decision.explain.format_explanation's fallback rationale
    assert "Kaynaklar:\n• https://www.kap.org.tr/tr/Bildirim/1" in message


def test_no_decision_result_at_all_renders_system_fallback():
    # A genuinely undeliverable case shouldn't occur via
    # notify.analysis_delivery (it skips companies with no decision), but
    # the formatter itself must still degrade gracefully.
    record = _insufficient_data_record(decision_result=None)
    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
    )
    assert "Karar desteği: Yetersiz veri" in message
    assert "Bu şirket için önbellekte yeterli belge metni bulunamadığından analiz yapılamadı." in message


# --------------------------------------------------------------------------
# missing optional fields
# --------------------------------------------------------------------------


def test_missing_deterministic_facts_render_as_bilinmiyor():
    record = _completed_record()
    message = format_analysis_notification(
        company_name="X", ticker=None, facts=_facts(), record=record, disclosure_notification_urls={}
    )
    assert "Fiyat: Bilinmiyor" in message
    assert "Talep tarihleri: Bilinmiyor" in message
    assert "Dağıtım: Bilinmiyor" in message
    assert "X (bilinmiyor)" in message


def test_empty_optional_lists_omit_their_sections():
    record = _completed_record(
        llm_analysis=_analysis(positive_factors=(), key_risks=(), missing_information=(), data_conflicts=())
    )
    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
    )
    assert "Eksik/çelişkili bilgi:" not in message
    assert "Kaynaklar:" not in message  # no matching URLs supplied either


# --------------------------------------------------------------------------
# caps: max items per section, max source URLs
# --------------------------------------------------------------------------


def test_lists_are_capped_at_their_maximums():
    record = _completed_record(
        llm_analysis=_analysis(
            positive_factors=tuple(f"Olumlu {i}" for i in range(10)),
            key_risks=tuple(f"Risk {i}" for i in range(10)),
            missing_information=tuple(f"Eksik {i}" for i in range(10)),
            data_conflicts=(),
            source_references=tuple(SourceReference(f"d{i}", 1) for i in range(10)),
        )
    )
    urls = {f"d{i}": f"https://www.kap.org.tr/tr/Bildirim/{i}" for i in range(10)}
    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls=urls
    )
    assert message.count("• Olumlu") == MAX_POSITIVE_ITEMS
    assert message.count("• Eksik") == MAX_MISSING_ITEMS
    assert message.count("kap.org.tr/tr/Bildirim/") == MAX_SOURCE_URLS
    # risk section mixes Gemini's key_risks with any triggered hard
    # rules, still capped at MAX_RISK_ITEMS total
    risk_block = message.split("Riskler / kısıtlayıcı kurallar:\n", 1)[1].split("\n\n", 1)[0]
    assert risk_block.count("•") == MAX_RISK_ITEMS


# --------------------------------------------------------------------------
# message length handling
# --------------------------------------------------------------------------


def test_long_rationale_is_truncated():
    long_rationale = "Çok uzun bir gerekçe. " * 200  # far beyond MAX_RATIONALE_CHARS
    record = _completed_record(llm_analysis=_analysis(decision_explanation=long_rationale))
    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
    )
    assert len(message) <= MAX_MESSAGE_CHARS
    # the (capped) rationale line itself shouldn't run away either
    rationale_line = message.split("Gerekçe:\n", 1)[1].split("\n\n", 1)[0]
    assert len(rationale_line) <= 500


def test_overall_message_never_exceeds_telegram_safe_limit():
    record = _completed_record(
        llm_analysis=_analysis(
            positive_factors=tuple("Olumlu " + "x" * 1000 for _ in range(3)),
            key_risks=tuple("Risk " + "y" * 1000 for _ in range(3)),
            missing_information=tuple("Eksik " + "z" * 1000 for _ in range(2)),
            decision_explanation="Gerekçe " * 500,
        )
    )
    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
    )
    assert len(message) <= MAX_MESSAGE_CHARS


def test_sanitizes_control_characters_and_collapses_whitespace():
    record = _completed_record(
        llm_analysis=_analysis(positive_factors=("Satır\x00 sonu\n\n\tve   fazla    boşluk",))
    )
    message = format_analysis_notification(
        company_name="X", ticker="X", facts=_facts(), record=record, disclosure_notification_urls={}
    )
    assert "\x00" not in message
    assert "Satır sonu ve fazla boşluk" in message
