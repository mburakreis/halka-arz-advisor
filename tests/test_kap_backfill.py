from datetime import date

from halka_arz_advisor.kap.attachments import ATTACHMENT_DETAIL_URL_TEMPLATE, FILE_DOWNLOAD_URL_TEMPLATE
from halka_arz_advisor.kap.backfill import search_and_backfill
from halka_arz_advisor.kap.backfill_cache import BackfillCache
from halka_arz_advisor.kap.client import KAP_DISCLOSURE_LIST_URL, KapClient
from halka_arz_advisor.kap.pdf import PdfCache
from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.spk.models import SpkIpoRecord

DISCLOSURE_INDEX = 555001


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=1, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _ipo_record(**overrides) -> SpkIpoRecord:
    defaults = dict(
        ay=6, donem="2026 / 6", borsa_kodu="TESTX", sirket_unvani="Test Enerji A.Ş.",
        halka_arz_sekli=None, halka_arz_orani=None, halka_arz_fiyati_tl=None,
        ortak_satis_bin_tl=None, nakit_sermaye_artisi_bin_tl=None, ek_satis_tutari_bin_tl=None,
        satisa_hazir_bekletilen_pay_tutari_bin_tl=None, satisa_sunulan_toplam_tutar_bin_abd_dolari=None,
        satisa_sunulan_toplam_tutar_bin_tl=None, mevcut_sermaye_bin_tl=None, yeni_sermaye_bin_tl=None,
        satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl=None, ilk_islem_gordugu_pazar=None,
        halka_arza_aracilik_eden_kurum=None, borsada_islem_gorme_tarihi=None, raw={},
    )
    defaults.update(overrides)
    return SpkIpoRecord(**defaults)


def _raw_kap_item(*, disclosure_id: str, title: str, stock_code: str | None, published_at: str) -> dict:
    return {
        "disclosureBasic": {
            "disclosureId": disclosure_id,
            "disclosureIndex": DISCLOSURE_INDEX,
            "title": title,
            "publishDate": published_at,
            "companyTitle": "Test Enerji A.Ş.",
            "summary": "",
            "stockCode": stock_code,
        },
        "disclosureDetail": {},
    }


def _detail_payload(obj_id: str) -> list[dict]:
    return [
        {
            "disclosure": {"disclosureBasic": {"disclosureId": "d1", "disclosureIndex": DISCLOSURE_INDEX, "companyTitle": "X"}, "disclosureDetail": {}},
            "disclosureBody": [],
            "attachments": [{"objId": obj_id, "fileName": "Izahname.pdf", "fileExtension": "pdf"}],
        }
    ]


def test_missing_document_backfill_finds_and_caches_a_historical_prospectus(httpx_mock, build_pdf_bytes, tmp_path):
    ipo_record = _ipo_record()
    record_id = "ipo:TESTX:2026 / 6"

    httpx_mock.add_response(
        url=KAP_DISCLOSURE_LIST_URL,
        json=[_raw_kap_item(disclosure_id="d-hist", title="İzahname (SPK Tarafından Onaylanan)", stock_code="TESTX", published_at="10.03.2026 10:00:00")],
        is_reusable=True,
    )
    httpx_mock.add_response(url=ATTACHMENT_DETAIL_URL_TEMPLATE.format(index=DISCLOSURE_INDEX), json=_detail_payload("obj-hist"))
    httpx_mock.add_response(url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id="obj-hist"), content=build_pdf_bytes(text="halka arz ile ilgili metin"))

    cache = BackfillCache(tmp_path / "backfill")
    pdf_cache = PdfCache(tmp_path / "pdfs")

    with KapClient(fast_config()) as kap_client:
        outcome = search_and_backfill(
            record_id,
            ipo_record=ipo_record,
            application_record=None,
            current_disclosures=[],
            ipo_records=[ipo_record],
            application_records=[],
            cache=cache,
            kap_client=kap_client,
            pdf_cache=pdf_cache,
            config=fast_config(),
            reference_date=date(2026, 8, 7),
        )

    assert outcome.searched is True
    assert outcome.recovered_document_types == ("approved_prospectus",)
    assert len(outcome.disclosures) == 1
    assert outcome.disclosures[0].pdf_status == "ok"
    assert outcome.disclosures[0].matched_spk_record_id == record_id

    entry = cache.get(record_id)
    assert entry is not None
    assert len(entry.seeds) == 1
    # Every other supported type was genuinely searched for (within this
    # same window) and not found — recorded so a later run doesn't
    # re-search for them until the window itself grows.
    assert "investor_sale_announcement" in entry.exhausted_document_types
    assert "approved_prospectus" not in entry.exhausted_document_types


def test_already_backfilled_company_skips_a_repeat_historical_search(httpx_mock, build_pdf_bytes, tmp_path):
    """The second call must not hit the KAP disclosure-list endpoint at
    all — only re-materializing the already-found seed from a cheap
    local PDF-cache read plus the (always-live) attachment lookup."""
    ipo_record = _ipo_record()
    record_id = "ipo:TESTX:2026 / 6"

    httpx_mock.add_response(
        url=KAP_DISCLOSURE_LIST_URL,
        json=[_raw_kap_item(disclosure_id="d-hist", title="İzahname (SPK Tarafından Onaylanan)", stock_code="TESTX", published_at="10.03.2026 10:00:00")],
        is_reusable=True,
    )
    httpx_mock.add_response(url=ATTACHMENT_DETAIL_URL_TEMPLATE.format(index=DISCLOSURE_INDEX), json=_detail_payload("obj-hist"))
    httpx_mock.add_response(url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id="obj-hist"), content=build_pdf_bytes(text="halka arz ile ilgili metin"))

    cache = BackfillCache(tmp_path / "backfill")
    pdf_cache = PdfCache(tmp_path / "pdfs")

    with KapClient(fast_config()) as kap_client:
        first = search_and_backfill(
            record_id, ipo_record=ipo_record, application_record=None, current_disclosures=[],
            ipo_records=[ipo_record], application_records=[], cache=cache, kap_client=kap_client,
            pdf_cache=pdf_cache, config=fast_config(), reference_date=date(2026, 8, 7),
        )
    assert first.searched is True

    # Only the attachment-metadata call is expected again — no new
    # KAP_DISCLOSURE_LIST_URL mock is registered, so a real search
    # attempt here would fail the test with an unmatched request.
    httpx_mock.add_response(url=ATTACHMENT_DETAIL_URL_TEMPLATE.format(index=DISCLOSURE_INDEX), json=_detail_payload("obj-hist"))

    with KapClient(fast_config()) as kap_client:
        second = search_and_backfill(
            record_id, ipo_record=ipo_record, application_record=None, current_disclosures=[],
            ipo_records=[ipo_record], application_records=[], cache=cache, kap_client=kap_client,
            pdf_cache=pdf_cache, config=fast_config(), reference_date=date(2026, 8, 7),
        )

    assert second.searched is False
    assert len(second.disclosures) == 1
    assert second.disclosures[0].pdf_status == "ok"


def test_ambiguous_historical_match_is_not_attributed_to_either_company(httpx_mock, build_pdf_bytes, tmp_path):
    """Two SPK records share the same ticker (a genuinely ambiguous
    situation) — halka_arz_advisor.kap.matching.match_disclosure already
    refuses to guess, and backfill must respect that: the disclosure is
    found by the search but attributed to neither record."""
    record_a = _ipo_record(donem="2026 / 6")
    record_b = _ipo_record(donem="2026 / 7")
    record_id = "ipo:TESTX:2026 / 6"

    httpx_mock.add_response(
        url=KAP_DISCLOSURE_LIST_URL,
        json=[_raw_kap_item(disclosure_id="d-hist", title="İzahname (SPK Tarafından Onaylanan)", stock_code="TESTX", published_at="10.03.2026 10:00:00")],
        is_reusable=True,
    )

    cache = BackfillCache(tmp_path / "backfill")
    pdf_cache = PdfCache(tmp_path / "pdfs")

    with KapClient(fast_config()) as kap_client:
        outcome = search_and_backfill(
            record_id,
            ipo_record=record_a,
            application_record=None,
            current_disclosures=[],
            ipo_records=[record_a, record_b],
            application_records=[],
            cache=cache,
            kap_client=kap_client,
            pdf_cache=pdf_cache,
            config=fast_config(),
            reference_date=date(2026, 8, 7),
        )

    # No attachment/PDF mock registered at all — an ambiguous match must
    # never even attempt to process the disclosure's document.
    assert outcome.searched is True
    assert outcome.recovered_document_types == ()
    assert outcome.disclosures == ()

    entry = cache.get(record_id)
    assert entry is not None
    assert "approved_prospectus" in entry.exhausted_document_types
