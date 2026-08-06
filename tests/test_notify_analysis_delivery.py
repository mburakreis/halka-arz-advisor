from dataclasses import replace
from datetime import UTC, datetime

from halka_arz_advisor.gemini.analysis import compute_document_content_hash
from halka_arz_advisor.gemini.cache import AnalysisCache, compute_cache_key
from halka_arz_advisor.gemini.context import select_context_sections
from halka_arz_advisor.gemini.models import AnalysisRecord
from halka_arz_advisor.gemini.schema import AnalysisOutput, SourceReference
from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.extraction import build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.pdf import PdfCache
from halka_arz_advisor.notify.analysis_delivery import deliver_pending_analyses
from halka_arz_advisor.notify.analysis_identity import analysis_notification_hash
from halka_arz_advisor.notify.analysis_state import SentAnalysesState, load_state, save_state
from halka_arz_advisor.notify.telegram import TelegramSendError

MODEL = "gemini-3.5-flash"
PROMPT_VERSION = "1"
RECORD_ID = "ipo:QUICK:2026 / 7"


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
        participation_signal="participate",
        participation_rationale="Gerekçe.",
        confidence=0.8,
        source_references=(SourceReference("d1", 1),),
    )
    defaults.update(overrides)
    return AnalysisOutput(**defaults)


def _seed_completed_analysis(*, pdf_cache: PdfCache, analysis_cache: AnalysisCache, disclosures, facts, analysis: AnalysisOutput) -> AnalysisRecord:
    """Write a 'completed' record to analysis_cache under exactly the
    cache key lookup_analysis() would derive for this facts/disclosures
    combination — mirrors what analyze_company() itself would have
    written."""
    sections = select_context_sections(disclosures, pdf_cache)
    assert sections, "test setup must produce at least one context section"
    content_hash = compute_document_content_hash(facts=facts, sections=sections)
    cache_key = compute_cache_key(
        document_content_hash=content_hash, model_name=MODEL, prompt_version=PROMPT_VERSION, schema_version="1"
    )
    record = AnalysisRecord(
        spk_record_id=RECORD_ID,
        llm_status="completed",
        llm_model=MODEL,
        llm_analysis=analysis,
        llm_warnings=(),
        analyzed_at=datetime(2026, 8, 6, tzinfo=UTC),
        document_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        schema_version="1",
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
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    _seed_completed_analysis(pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis())

    state = SentAnalysesState()
    sender = _RecordingSender()

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts},
        disclosures_by_record={RECORD_ID: disclosures},
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
# insufficient-data delivery
# --------------------------------------------------------------------------


def test_insufficient_data_analysis_is_delivered(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(with_image=True))  # scanned -> no extractable text
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    state = SentAnalysesState()
    sender = _RecordingSender()

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts},
        disclosures_by_record={RECORD_ID: disclosures},
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker,
        sender=sender,
    )

    assert result.sent_record_ids == [RECORD_ID]
    assert "Yetersiz veri" in sender.messages[0]


def test_invalid_output_status_is_not_delivered(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    sections = select_context_sections(disclosures, pdf_cache)
    content_hash = compute_document_content_hash(facts=facts, sections=sections)
    cache_key = compute_cache_key(document_content_hash=content_hash, model_name=MODEL, prompt_version=PROMPT_VERSION, schema_version="1")
    analysis_cache.put(
        cache_key,
        AnalysisRecord(
            spk_record_id=RECORD_ID, llm_status="invalid_output", llm_model=MODEL, llm_analysis=None,
            llm_warnings=("bad json",), analyzed_at=datetime(2026, 8, 6, tzinfo=UTC),
            document_content_hash=content_hash, prompt_version=PROMPT_VERSION, schema_version="1",
        ),
    )

    state = SentAnalysesState()
    sender = _RecordingSender()
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == []
    assert result.skipped_no_analysis_record_ids == [RECORD_ID]
    assert sender.messages == []


def test_no_cached_analysis_yet_is_skipped(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    analysis_cache = AnalysisCache(tmp_path / "analysis")  # nothing ever written to it

    state = SentAnalysesState()
    sender = _RecordingSender()
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.skipped_no_analysis_record_ids == [RECORD_ID]
    assert sender.messages == []


# --------------------------------------------------------------------------
# duplicate suppression / changed analysis resent
# --------------------------------------------------------------------------


def test_already_sent_unchanged_analysis_is_not_resent(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    record = _seed_completed_analysis(pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis())

    already_sent_hash = analysis_notification_hash(
        spk_record_id=RECORD_ID, ticker="QUICK", model=MODEL, prompt_version=PROMPT_VERSION, record=record
    )
    state = SentAnalysesState(sent_hashes={already_sent_hash})
    sender = _RecordingSender()

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
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
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    record = _seed_completed_analysis(pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis())

    # A hash computed from a *different* (older) analysis content for the
    # same company is already in the sent-state...
    older_variant = replace(record, llm_analysis=_analysis(participation_rationale="Eski gerekçe."))
    stale_hash = analysis_notification_hash(
        spk_record_id=RECORD_ID, ticker="QUICK", model=MODEL, prompt_version=PROMPT_VERSION, record=older_variant
    )
    state = SentAnalysesState(sent_hashes={stale_hash})
    sender = _RecordingSender()

    # ...but the currently cached analysis has different content, so its
    # hash differs from stale_hash -> must be resent.
    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=sender,
    )

    assert result.sent_record_ids == [RECORD_ID]
    assert len(sender.messages) == 1
    assert stale_hash in state.sent_hashes  # old hash stays; it's a set, not replaced
    assert len(state.sent_hashes) == 2


# --------------------------------------------------------------------------
# failed Telegram request not updating state / retried later
# --------------------------------------------------------------------------


def test_failed_send_does_not_update_state_and_is_retried_next_call(build_pdf_bytes, tmp_path):
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir"))
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    facts = _facts_not_found()
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    _seed_completed_analysis(pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis())

    state = SentAnalysesState()
    failing_sender = _RecordingSender(fail=True)

    first = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
        analysis_cache=analysis_cache, model=MODEL, prompt_version=PROMPT_VERSION, state=state,
        infer_company_name_and_ticker=_infer_company_name_and_ticker, sender=failing_sender,
    )
    assert first.failed_record_ids == [RECORD_ID]
    assert first.sent_record_ids == []
    assert state.sent_hashes == set()

    # Next run (same state, this time delivery succeeds) picks it right back up.
    working_sender = _RecordingSender()
    second = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
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
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    _seed_completed_analysis(pdf_cache=pdf_cache, analysis_cache=analysis_cache, disclosures=disclosures, facts=facts, analysis=_analysis())

    state_path = tmp_path / "state" / "sent_analyses.json"
    state, _ = load_state(state_path)
    sender = _RecordingSender()  # a dry-run sender that "succeeds" (just prints, in the real CLI)

    result = deliver_pending_analyses(
        company_facts={RECORD_ID: facts}, disclosures_by_record={RECORD_ID: disclosures}, pdf_cache=pdf_cache,
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
