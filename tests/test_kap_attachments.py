import httpx
import pytest

from halka_arz_advisor.kap.attachments import (
    ATTACHMENT_DETAIL_URL_TEMPLATE,
    KapAttachment,
    fetch_disclosure_detail,
    resolve_attachments,
    select_primary_attachment,
)
from halka_arz_advisor.kap.exceptions import KapResponseError, KapSchemaError, KapTransportError
from halka_arz_advisor.probe.config import ProbeConfig

DISCLOSURE_INDEX = 1641990
DETAIL_URL = ATTACHMENT_DETAIL_URL_TEMPLATE.format(index=DISCLOSURE_INDEX)


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=1, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _detail_payload(attachments: list[dict]) -> list[dict]:
    return [
        {
            "disclosure": {
                "disclosureBasic": {
                    "disclosureId": "disc-1",
                    "disclosureIndex": DISCLOSURE_INDEX,
                    "companyTitle": "CVK MADEN İŞLETMELERİ SANAYİ VE TİCARET A.Ş.",
                },
                "disclosureDetail": {},
            },
            "disclosureBody": ["<table></table>"],
            "attachments": attachments,
        }
    ]


# --------------------------------------------------------------------------
# fetch_disclosure_detail / resolve_attachments — real shape, mocked network
# --------------------------------------------------------------------------


def test_fetch_disclosure_detail_returns_first_element(httpx_mock):
    payload = _detail_payload([{"objId": "obj-1", "fileName": "SPK Onaylı Izahname.pdf", "fileExtension": "pdf"}])
    httpx_mock.add_response(url=DETAIL_URL, json=payload)

    detail = fetch_disclosure_detail(DISCLOSURE_INDEX, config=fast_config())

    assert detail["disclosure"]["disclosureBasic"]["disclosureIndex"] == DISCLOSURE_INDEX
    assert detail["attachments"][0]["fileName"] == "SPK Onaylı Izahname.pdf"


def test_fetch_disclosure_detail_sends_referer_header(httpx_mock):
    httpx_mock.add_response(url=DETAIL_URL, json=_detail_payload([]))

    fetch_disclosure_detail(DISCLOSURE_INDEX, config=fast_config())

    request = httpx_mock.get_requests()[0]
    assert request.headers["referer"] == f"https://www.kap.org.tr/tr/Bildirim/{DISCLOSURE_INDEX}"


def test_resolve_attachments_parses_real_shape(httpx_mock):
    payload = _detail_payload(
        [
            {"objId": "obj-1", "fileName": "SPK Onaylı Izahname (CVKMD).pdf", "fileExtension": "pdf"},
        ]
    )
    httpx_mock.add_response(url=DETAIL_URL, json=payload)

    attachments = resolve_attachments(DISCLOSURE_INDEX, config=fast_config())

    assert len(attachments) == 1
    assert attachments[0].name == "SPK Onaylı Izahname (CVKMD).pdf"
    assert attachments[0].obj_id == "obj-1"
    assert attachments[0].url == "https://www.kap.org.tr/tr/api/file/download/obj-1"
    assert attachments[0].content_type == "application/pdf"
    assert attachments[0].document_role == "primary_candidate"


def test_resolve_attachments_empty_when_no_attachments_key(httpx_mock):
    payload = [
        {
            "disclosure": {"disclosureBasic": {"disclosureId": "d", "disclosureIndex": DISCLOSURE_INDEX, "companyTitle": "X"}},
            "disclosureBody": [],
        }
    ]
    httpx_mock.add_response(url=DETAIL_URL, json=payload)

    assert resolve_attachments(DISCLOSURE_INDEX, config=fast_config()) == []


def test_resolve_attachments_raises_on_malformed_item(httpx_mock):
    payload = _detail_payload([{"fileName": "missing-obj-id.pdf", "fileExtension": "pdf"}])
    httpx_mock.add_response(url=DETAIL_URL, json=payload)

    with pytest.raises(KapSchemaError, match="objId"):
        resolve_attachments(DISCLOSURE_INDEX, config=fast_config())


def test_fetch_disclosure_detail_raises_on_http_error(httpx_mock):
    httpx_mock.add_response(url=DETAIL_URL, status_code=404, text="not found")

    with pytest.raises(KapResponseError, match="HTTP 404"):
        fetch_disclosure_detail(DISCLOSURE_INDEX, config=fast_config())


def test_fetch_disclosure_detail_raises_on_empty_array(httpx_mock):
    httpx_mock.add_response(url=DETAIL_URL, json=[])

    with pytest.raises(KapSchemaError, match="non-empty"):
        fetch_disclosure_detail(DISCLOSURE_INDEX, config=fast_config())


def test_fetch_disclosure_detail_wraps_transport_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=DETAIL_URL)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=DETAIL_URL)

    with pytest.raises(KapTransportError):
        fetch_disclosure_detail(DISCLOSURE_INDEX, config=fast_config(max_retries=1))


# --------------------------------------------------------------------------
# Attachment role classification (analyst-review / appendix exclusion)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "expected_role"),
    [
        ("SPK Onaylı Izahname (CVKMD).pdf", "primary_candidate"),
        ("SPK Onaylı İzahname Part1.pdf", "primary_candidate"),
        ("SPK Onaylı TSSD.pdf", "primary_candidate"),
        ("EK5-Quick Sigorta Fiyat Tespit Raporu.pdf", "primary_candidate"),  # real filename, real primary doc
        ("EK 5 - FTR_Parça2.pdf", "primary_candidate"),  # real filename, real primary doc
        ("EK 1 - ESAS SÖZLEŞME.pdf", "appendix"),
        ("Masfen Bağımsız Denetim Raporu 3.pdf", "appendix"),
        ("İmza Sirküleri.pdf", "signature"),
        ("Kapak Sayfası.pdf", "cover_page"),
        ("Fiyat Tespit Raporuna İlişkin Analist Raporu.pdf", "analyst_review"),
        ("Değerlendirme Raporu.pdf", "analyst_review"),
    ],
)
def test_classify_attachment_role(file_name, expected_role):
    from halka_arz_advisor.kap.attachments import _classify_attachment_role

    assert _classify_attachment_role(file_name) == expected_role


# --------------------------------------------------------------------------
# select_primary_attachment
# --------------------------------------------------------------------------


def _attachment(name: str, role: str) -> KapAttachment:
    return KapAttachment(name=name, url=f"https://example/{name}", content_type="application/pdf", document_role=role, obj_id=name)


def test_select_primary_attachment_picks_primary_candidate():
    attachments = [
        _attachment("cover.pdf", "cover_page"),
        _attachment("izahname.pdf", "primary_candidate"),
        _attachment("signature.pdf", "signature"),
    ]
    selected = select_primary_attachment(attachments)
    assert selected.name == "izahname.pdf"


def test_select_primary_attachment_none_when_no_attachments():
    assert select_primary_attachment([]) is None


def test_select_primary_attachment_none_when_only_non_primary_present():
    """A signature page or analyst review must never be mistaken for the
    primary IPO document."""
    attachments = [
        _attachment("signature.pdf", "signature"),
        _attachment("analyst-review.pdf", "analyst_review"),
        _attachment("appendix.pdf", "appendix"),
    ]
    assert select_primary_attachment(attachments) is None
