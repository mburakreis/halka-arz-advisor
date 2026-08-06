import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors

from halka_arz_advisor.gemini.analysis import analyze_company, verify_gemini_ready
from halka_arz_advisor.gemini.cache import AnalysisCache, compute_cache_key
from halka_arz_advisor.gemini.client import GeminiClient
from halka_arz_advisor.gemini.config import GeminiConfig
from halka_arz_advisor.gemini.exceptions import GeminiModelNotFoundError, GeminiUnavailableError
from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.pdf import PdfCache


def make_config(**overrides) -> GeminiConfig:
    defaults = dict(api_key="test-key", model="gemini-3.5-flash", timeout_seconds=5.0)
    defaults.update(overrides)
    return GeminiConfig(**defaults)


def client_error(code: int, message: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"message": message})


class _FakeModels:
    """Each queued item in ``generate_results`` is consumed in order by
    successive ``generate_content`` calls — mirrors a retry sequence."""

    def __init__(self, *, list_error=None, get_error=None, generate_results=None):
        self._list_error = list_error
        self._get_error = get_error
        self._generate_results = list(generate_results or [])
        self.generate_call_count = 0

    def list(self, config=None):
        if self._list_error:
            raise self._list_error
        return iter([SimpleNamespace(name="models/gemini-3.5-flash")])

    def get(self, *, model):
        if self._get_error:
            raise self._get_error
        return SimpleNamespace(name=f"models/{model}")

    def generate_content(self, *, model, contents, config):
        self.generate_call_count += 1
        item = self._generate_results.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)


class _FakeGenaiClient:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models

    def close(self) -> None:
        pass


def make_client(**models_kwargs) -> GeminiClient:
    fake = _FakeGenaiClient(_FakeModels(**models_kwargs))
    return GeminiClient(make_config(), client=fake)


def _attachment(obj_id: str) -> KapAttachment:
    return KapAttachment(
        name="Izahname.pdf",
        url=f"https://www.kap.org.tr/tr/api/file/download/{obj_id}",
        content_type="application/pdf",
        document_role="primary_candidate",
        obj_id=obj_id,
    )


def _disclosure(*, disclosure_id: str, obj_id: str, document_type: str = "approved_prospectus") -> KapDisclosure:
    attachment = _attachment(obj_id)
    return KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=1,
        published_at=datetime(2026, 7, 24),
        company_name="QUİCK SİGORTA A.Ş.",
        ticker="QUICK",
        title="İzahname (SPK Tarafından Onaylanan)",
        summary="",
        document_type=document_type,
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id="ipo:QUICK:2026 / 7",
        match_method="ticker",
        raw={},
        attachments=(attachment,),
        primary_document=attachment,
    )


def _facts_not_found():
    return build_extracted_facts(None, None)


def valid_response_payload(**overrides) -> dict:
    payload = {
        "company_summary": "Şirket özeti.",
        "offering_summary": "Halka arz özeti.",
        "use_of_proceeds_summary": "Fon kullanım özeti.",
        "key_risks": ["Risk 1"],
        "positive_factors": ["Olumlu 1"],
        "negative_factors": ["Olumsuz 1"],
        "missing_information": [],
        "data_conflicts": [],
        "participation_signal": "participate",
        "participation_rationale": "Gerekçe.",
        "confidence": 0.7,
        "source_references": [{"disclosure_id": "d1", "page_number": 1}],
    }
    payload.update(overrides)
    return payload


def _company_kwargs(disclosures, pdf_cache, analysis_cache, gemini_client, **overrides):
    kwargs = dict(
        spk_record_id="ipo:QUICK:2026 / 7",
        company_name="QUİCK SİGORTA A.Ş.",
        ticker="QUICK",
        facts=_facts_not_found(),
        disclosures=disclosures,
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        gemini_client=gemini_client,
    )
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------
# verify_gemini_ready: unavailable server / missing model
# --------------------------------------------------------------------------


def test_verify_gemini_ready_raises_when_unreachable():
    client = make_client(list_error=httpx.ConnectError("connection refused"))
    with pytest.raises(GeminiUnavailableError):
        verify_gemini_ready(client)


def test_verify_gemini_ready_raises_when_model_not_available():
    client = make_client(get_error=client_error(404, "Model is not found"))
    with pytest.raises(GeminiModelNotFoundError):
        verify_gemini_ready(client)


# --------------------------------------------------------------------------
# analyze_company: valid response, invalid JSON + retry, invalid signal,
# invented reference, cache hit, scanned/no-text skip, facts unchanged
# --------------------------------------------------------------------------


def test_valid_response_produces_completed_record(build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    client = make_client(generate_results=[json.dumps(valid_response_payload())])
    record = analyze_company(**_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client))

    assert record.llm_status == "completed"
    assert record.llm_model == "gemini-3.5-flash"
    assert record.llm_analysis is not None
    assert record.llm_analysis.participation_signal == "participate"
    assert record.llm_warnings == ()


def test_transient_error_propagates_and_is_not_cached(build_pdf_bytes, tmp_path):
    """A rate-limit/quota/temporary error must propagate out of
    analyze_company (so the CLI can skip just this company and retry it
    on a later run) rather than being cached as a failure."""
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    client = make_client(generate_results=[client_error(429, "Rate limit exceeded")])
    analysis_cache = AnalysisCache(tmp_path / "analysis")
    with pytest.raises(GeminiUnavailableError):
        analyze_company(**_company_kwargs(disclosures, pdf_cache, analysis_cache, client))

    assert list((tmp_path / "analysis").glob("*.json")) == []


def test_invalid_json_retries_once_then_succeeds(build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    client = make_client(generate_results=["not valid json at all", json.dumps(valid_response_payload())])
    record = analyze_company(
        **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
    )

    assert record.llm_status == "completed"
    assert len(record.llm_warnings) == 1
    assert "attempt 1" in record.llm_warnings[0]


def test_invalid_json_twice_marks_invalid_output_with_raw_response(build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    client = make_client(generate_results=["still not json", "still not json either"])
    record = analyze_company(
        **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
    )

    assert record.llm_status == "invalid_output"
    assert record.llm_analysis is None
    assert record.raw_response == "still not json either"
    assert len(record.llm_warnings) == 2


def test_invalid_participation_signal_treated_as_invalid_output(build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    bad_signal = json.dumps(valid_response_payload(participation_signal="buy_now"))
    client = make_client(generate_results=[bad_signal, bad_signal])
    record = analyze_company(
        **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
    )

    assert record.llm_status == "invalid_output"
    assert any("participation_signal" in w for w in record.llm_warnings)


def test_invented_source_reference_is_rejected(build_pdf_bytes, tmp_path):
    """A citation naming a disclosure_id/page the model was never shown
    must be rejected, even though the rest of the response is well-formed."""
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    invented = json.dumps(valid_response_payload(source_references=[{"disclosure_id": "d2", "page_number": 99}]))
    client = make_client(generate_results=[invented, invented])
    record = analyze_company(
        **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
    )

    assert record.llm_status == "invalid_output"
    assert any("not part of the supplied context" in w for w in record.llm_warnings)


def test_second_run_with_unchanged_inputs_hits_cache(build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    # Only one generate result queued — a second live call would raise
    # IndexError (list.pop on empty list), failing the test.
    client = make_client(generate_results=[json.dumps(valid_response_payload())])

    first = analyze_company(**_company_kwargs(disclosures, pdf_cache, analysis_cache, client))
    second = analyze_company(**_company_kwargs(disclosures, pdf_cache, analysis_cache, client))

    assert first.llm_status == "completed"
    assert second.llm_status == "completed"
    assert second.llm_analysis.company_summary == first.llm_analysis.company_summary
    assert second.analyzed_at == first.analyzed_at


def test_scanned_pdf_with_no_extractable_text_is_insufficient_data(build_pdf_bytes, tmp_path):
    """A company whose only cached PDF is scanned (image-only, no text
    layer) yields no context sections at all, so analysis is skipped
    without ever calling Gemini."""
    scanned_bytes = build_pdf_bytes(with_image=True)
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", scanned_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    # No generate results queued at all — a call would raise IndexError.
    client = make_client(generate_results=[])
    record = analyze_company(
        **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
    )

    assert record.llm_status == "insufficient_data"
    assert record.llm_analysis is None
    assert any("no extractable" in w for w in record.llm_warnings)


def test_deterministic_facts_are_not_mutated_by_analysis(build_pdf_bytes, tmp_path):
    """The supplied ExtractedFacts object must come back untouched — the
    model's output never overwrites deterministic, regex-extracted facts."""
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    src = SourceRef("approved_prospectus", "d1", "url-1", 1)
    facts = build_extracted_facts({"offering_price": FieldObservation(76.6, "76,60 TL", src)}, None)

    payload = json.dumps(valid_response_payload(offering_summary="Fiyat 999,00 TL olarak belirtilmistir."))
    client = make_client(generate_results=[payload])

    analyze_company(
        **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client, facts=facts)
    )

    assert facts.offering_price.value == 76.6
    assert facts.offering_price.raw_snippet == "76,60 TL"


# --------------------------------------------------------------------------
# cache key composition sanity check
# --------------------------------------------------------------------------


def test_cache_key_changes_when_model_changes():
    key_a = compute_cache_key(document_content_hash="h", model_name="a", prompt_version="1", schema_version="1")
    key_b = compute_cache_key(document_content_hash="h", model_name="b", prompt_version="1", schema_version="1")
    assert key_a != key_b
