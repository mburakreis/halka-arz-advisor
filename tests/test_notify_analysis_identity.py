from datetime import UTC, datetime

from halka_arz_advisor.decision.engine import DecisionResult
from halka_arz_advisor.gemini.models import AnalysisRecord
from halka_arz_advisor.gemini.schema import AnalysisOutput, SourceReference
from halka_arz_advisor.notify.analysis_identity import analysis_notification_hash


def _decision_result(**overrides) -> DecisionResult:
    defaults = dict(
        signal="participate",
        total_score=70.0,
        confidence_score=75.0,
        category_scores=(),
        feature_contributions=(),
        confidence_components=(),
        hard_rules=(),
        warnings=(),
        evidence_references=(),
        rule_version="expert_v0",
        weight_set_version="expert_v0",
    )
    defaults.update(overrides)
    return DecisionResult(**defaults)


def _completed_record(**overrides) -> AnalysisRecord:
    analysis = AnalysisOutput(
        company_summary="Özet.",
        offering_summary="Halka arz özeti.",
        use_of_proceeds_summary="Fon kullanım özeti.",
        key_risks=("Risk 1",),
        positive_factors=("Olumlu 1",),
        negative_factors=("Olumsuz 1",),
        missing_information=(),
        data_conflicts=(),
        decision_explanation="Karar motoru açıklaması.",
        source_references=(SourceReference("d1", 1),),
    )
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
        llm_warnings=("no extractable PDF text",),
        analyzed_at=datetime(2026, 8, 6, tzinfo=UTC),
        decision_result=_decision_result(),
    )
    defaults.update(overrides)
    return AnalysisRecord(**defaults)


def test_same_inputs_produce_same_hash():
    record = _completed_record()
    h1 = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record
    )
    h2 = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record
    )
    assert h1 == h2


def test_different_analysis_content_changes_hash():
    record_a = _completed_record()
    record_b = _completed_record(
        llm_analysis=AnalysisOutput(
            company_summary="Farklı özet.",
            offering_summary="Halka arz özeti.",
            use_of_proceeds_summary="Fon kullanım özeti.",
            key_risks=("Risk 1",),
            positive_factors=("Olumlu 1",),
            negative_factors=("Olumsuz 1",),
            missing_information=(),
            data_conflicts=(),
            decision_explanation="Karar motoru açıklaması.",
            source_references=(SourceReference("d1", 1),),
        )
    )
    h_a = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_a
    )
    h_b = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_b
    )
    assert h_a != h_b


def test_different_decision_signal_changes_hash():
    # Gemini's narrative is identical — only the deterministic decision
    # differs — must still count as "content changed" (see
    # halka_arz_advisor.decision.engine.decision_signature).
    record_a = _completed_record(decision_result=_decision_result(signal="participate"))
    record_b = _completed_record(decision_result=_decision_result(signal="skip"))
    h_a = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_a
    )
    h_b = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_b
    )
    assert h_a != h_b


def test_different_decision_confidence_alone_does_not_change_hash():
    # confidence_score alone drifts with document freshness/time — must
    # not be treated as a content change (see decision_signature's own
    # docstring for why), or the notification would look "changed" every
    # single day and get re-sent for no real reason.
    record_a = _completed_record(decision_result=_decision_result(confidence_score=75.0))
    record_b = _completed_record(decision_result=_decision_result(confidence_score=60.0))
    h_a = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_a
    )
    h_b = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_b
    )
    assert h_a == h_b


def test_different_ticker_or_model_or_prompt_version_changes_hash():
    record = _completed_record()
    base = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record
    )
    assert base != analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="OTHER", model="gemini-3.5-flash", prompt_version="1", record=record
    )
    assert base != analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-2.0-flash", prompt_version="1", record=record
    )
    assert base != analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="2", record=record
    )


def test_insufficient_data_hash_stable_across_different_analyzed_at():
    record_1 = _insufficient_data_record(analyzed_at=datetime(2026, 8, 6, tzinfo=UTC))
    record_2 = _insufficient_data_record(analyzed_at=datetime(2026, 8, 7, tzinfo=UTC))
    h1 = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_1
    )
    h2 = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=record_2
    )
    assert h1 == h2


def test_insufficient_data_and_completed_have_different_hashes():
    completed = _completed_record()
    insufficient = _insufficient_data_record()
    h_completed = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=completed
    )
    h_insufficient = analysis_notification_hash(
        spk_record_id="ipo:QUICK:2026 / 7", ticker="QUICK", model="gemini-3.5-flash", prompt_version="1", record=insufficient
    )
    assert h_completed != h_insufficient
