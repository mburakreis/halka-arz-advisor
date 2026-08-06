from dataclasses import replace
from datetime import UTC, datetime

from halka_arz_advisor.decision.engine import DecisionResult
from halka_arz_advisor.gemini.analysis import compute_document_content_hash
from halka_arz_advisor.gemini.cache import AnalysisCache, compute_cache_key
from halka_arz_advisor.gemini.context import select_context_sections
from halka_arz_advisor.gemini.models import AnalysisRecord
from halka_arz_advisor.gemini.prompt import PROMPT_VERSION
from halka_arz_advisor.gemini.schema import SCHEMA_VERSION, AnalysisOutput, SourceReference
from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.extraction import build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.pdf import PdfCache
from halka_arz_advisor.notify.analysis_delivery import deliver_pending_analyses
from halka_arz_advisor.notify.analysis_identity import analysis_notification_hash
from halka_arz_advisor.notify.analysis_state import SentAnalysesState, load_state, save_state
from halka_arz_advisor.notify.telegram import TelegramSendError

# Real production versions — must match exactly what
# halka_arz_advisor.gemini.analysis.lookup_analysis derives its cache
# key from internally (it always imports these itself, not whatever a
# caller happens to pass to deliver_pending_analyses's own
# prompt_version= parameter, which only feeds the *notification dedup*
# hash, a separate concern — see analysis_notification_hash).
MODEL = "gemini-3.5-flash"
RECORD_ID = "ipo:QUICK:2026 / 7"


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


def _attachment(obj_id: str) -> KapAttachment:
    return KapAttachment(
        name="Izahname.pdf",
        url=f"https://www.kap.org.tr/tr/api/file/download/{obj_id}",
        content_type="application/pdf",
        document_role="primary_candidate",
        obj_id=obj_id,
    )


def _disclosure(*, disclosure_id: str, obj_id: str, notification_url: str = "https://www.kap.org.tr/tr/Bildirim/1") -> KapDisclosure:
    attachment = _attachment(obj_id)
    return KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=1,
        published_at=datetime(2026, 7, 24),
        company_name="QUİCK SİGORTA A.Ş.",
        ticker="QUICK",
        title="İzahname (SPK Tarafından Onaylanan)",
        summary="",
        document_type="approved_prospectus",
        notification_url=notification_url,
        attachment_urls=(),
        matched_spk_record_id=RECORD_ID,
        match_method="ticker",
        raw={},
        attachments=(attachment,),
        primary_document=attachment,
    )


def _analysis(**overrides) -> AnalysisOutput:
    defaults = dict(
        company_summary="Özet.",
        offering_summary="Halka arz özeti.",
        use_of_proceeds_summary="Fon kullanım özeti.",
        key_risks=("Risk 1",),
        positive_factors=("Olumlu 1",),
        negative_factors=(),
        missing_information=(),
        data_conflicts=(),
        decision_explanation="Gerekçe.",
        source_references=(SourceReference("d1", 1),),
    )
    defaults.update(overrides)
    return AnalysisOutput(**defaults)


def _seed_completed_analysis(
    *, pdf_cache: PdfCache, analysis_cache: AnalysisCache, disclosures, facts, analysis: AnalysisOutput, decision_result: DecisionResult
) -> AnalysisRecord:
    """Write a 'completed' record to analysis_cache under exactly the
    cache key lookup_analysis() would derive for this facts/disclosures/
    decision_result combination — mirrors what analyze_company() itself
    would have written."""
    sections = select_context_sections(disclosures, pdf_cache)
    assert sections, "test setup must produce at least one context section"
    content_hash = compute_document_content_hash(facts=facts, sections=sections, decision_result=decision_result)
    cache_key = compute_cache_key(
        document_content_hash=content_hash, model_name=MODEL, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION
    )
    record = AnalysisRecord(
        spk_record_id=RECORD_ID,
        llm_status="completed",
        llm_model=MODEL,
        llm_analysis=analysis,
        llm_warnings=(),
        analyzed_at=datetime(2026, 8, 6, tzinfo=UTC),
        decision_result=decision_result,
        document_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    analysis_cache.put(cache_key, record)
    return record


def _infer_company_name_and_ticker(record_id, disclosures):
    return (disclosures[0].company_name if disclosures else record_id), (disclosures[0].ticker if disclosures else None)


class _RecordingSender:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        if self.fail:
            raise TelegramSendError("simulated failure")
        self.messages.append(message)


def _facts_not_found():
    return build_extracted_facts(None, None)


# --------------------------------------------------------------------------
# completed analysis delivery
# --------------------------------------------------------------------------


def test_completed_analysis_is_delivered_and_marked_sent(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    _seed_completed_analysis(
        pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis(),
        decision_result=decision_result,
    )

    state = SentAnalysesState()
    sender = _RecordingSender()

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts},
        disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result},
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker,
        sender=sender,
    )

    assert result.sent_record_ids == [RECORD_ID]
    assert len(sender.messages) == 1
    assert "QUİCK SİGORTA A.Ş." in sender.messages[0]
    assert len(state.sent_hashes) == 1


# --------------------------------------------------------------------------
# insufficient-data / invalid-output delivery — both now deliverable via
# the deterministic fallback, since decision_result is always the source
# of truth regardless of Gemini's own status.
# --------------------------------------------------------------------------


def test_insufficient_data_analysis_is_delivered(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(with_image=True))  # scanned -> no extractable text
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    state = SentAnalysesState()
    sender = _RecordingSender()

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts},
        disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result},
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker,
        sender=sender,
    )

    assert result.sent_record_ids == [RECORD_ID]
    assert "Katıl" in sender.messages[0]  # from decision_result, not Gemini


def test_invalid_output_status_is_still_delivered_via_deterministic_fallback(build_pdf_bytes, tmp_path):
    """Gemini's narrative failed validation, but the deterministic
    decision_result is unaffected by that — the message still goes out,
    explained by halka_arz_advisor.decision.explain.format_explanation
    instead of Gemini's (missing) narrative."""
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    sections = select_context_sections(disclosures, pdf_cache)
    content_hash = compute_document_content_hash(facts=facts, sections=sections, decision_result=decision_result)
    cache_key = compute_cache_key(document_content_hash=content_hash, model_name=MODEL, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION)
    analysis_cache.put(
        cache_key,
        AnalysisRecord(
            spk_record_id=RECORD_ID, llm_status="invalid_output", llm_model=MODEL, llm_analysis=None,
            llm_warnings=("bad json",), analyzed_at=datetime(2026, 8, 6, tzinfo=UTC), decision_result=decision_result,
            document_content_hash=content_hash, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
        ),
    )

    state = SentAnalysesState()
    sender = _RecordingSender()
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == [RECORD_ID]
    assert len(sender.messages) == 1
    assert "Katıl" in sender.messages[0]


def test_no_cached_analysis_yet_is_skipped(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")  # nothing ever written to it

    state = SentAnalysesState()
    sender = _RecordingSender()
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.skipped_no_analysis_record_ids == [RECORD_ID]
    assert sender.messages == []


def test_company_with_no_decision_result_is_skipped(build_pdf_bytes, tmp_path):
    """A company absent from decision_results (no matched KAP/SPK data
    at all) is skipped, even if it somehow has cached facts."""
    pdf_cache = PdfCache(tmp_path / "pdfs")
    disclosures: list[KapDisclosure] = []
    facts = _facts_not_found()
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    state = SentAnalysesState()
    sender = _RecordingSender()
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == []
    assert sender.messages == []


# --------------------------------------------------------------------------
# duplicate suppression / changed analysis resent
# --------------------------------------------------------------------------


def test_already_sent_unchanged_analysis_is_not_resent(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    record = _seed_completed_analysis(
        pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis(),
        decision_result=decision_result,
    )

    already_sent_hash = analysis_notification_hash(
        spk_record_id=RECORD_ID, ticker="QUICK", model=MODEL, prompt_version=PROMPT_VERSION, record=record
    )
    state = SentAnalysesState(sent_hashes={already_sent_hash})
    sender = _RecordingSender()

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == []
    assert result.skipped_unchanged_record_ids == [RECORD_ID]
    assert sender.messages == []


def test_changed_analysis_content_is_resent(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    record = _seed_completed_analysis(
        pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis(),
        decision_result=decision_result,
    )

    # A hash computed from a *different* (older) analysis content for the
    # same company is already in the sent-state...
    older_variant = replace(record, llm_analysis=_analysis(decision_explanation="Eski gerekçe."))
    stale_hash = analysis_notification_hash(
        spk_record_id=RECORD_ID, ticker="QUICK", model=MODEL, prompt_version=PROMPT_VERSION, record=older_variant
    )
    state = SentAnalysesState(sent_hashes={stale_hash})
    sender = _RecordingSender()

    # ...but the currently cached analysis has different content, so its
    # hash differs from stale_hash -> must be resent.
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == [RECORD_ID]
    assert len(sender.messages) == 1
    assert stale_hash in state.sent_hashes  # old hash stays; it's a set, not replaced
    assert len(state.sent_hashes) == 2


def test_changed_decision_result_invalidates_the_gemini_cache_entry(build_pdf_bytes, tmp_path):
    """A materially different deterministic decision (e.g. a resolved
    conflict changed the signal) changes the Gemini cache key too (see
    compute_document_content_hash's decision_signature component) — the
    old cached narrative is never reused for a new decision; it's
    correctly a cache miss ("no analysis yet" for *this* decision), not
    a silent resend of stale content explaining a different signal."""
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    old_decision = _decision_result(signal="skip")
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    old_record = _seed_completed_analysis(
        pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis(),
        decision_result=old_decision,
    )
    already_sent_hash = analysis_notification_hash(
        spk_record_id=RECORD_ID, ticker="QUICK", model=MODEL, prompt_version=PROMPT_VERSION, record=old_record
    )
    state = SentAnalysesState(sent_hashes={already_sent_hash})
    sender = _RecordingSender()

    # A fresh run recomputed the decision differently (e.g. new data
    # resolved a conflict) — same Gemini narrative content, but a
    # different cache entry (different decision_result -> different
    # content hash), so this is a genuine cache miss, correctly skipped
    # as "no analysis yet" for the *new* decision rather than resending
    # stale content.
    new_decision = _decision_result(signal="participate")
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: new_decision}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == []
    assert result.skipped_no_analysis_record_ids == [RECORD_ID]
    assert sender.messages == []


# --------------------------------------------------------------------------
# failed Telegram request not updating state / retried later
# --------------------------------------------------------------------------


def test_failed_send_does_not_update_state_and_is_retried_next_call(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    _seed_completed_analysis(
        pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis(),
        decision_result=decision_result,
    )

    state = SentAnalysesState()
    failing_sender = _RecordingSender(fail=True)

    first = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=failing_sender,
    )
    assert first.failed_record_ids == [RECORD_ID]
    assert first.sent_record_ids == []
    assert state.sent_hashes == set()

    # Next run (same state, this time delivery succeeds) picks it right back up.
    working_sender = _RecordingSender()
    second = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=working_sender,
    )
    assert second.sent_record_ids == [RECORD_ID]
    assert len(state.sent_hashes) == 1


# --------------------------------------------------------------------------
# dry-run behavior: nothing is persisted unless the caller saves state
# --------------------------------------------------------------------------


def test_dry_run_like_usage_never_touches_state_file_on_disk(build_pdf_bytes, tmp_path):
    """deliver_pending_analyses() itself never writes to disk — it only
    mutates the in-memory state object handed to it. A dry-run CLI
    invocation (this project's scripts/send_pending_analyses.py) simply
    never calls save_state() afterward, exactly like omitting that call
    here."""
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    decision_result = _decision_result()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    _seed_completed_analysis(
        pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis(),
        decision_result=decision_result,
    )

    state_path = tmp_path / "state" / "sent_analyses.json"
    state, _ = load_state(state_path)
    sender = _RecordingSender()  # a dry-run sender that "succeeds" (just prints, in the real CLI)

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures},
        decision_results={RECORD_ID: decision_result}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )
    # A real send/message-preview did happen (this is what --dry-run prints)...
    assert result.sent_record_ids == [RECORD_ID]
    assert len(sender.messages) == 1
    # ...but nothing was ever written to disk, since save_state() was never called.
    assert not state_path.exists()

    reloaded, is_first_run = load_state(state_path)
    assert is_first_run is True
    assert reloaded.sent_hashes == set()


def test_save_state_persists_only_when_explicitly_called(tmp_path):
    path = tmp_path / "sent.json"
    state = SentAnalysesState(sent_hashes={"abc"})
    assert not path.exists()
    save_state(path, state)
    assert path.exists()
