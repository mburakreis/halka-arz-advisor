import httpx
import pytest

from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.spk.client import BASE_URL, IPO_ENDPOINT_PATH, SpkApiClient
from halka_arz_advisor.spk.exceptions import SpkResponseError, SpkSchemaError, SpkTransportError

FULL_URL = f"{BASE_URL}{IPO_ENDPOINT_PATH}"


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=2, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def test_get_initial_public_offerings_parses_sample(httpx_mock, fixture_json):
    sample = fixture_json("spk_ipo_2024_sample.json")
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=sample)

    with SpkApiClient(fast_config()) as client:
        records = client.get_initial_public_offerings(2024)

    assert len(records) == 2
    assert records[0].borsa_kodu == "PATEK"
    assert records[1].borsa_kodu == "BORSK"
    assert records[0].raw == sample[0]


def test_sends_yil_query_param_and_accept_header(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=fixture_json("spk_ipo_2024_sample.json"))

    with SpkApiClient(fast_config()) as client:
        client.get_initial_public_offerings(2024)

    request = httpx_mock.get_requests()[0]
    assert request.url.params["yil"] == "2024"
    assert request.headers["accept"] == "application/json"


def test_empty_year_returns_empty_list(httpx_mock):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=1990", json=[])

    with SpkApiClient(fast_config()) as client:
        records = client.get_initial_public_offerings(1990)

    assert records == []


def test_rejects_non_json_content_type(httpx_mock):
    httpx_mock.add_response(
        url=f"{FULL_URL}?yil=2024", text="<html>nope</html>", headers={"content-type": "text/html"}
    )

    with SpkApiClient(fast_config()) as client:
        with pytest.raises(SpkResponseError, match="non-JSON content-type"):
            client.get_initial_public_offerings(2024)


def test_rejects_invalid_json_body(httpx_mock):
    httpx_mock.add_response(
        url=f"{FULL_URL}?yil=2024", text="not json{", headers={"content-type": "application/json"}
    )

    with SpkApiClient(fast_config()) as client:
        with pytest.raises(SpkResponseError, match="not valid JSON"):
            client.get_initial_public_offerings(2024)


def test_rejects_non_array_top_level(httpx_mock):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json={"unexpected": "object"})

    with SpkApiClient(fast_config()) as client:
        with pytest.raises(SpkSchemaError, match="top-level JSON array"):
            client.get_initial_public_offerings(2024)


def test_rejects_non_object_array_items(httpx_mock):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=["not", "objects"])

    with SpkApiClient(fast_config()) as client:
        with pytest.raises(SpkSchemaError, match="expected a JSON object at index"):
            client.get_initial_public_offerings(2024)


def test_malformed_record_raises_schema_error_not_silently_zeroed(httpx_mock):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=[{"ay": "not-a-number"}])

    with SpkApiClient(fast_config()) as client:
        with pytest.raises(SpkSchemaError, match="expected int for 'ay'"):
            client.get_initial_public_offerings(2024)


def test_http_error_status_raises_response_error(httpx_mock):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", status_code=404, text="not found")

    with SpkApiClient(fast_config()) as client:
        with pytest.raises(SpkResponseError, match="HTTP 404"):
            client.get_initial_public_offerings(2024)


def test_retries_on_500_then_succeeds(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", status_code=500)
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", status_code=500)
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=fixture_json("spk_ipo_2024_sample.json"))

    with SpkApiClient(fast_config(max_retries=2)) as client:
        records = client.get_initial_public_offerings(2024)

    assert len(records) == 2
    assert len(httpx_mock.get_requests()) == 3


def test_transport_error_wrapped_after_retries_exhausted(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=f"{FULL_URL}?yil=2024")
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=f"{FULL_URL}?yil=2024")

    with SpkApiClient(fast_config(max_retries=1)) as client:
        with pytest.raises(SpkTransportError) as exc_info:
            client.get_initial_public_offerings(2024)

    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_fetch_ipo_raw_preserves_raw_json_before_normalization(httpx_mock, fixture_json):
    sample = fixture_json("spk_ipo_2024_sample.json")
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=sample)

    with SpkApiClient(fast_config()) as client:
        raw = client.fetch_ipo_raw(2024)

    assert raw.year == 2024
    assert raw.http_status == 200
    assert raw.content_type is not None and "json" in raw.content_type.lower()
    assert raw.raw_json == sample
