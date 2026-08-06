import json
from datetime import datetime

import httpx
import pytest

from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.pdf import PdfCache
from halka_arz_advisor.ollama.analysis import analyze_company, verify_ollama_ready
from halka_arz_advisor.ollama.cache import AnalysisCache, compute_cache_key
from halka_arz_advisor.ollama.client import OllamaClient
from halka_arz_advisor.ollama.config import OllamaConfig
from halka_arz_advisor.ollama.exceptions import OllamaModelNotFoundError, OllamaUnavailableError

BASE_URL = "http://localhost:11434"
GENERATE_URL = f"{BASE_URL}/api/generate"


def make_config(**overrides) -> OllamaConfig:
    defaults = dict(base_url=BASE_URL, model="llama3.1:8b", timeout_seconds=5.0)
    defaults.update(overrides)
    return OllamaConfig(**defaults)


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


def _company_kwargs(disclosures, pdf_cache, analysis_cache, ollama_client, **overrides):
    kwargs = dict(
        spk_record_id="ipo:QUICK:2026 / 7",
        company_name="QUİCK SİGORTA A.Ş.",
        ticker="QUICK",
        facts=_facts_not_found(),
        disclosures=disclosures,
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        ollama_client=ollama_client,
    )
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------
# verify_ollama_ready: unavailable server / missing model
# --------------------------------------------------------------------------


def test_verify_ollama_ready_raises_when_server_unreachable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=f"{BASE_URL}/api/version")

    with OllamaClient(make_config()) as client, pytest.raises(OllamaUnavailableError):
        verify_ollama_ready(client)


def test_verify_ollama_ready_raises_when_model_not_pulled(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/version", json={"version": "0.5.1"})
    httpx_mock.add_response(url=f"{BASE_URL}/api/tags", json={"models": [{"name": "mistral:latest"}]})

    with OllamaClient(make_config(model="llama3.1:8b")) as client, pytest.raises(OllamaModelNotFoundError):
        verify_ollama_ready(client)


# --------------------------------------------------------------------------
# analyze_company: valid response, invalid JSON + retry, invalid signal,
# invented reference, cache hit, scanned/no-text skip, facts unchanged
# --------------------------------------------------------------------------


def test_valid_response_produces_completed_record(httpx_mock, build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(valid_response_payload())})

    with OllamaClient(make_config()) as client:
        record = analyze_company(
            **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
        )

    assert record.llm_status == "completed"
    assert record.llm_model == "llama3.1:8b"
    assert record.llm_analysis is not None
    assert record.llm_analysis.participation_signal == "participate"
    assert record.llm_warnings == ()


def test_unavailable_ollama_is_not_analyzed_by_analyze_company(httpx_mock, build_pdf_bytes, tmp_path):
    """analyze_company assumes verify_ollama_ready already ran; a transport
    failure from generate() propagates as a hard error rather than being
    silently downgraded — callers (the CLI) build the model_unavailable
    record themselves from a failed verify_ollama_ready() instead."""
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=GENERATE_URL)

    with OllamaClient(make_config()) as client:
        with pytest.raises(OllamaUnavailableError):
            analyze_company(**_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client))


def test_invalid_json_retries_once_then_succeeds(httpx_mock, build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    httpx_mock.add_response(url=GENERATE_URL, json={"response": "not valid json at all"})
    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(valid_response_payload())})

    with OllamaClient(make_config()) as client:
        record = analyze_company(
            **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
        )

    assert record.llm_status == "completed"
    assert len(record.llm_warnings) == 1
    assert "attempt 1" in record.llm_warnings[0]
    assert len(httpx_mock.get_requests()) == 2


def test_invalid_json_twice_marks_invalid_output_with_raw_response(httpx_mock, build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    httpx_mock.add_response(url=GENERATE_URL, json={"response": "still not json"})
    httpx_mock.add_response(url=GENERATE_URL, json={"response": "still not json either"})

    with OllamaClient(make_config()) as client:
        record = analyze_company(
            **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
        )

    assert record.llm_status == "invalid_output"
    assert record.llm_analysis is None
    assert record.raw_response == "still not json either"
    assert len(record.llm_warnings) == 2
    assert len(httpx_mock.get_requests()) == 2


def test_invalid_participation_signal_treated_as_invalid_output(httpx_mock, build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    bad_signal = valid_response_payload(participation_signal="buy_now")
    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(bad_signal)})
    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(bad_signal)})

    with OllamaClient(make_config()) as client:
        record = analyze_company(
            **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
        )

    assert record.llm_status == "invalid_output"
    assert any("participation_signal" in w for w in record.llm_warnings)


def test_invented_source_reference_is_rejected(httpx_mock, build_pdf_bytes, tmp_path):
    """A citation naming a disclosure_id/page the model was never shown
    must be rejected, even though the rest of the response is well-formed."""
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    invented = valid_response_payload(source_references=[{"disclosure_id": "d2", "page_number": 99}])
    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(invented)})
    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(invented)})

    with OllamaClient(make_config()) as client:
        record = analyze_company(
            **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
        )

    assert record.llm_status == "invalid_output"
    assert any("not part of the supplied context" in w for w in record.llm_warnings)


def test_second_run_with_unchanged_inputs_hits_cache(httpx_mock, build_pdf_bytes, tmp_path):
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(valid_response_payload())})
    # No second response registered — a second /api/generate call would fail the test.

    with OllamaClient(make_config()) as client:
        first = analyze_company(**_company_kwargs(disclosures, pdf_cache, analysis_cache, client))
        second = analyze_company(**_company_kwargs(disclosures, pdf_cache, analysis_cache, client))

    assert first.llm_status == "completed"
    assert second.llm_status == "completed"
    assert second.llm_analysis.company_summary == first.llm_analysis.company_summary
    assert len(httpx_mock.get_requests()) == 1


def test_scanned_pdf_with_no_extractable_text_is_insufficient_data(httpx_mock, build_pdf_bytes, tmp_path):
    """A company whose only cached PDF is scanned (image-only, no text
    layer) yields no context sections at all, so analysis is skipped
    without ever calling Ollama."""
    scanned_bytes = build_pdf_bytes(with_image=True)
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", scanned_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]
    # No httpx_mock response registered for /api/generate at all — a call would fail the test.

    with OllamaClient(make_config()) as client:
        record = analyze_company(
            **_company_kwargs(disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client)
        )

    assert record.llm_status == "insufficient_data"
    assert record.llm_analysis is None
    assert any("no extractable" in w for w in record.llm_warnings)


def test_deterministic_facts_are_not_mutated_by_analysis(httpx_mock, build_pdf_bytes, tmp_path):
    """The supplied ExtractedFacts object must come back untouched — the
    model's output never overwrites deterministic, regex-extracted facts."""
    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosures = [_disclosure(disclosure_id="d1", obj_id="obj-1")]

    src = SourceRef("approved_prospectus", "d1", "url-1", 1)
    facts = build_extracted_facts({"offering_price": FieldObservation(76.6, "76,60 TL", src)}, None)
    before = facts.offering_price

    # The model's own response tries to state a different price in prose —
    # this must not affect the ExtractedFacts object at all.
    payload = valid_response_payload(offering_summary="Fiyat 999,00 TL olarak belirtilmistir.")
    httpx_mock.add_response(url=GENERATE_URL, json={"response": json.dumps(payload)})

    with OllamaClient(make_config()) as client:
        analyze_company(
            **_company_kwargs(
                disclosures, pdf_cache, AnalysisCache(tmp_path / "analysis"), client, facts=facts
            )
        )

    assert facts.offering_price is before
    assert facts.offering_price.value == 76.6


# --------------------------------------------------------------------------
# cache key composition sanity check
# --------------------------------------------------------------------------


def test_cache_key_changes_when_model_changes():
    key_a = compute_cache_key(document_content_hash="h", model_name="a", prompt_version="1", schema_version="1")
    key_b = compute_cache_key(document_content_hash="h", model_name="b", prompt_version="1", schema_version="1")
    assert key_a != key_b
