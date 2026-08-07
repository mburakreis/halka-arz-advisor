"""End-to-end integration test for the new wiring: deterministic
decision engine -> Gemini narrative explanation -> Telegram formatting.

Uses real-shaped KAP/SPK data (the same models
halka_arz_advisor.kap.documents.process_disclosure_documents would
produce) but never touches the network or OCR — PdfCache/attachment
resolution are bypassed entirely by constructing already-processed
KapDisclosure objects directly, exactly like
halka_arz_advisor.kap.documents.aggregate_company_facts's own tests do.
"""

import json
from datetime import date, datetime
from types import SimpleNamespace

from halka_arz_advisor.decision.pipeline import compute_decision_results
from halka_arz_advisor.gemini.analysis import analyze_company
from halka_arz_advisor.gemini.cache import AnalysisCache
from halka_arz_advisor.gemini.client import GeminiClient
from halka_arz_advisor.gemini.config import GeminiConfig
from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.kap.financials import FinancialObservation
from halka_arz_advisor.kap.financials import SourceRef as ObsSourceRef
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.pdf import PdfCache
from halka_arz_advisor.notify.analysis_formatting import format_analysis_notification

RECORD_ID = "ipo:ORNK:2026 / 1"


class _FakeModels:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def generate_content(self, *, model, contents, config):
        return SimpleNamespace(text=self._response_text)


class _FakeGenaiClient:
    def __init__(self, response_text: str) -> None:
        self.models = _FakeModels(response_text)

    def close(self) -> None:
        pass


def _make_gemini_client(response_payload: dict) -> GeminiClient:
    config = GeminiConfig(api_key="test-key", model="gemini-3.5-flash", timeout_seconds=5.0)
    return GeminiClient(config, client=_FakeGenaiClient(json.dumps(response_payload)))


def test_real_shaped_pipeline_from_kap_data_through_decision_engine_to_telegram_message(build_pdf_bytes, tmp_path):
    src_p = SourceRef("approved_prospectus", "d-p", "url-p", 1)
    src_a = SourceRef("investor_sale_announcement", "d-a", "url-a", 1)
    obs_src = ObsSourceRef("price_determination_report", "d-pdr", "url-pdr", 26, "digital")

    facts = build_extracted_facts(
        {
            "business_description": FieldObservation("Enerji sektöründe faaliyet gösteren bir şirket.", "snip", src_p),
            "key_risk_factors": FieldObservation(["Elektrik fiyat riski"], "snip", src_p),
            "use_of_proceeds_plan": FieldObservation(["Kapasite artışı"], "snip", src_p),
            "capital_increase_shares": FieldObservation(1000.0, "snip", src_p),
            "secondary_sale_shares": FieldObservation(200.0, "snip", src_p),
            "total_offered_shares": FieldObservation(1200.0, "snip", src_p),
            "capital_increase_ratio": FieldObservation(50.0, "snip", src_p),
            "subscription_start_date": FieldObservation(date(2026, 1, 1), "snip", src_p),
            "subscription_end_date": FieldObservation(date(2026, 1, 3), "snip", src_p),
            "distribution_method": FieldObservation("sabit fiyatla talep toplama", "snip", src_p),
            "offering_price": FieldObservation(10.0, "snip", src_p),
            "currency": FieldObservation("TRY", "snip", src_p),
        },
        {"offering_price": FieldObservation(10.0, "snip", src_a), "currency": FieldObservation("TRY", "snip", src_a)},
    )

    def obs(metric: str, value: float, year: int) -> FinancialObservation:
        return FinancialObservation(
            metric, value, "TRY", "unit", date(year, 1, 1), date(year, 12, 31), "ANNUAL", "standalone", None, str(value), obs_src
        )

    financial_observations = (
        obs("revenue", 1000.0, 2023), obs("revenue", 1300.0, 2024),
        obs("net_income", 100.0, 2024),
        obs("financial_debt", 400.0, 2024), obs("cash_and_equivalents", 100.0, 2024),
        obs("equity", 1000.0, 2024),
        obs("current_assets", 300.0, 2024), obs("current_liabilities", 150.0, 2024),
        obs("operating_cash_flow", 90.0, 2024),
        obs("operating_profit", 120.0, 2024), obs("finance_expense", 20.0, 2024),
    )

    attachment = KapAttachment(
        name="Izahname.pdf", url="https://www.kap.org.tr/tr/api/file/download/obj-1",
        content_type="application/pdf", document_role="primary_candidate", obj_id="obj-1",
    )
    disclosure = KapDisclosure(
        disclosure_id="d-p",
        disclosure_index=1,
        published_at=datetime(2026, 1, 1),
        company_name="Örnek Enerji A.Ş.",
        ticker="ORNK",
        title="İzahname (SPK Tarafından Onaylanan)",
        summary="",
        document_type="approved_prospectus",
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id=RECORD_ID,
        match_method="ticker",
        raw={},
        attachments=(attachment,),
        primary_document=attachment,
        pdf_status="ok",
        extracted_facts=facts,
        financial_observations=financial_observations,
    )

    decision_results = compute_decision_results([disclosure], reference_date=datetime(2026, 1, 10))
    assert RECORD_ID in decision_results
    decision_result = decision_results[RECORD_ID]
    assert decision_result.signal in ("participate", "limited_participation", "skip", "insufficient_data")
    assert decision_result.rule_version == "expert_v0"

    pdf_bytes = build_pdf_bytes(text="Halka arz talep toplama ile ilgili bilgiler bu sayfada yer almaktadir")
    pdf_cache = PdfCache(tmp_path / "pdfs")
    pdf_cache.put("obj-1", pdf_bytes)
    analysis_cache = AnalysisCache(tmp_path / "analysis")

    response_payload = {
        "company_summary": "Şirket özeti.",
        "offering_summary": "Halka arz özeti.",
        "use_of_proceeds_summary": "Fon kullanım özeti.",
        "key_risks": ["Risk 1"],
        # Left empty: halka_arz_advisor.gemini.grounding requires every
        # positive_factors/negative_factors item to be traceable to the
        # deterministic decision_result's own precomputed contributions,
        # and this fixture doesn't try to model that precisely.
        "positive_factors": [],
        "negative_factors": [],
        "missing_information": [],
        "data_conflicts": [],
        "decision_explanation": f"Karar motoru {decision_result.signal} sinyalini üretti.",
        "source_references": [{"disclosure_id": "d-p", "page_number": 1}],
    }
    gemini_client = _make_gemini_client(response_payload)

    record = analyze_company(
        spk_record_id=RECORD_ID,
        company_name="Örnek Enerji A.Ş.",
        ticker="ORNK",
        facts=facts,
        disclosures=[disclosure],
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        gemini_client=gemini_client,
        decision_result=decision_result,
    )

    assert record.llm_status == "completed"
    assert record.decision_result is decision_result

    message = format_analysis_notification(
        company_name="Örnek Enerji A.Ş.",
        ticker="ORNK",
        facts=facts,
        record=record,
        disclosure_notification_urls={"d-p": "https://www.kap.org.tr/tr/Bildirim/1"},
    )

    assert "Örnek Enerji A.Ş." in message
    assert "Karar desteği:" in message
    assert "Kategori skorları:" in message
    assert "https://www.kap.org.tr/tr/Bildirim/1" in message
    # Gemini's narrative explains the deterministic signal, never a
    # competing one of its own (the field doesn't even exist anymore).
    assert decision_result.signal in response_payload["decision_explanation"]
