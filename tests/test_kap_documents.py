import struct
from datetime import datetime

import pytest

from halka_arz_advisor.kap.attachments import ATTACHMENT_DETAIL_URL_TEMPLATE, FILE_DOWNLOAD_URL_TEMPLATE
from halka_arz_advisor.kap.documents import aggregate_company_facts, process_disclosure_documents
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.probe.config import ProbeConfig

DISCLOSURE_INDEX = 1636670
DETAIL_URL = ATTACHMENT_DETAIL_URL_TEMPLATE.format(index=DISCLOSURE_INDEX)


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=1, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _disclosure(**overrides) -> KapDisclosure:
    defaults = dict(
        disclosure_id="disc-1",
        disclosure_index=DISCLOSURE_INDEX,
        published_at=datetime(2026, 7, 24),
        company_name="QUİCK SİGORTA A.Ş.",
        ticker="QUICK",
        title="İzahname (SPK Tarafından Onaylanan)",
        summary="",
        document_type="approved_prospectus",
        notification_url=f"https://www.kap.org.tr/tr/Bildirim/{DISCLOSURE_INDEX}",
        attachment_urls=(),
        matched_spk_record_id="ipo:QUICK:2026 / 7",
        match_method="ticker",
        raw={},
    )
    defaults.update(overrides)
    return KapDisclosure(**defaults)


def _detail_payload(attachments: list[dict]) -> list[dict]:
    return [
        {
            "disclosure": {
                "disclosureBasic": {"disclosureId": "disc-1", "disclosureIndex": DISCLOSURE_INDEX, "companyTitle": "X"},
                "disclosureDetail": {},
            },
            "disclosureBody": [],
            "attachments": attachments,
        }
    ]


def _java_wrap(pdf_bytes: bytes) -> bytes:
    header = bytes.fromhex("aced000575720002" + "5b42acf317f8060854e00200007870")
    return header + struct.pack(">I", len(pdf_bytes)) + pdf_bytes


# --------------------------------------------------------------------------
# process_disclosure_documents
# --------------------------------------------------------------------------


def test_full_success_extracts_from_prospectus(httpx_mock, build_pdf_bytes):
    httpx_mock.add_response(
        url=DETAIL_URL, json=_detail_payload([{"objId": "obj-1", "fileName": "Izahname.pdf", "fileExtension": "pdf"}])
    )
    pdf_bytes = build_pdf_bytes(text="artirilacak 2.380.000.000 tl nominal degerli")
    httpx_mock.add_response(url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id="obj-1"), content=_java_wrap(pdf_bytes))

    result = process_disclosure_documents(_disclosure(), config=fast_config())

    assert result.pdf_status == "ok"
    assert len(result.attachments) == 1
    assert result.primary_document.obj_id == "obj-1"
    assert result.extracted_facts is not None
    assert result.extracted_facts.capital_increase_shares.status == "extracted"
    assert result.extracted_facts.capital_increase_shares.value == 2380000000.0
    assert result.extraction_warnings == ()


def test_scanned_pdf_records_status_and_no_fabricated_facts(httpx_mock, build_pdf_bytes):
    httpx_mock.add_response(
        url=DETAIL_URL, json=_detail_payload([{"objId": "obj-2", "fileName": "TSSD.pdf", "fileExtension": "pdf"}])
    )
    pdf_bytes = build_pdf_bytes(with_image=True)
    httpx_mock.add_response(url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id="obj-2"), content=_java_wrap(pdf_bytes))

    result = process_disclosure_documents(
        _disclosure(document_type="investor_sale_announcement"), config=fast_config()
    )

    assert result.pdf_status == "scanned"
    assert result.extracted_facts is None
    assert any("scanned" in w for w in result.extraction_warnings)


def test_no_attachments_reports_unavailable(httpx_mock):
    httpx_mock.add_response(url=DETAIL_URL, json=_detail_payload([]))

    result = process_disclosure_documents(_disclosure(), config=fast_config())

    assert result.pdf_status == "unavailable"
    assert result.primary_document is None
    assert "no attachments" in result.extraction_warnings[0]


def test_only_non_primary_attachments_reports_unavailable_not_fabricated(httpx_mock):
    """A disclosure whose only attachment is a signature page must never
    be treated as if it had a real primary document."""
    httpx_mock.add_response(
        url=DETAIL_URL,
        json=_detail_payload([{"objId": "obj-3", "fileName": "İmza Sirküleri.pdf", "fileExtension": "pdf"}]),
    )

    result = process_disclosure_documents(_disclosure(), config=fast_config())

    assert result.pdf_status == "unavailable"
    assert result.primary_document is None
    assert len(result.attachments) == 1  # still recorded, just not selected as primary


def test_no_disclosure_index_skips_network_entirely(httpx_mock):
    # No httpx_mock response registered — a network call would fail the test.
    result = process_disclosure_documents(_disclosure(disclosure_index=None), config=fast_config())

    assert result.pdf_status == "unavailable"
    assert "disclosure index" in result.extraction_warnings[0]


def test_extraction_skipped_for_non_eligible_document_type(httpx_mock, build_pdf_bytes):
    """trading_start gets attachments resolved and its PDF read, but
    field extraction is not attempted — rule 6 scopes extraction to the
    prospectus, investor announcement, IPO results, and price
    determination report disclosures only (each covered separately, see
    test_ipo_results_extraction_populates_post_offer_fields and
    test_price_determination_report_extraction_succeeds_for_a_real_summary_line)."""
    httpx_mock.add_response(
        url=DETAIL_URL, json=_detail_payload([{"objId": "obj-4", "fileName": "Rapor.pdf", "fileExtension": "pdf"}])
    )
    pdf_bytes = build_pdf_bytes(text="belirlenen 76,60 TL")  # would match if extraction ran
    httpx_mock.add_response(url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id="obj-4"), content=_java_wrap(pdf_bytes))

    result = process_disclosure_documents(_disclosure(document_type="trading_start"), config=fast_config())

    assert result.pdf_status == "ok"
    assert result.extracted_facts is None


def test_no_fields_matched_produces_warning(httpx_mock, build_pdf_bytes):
    httpx_mock.add_response(
        url=DETAIL_URL, json=_detail_payload([{"objId": "obj-5", "fileName": "Izahname.pdf", "fileExtension": "pdf"}])
    )
    pdf_bytes = build_pdf_bytes(text="alakasiz bir metin, hicbir hedef alan yok burada")
    httpx_mock.add_response(url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id="obj-5"), content=_java_wrap(pdf_bytes))

    result = process_disclosure_documents(_disclosure(), config=fast_config())

    assert result.pdf_status == "ok"
    assert result.extracted_facts is not None
    assert all(f.status == "not_found" for f in result.extracted_facts.as_dict().values())
    assert "no target fields matched" in result.extraction_warnings[0]


# --------------------------------------------------------------------------
# aggregate_company_facts
# --------------------------------------------------------------------------

SRC_P = SourceRef("approved_prospectus", "disc-p", "url-p", 1)
SRC_A = SourceRef("investor_sale_announcement", "disc-a", "url-a", 2)


def test_aggregate_merges_facts_from_two_disclosures():
    prospectus_facts = build_extracted_facts({"capital_increase_shares": FieldObservation(2000.0, "p", SRC_P)}, None)
    announcement_facts = build_extracted_facts(None, {"offering_price": FieldObservation(76.6, "a", SRC_A)})

    d1 = _disclosure(disclosure_id="d1", document_type="approved_prospectus", extracted_facts=prospectus_facts)
    d2 = _disclosure(disclosure_id="d2", document_type="investor_sale_announcement", extracted_facts=announcement_facts)

    result = aggregate_company_facts([d1, d2])

    assert "ipo:QUICK:2026 / 7" in result
    combined = result["ipo:QUICK:2026 / 7"]
    assert combined.capital_increase_shares.value == 2000.0
    assert combined.offering_price.value == 76.6


def test_aggregate_detects_conflict_across_disclosures():
    facts1 = build_extracted_facts(None, {"offering_price": FieldObservation(76.6, "a1", SRC_A)})
    facts2 = build_extracted_facts(
        {"offering_price": FieldObservation(80.0, "p1", SRC_P)}, None
    )
    d1 = _disclosure(disclosure_id="d1", document_type="investor_sale_announcement", extracted_facts=facts1)
    d2 = _disclosure(disclosure_id="d2", document_type="approved_prospectus", extracted_facts=facts2)

    combined = aggregate_company_facts([d1, d2])["ipo:QUICK:2026 / 7"]
    assert combined.offering_price.status == "conflicting"


def test_aggregate_skips_unmatched_and_unprocessed_disclosures():
    unmatched = _disclosure(matched_spk_record_id=None)
    unprocessed = _disclosure(extracted_facts=None)

    assert aggregate_company_facts([unmatched, unprocessed]) == {}
