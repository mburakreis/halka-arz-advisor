import json
from datetime import date

import httpx
import pytest

from halka_arz_advisor.kap.client import KAP_DISCLOSURE_LIST_URL, KapClient, fetch_disclosures_raw
from halka_arz_advisor.kap.exceptions import KapResponseError, KapSchemaError, KapTransportError
from halka_arz_advisor.probe.config import ProbeConfig


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=2, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def test_sends_expected_payload_shape(httpx_mock, fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, json=sample)

    fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config())

    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    body = json.loads(request.read())
    assert body["fromDate"] == "07.07.2026"
    assert body["toDate"] == "06.08.2026"
    assert "disclosureTypes" in body
    assert "memberTypes" in body
    assert "fundTypes" not in body  # the field that caused HTTP 500 in the wrong-shaped payload
    assert "disclosureType" not in body  # singular form is also wrong


def test_client_fetches_and_normalizes(httpx_mock, fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, json=sample)

    with KapClient(fast_config()) as client:
        disclosures = client.fetch_disclosures(date(2026, 7, 7), date(2026, 8, 6))

    assert len(disclosures) == len(sample)
    assert disclosures[0].document_type == "approved_prospectus"


def test_fetch_recent_disclosures_uses_today_and_days_window(httpx_mock, fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, json=sample)

    with KapClient(fast_config()) as client:
        disclosures = client.fetch_recent_disclosures(days=30)

    assert len(disclosures) == len(sample)


def test_rejects_non_json_content_type(httpx_mock):
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, text="<html></html>", headers={"content-type": "text/html"})

    with pytest.raises(KapResponseError, match="non-JSON content-type"):
        fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config())


def test_rejects_invalid_json(httpx_mock):
    httpx_mock.add_response(
        url=KAP_DISCLOSURE_LIST_URL, text="not json{", headers={"content-type": "application/json"}
    )

    with pytest.raises(KapResponseError, match="not valid JSON"):
        fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config())


def test_rejects_non_array_top_level(httpx_mock):
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, json={"success": False, "errorMessage": "HTTP 400 - "})

    with pytest.raises(KapSchemaError, match="top-level JSON array"):
        fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config())


def test_http_error_status_raises_response_error(httpx_mock):
    # 404 is not in retry_status_codes, so this needs only one registered
    # response — unlike KAP's real 429/5xx transient failures.
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, status_code=404, text="not found")

    with pytest.raises(KapResponseError, match="HTTP 404"):
        fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config())


def test_retries_on_5xx_then_succeeds(httpx_mock, fixture_json):
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, status_code=500)
    httpx_mock.add_response(url=KAP_DISCLOSURE_LIST_URL, json=fixture_json("kap_disclosures_sample.json"))

    data = fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config(max_retries=2))

    assert len(httpx_mock.get_requests()) == 2
    assert isinstance(data, list)


def test_transport_error_wrapped_after_retries_exhausted(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=KAP_DISCLOSURE_LIST_URL)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=KAP_DISCLOSURE_LIST_URL)

    with pytest.raises(KapTransportError) as exc_info:
        fetch_disclosures_raw(date(2026, 7, 7), date(2026, 8, 6), config=fast_config(max_retries=1))

    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
