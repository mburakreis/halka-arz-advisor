import httpx
import pytest

from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.spk.exceptions import SpkDiscoveryError, SpkResponseError, SpkSchemaError, SpkTransportError
from halka_arz_advisor.spk.ipo_client import SpkIpoApiClient
from halka_arz_advisor.spk.openapi import (
    OpenApiOperation,
    OpenApiParameter,
    SPK_OPENAPI_URL,
    resolve_schema,
)

FULL_URL = "https://ws.spk.gov.tr/BorclanmaAraclari/api/IlkHalkaArzVerileri"


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=2, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _client_from_fixture(fixture_json, **config_overrides) -> SpkIpoApiClient:
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")
    operation = OpenApiOperation(
        method="GET",
        path="/BorclanmaAraclari/api/IlkHalkaArzVerileri",
        summary="İlk Halka Arz Verileri",
        tags=("BorclanmaAraclari",),
        parameters=(OpenApiParameter("yil", "query", False, "integer", "int32", "Yil (yyyy)"),),
        response_content_types=("application/json",),
        response_schema_ref="#/components/schemas/IlkHalkaArzVerileriBilgi",
        response_is_array=True,
        security=(),
    )
    return SpkIpoApiClient(
        base_url="https://ws.spk.gov.tr", operation=operation, schema=schema, config=fast_config(**config_overrides)
    )


# --------------------------------------------------------------------------
# discover()
# --------------------------------------------------------------------------


def test_discover_finds_endpoint_and_resolves_schema(httpx_mock, fixture_json):
    httpx_mock.add_response(url=SPK_OPENAPI_URL, json=fixture_json("spk_openapi_sample.json"))

    with SpkIpoApiClient.discover(fast_config()) as client:
        assert client.base_url == "https://ws.spk.gov.tr"
        assert client.operation.path == "/BorclanmaAraclari/api/IlkHalkaArzVerileri"
        assert client.schema.name == "IlkHalkaArzVerileriBilgi"
        assert len(client.schema.fields) == 19


def test_discover_saves_raw_openapi_document(httpx_mock, fixture_json, tmp_path):
    doc = fixture_json("spk_openapi_sample.json")
    httpx_mock.add_response(url=SPK_OPENAPI_URL, json=doc)

    dest = tmp_path / "openapi-raw"
    with SpkIpoApiClient.discover(fast_config(), save_raw_openapi_to=dest) as client:
        pass

    assert (dest / "swagger.json").exists()
    assert (dest / "meta.json").exists()
    import json

    saved = json.loads((dest / "swagger.json").read_text())
    assert saved["paths"].keys() == doc["paths"].keys()


def test_discover_raises_when_no_ipo_operation_matches(httpx_mock):
    doc = {
        "openapi": "3.0.1",
        "paths": {
            "/AKBankaFaaliyet/api/AraciKurumListe": {
                "get": {
                    "tags": ["AKBankaFaaliyet"],
                    "summary": "Tüm Aracı Kurum Listesi",
                    "responses": {"200": {"content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/X"}}}}}},
                }
            }
        },
        "components": {"schemas": {}},
    }
    httpx_mock.add_response(url=SPK_OPENAPI_URL, json=doc)

    with pytest.raises(SpkDiscoveryError, match="no documented GET operation"):
        SpkIpoApiClient.discover(fast_config())


def test_discover_raises_when_multiple_candidates_found(httpx_mock):
    def ipo_op(path: str) -> dict:
        return {
            "get": {
                "tags": ["BorclanmaAraclari"],
                "summary": "İlk Halka Arz Verileri",
                "parameters": [{"name": "yil", "in": "query", "schema": {"type": "integer"}}],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"$ref": "#/components/schemas/IlkHalkaArzVerileriBilgi"}}
                            }
                        }
                    }
                },
            }
        }

    doc = {
        "openapi": "3.0.1",
        "paths": {
            "/BorclanmaAraclari/api/IlkHalkaArzVerileri": ipo_op("/BorclanmaAraclari/api/IlkHalkaArzVerileri"),
            "/BorclanmaAraclari/api/IlkHalkaArzVerileriV2": ipo_op("/BorclanmaAraclari/api/IlkHalkaArzVerileriV2"),
        },
        "components": {"schemas": {"IlkHalkaArzVerileriBilgi": {"type": "object", "properties": {}}}},
    }
    httpx_mock.add_response(url=SPK_OPENAPI_URL, json=doc)

    with pytest.raises(SpkDiscoveryError, match="multiple candidate"):
        SpkIpoApiClient.discover(fast_config())


def test_discover_raises_when_matched_operation_has_no_schema_ref(httpx_mock):
    doc = {
        "openapi": "3.0.1",
        "paths": {
            "/BorclanmaAraclari/api/IlkHalkaArzVerileri": {
                "get": {
                    "summary": "İlk Halka Arz Verileri",
                    "parameters": [{"name": "yil", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "Success"}},
                }
            }
        },
        "components": {"schemas": {}},
    }
    httpx_mock.add_response(url=SPK_OPENAPI_URL, json=doc)

    with pytest.raises(SpkDiscoveryError, match="no response schema reference"):
        SpkIpoApiClient.discover(fast_config())


# --------------------------------------------------------------------------
# fetch()
# --------------------------------------------------------------------------


def test_fetch_returns_records_and_ok_schema_validation(httpx_mock, fixture_json):
    sample = fixture_json("spk_ipo_2024_sample.json")
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=sample)

    with _client_from_fixture(fixture_json) as client:
        result = client.fetch(2024)

    assert result.http_status == 200
    assert result.record_count == 2
    assert result.raw_json == sample
    assert result.schema_validation.ok is True
    assert result.is_empty is False


def test_fetch_valid_empty_array(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=1990", json=[])

    with _client_from_fixture(fixture_json) as client:
        result = client.fetch(1990)

    assert result.record_count == 0
    assert result.is_empty is True
    assert result.raw_json == []


def test_fetch_rejects_html_content_type(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", text="<html></html>", headers={"content-type": "text/html"})

    with _client_from_fixture(fixture_json) as client:
        with pytest.raises(SpkResponseError, match="non-JSON content-type"):
            client.fetch(2024)


def test_fetch_rejects_invalid_json(httpx_mock, fixture_json):
    httpx_mock.add_response(
        url=f"{FULL_URL}?yil=2024", text="{not valid", headers={"content-type": "application/json"}
    )

    with _client_from_fixture(fixture_json) as client:
        with pytest.raises(SpkResponseError, match="not valid JSON"):
            client.fetch(2024)


def test_fetch_rejects_top_level_object(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json={"not": "an array"})

    with _client_from_fixture(fixture_json) as client:
        with pytest.raises(SpkSchemaError, match="top-level JSON array"):
            client.fetch(2024)


def test_fetch_http_error_status_raises_response_error(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", status_code=404, text="not found")

    with _client_from_fixture(fixture_json) as client:
        with pytest.raises(SpkResponseError, match="HTTP 404"):
            client.fetch(2024)


def test_fetch_retries_on_5xx_then_succeeds(httpx_mock, fixture_json):
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", status_code=500)
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", status_code=503)
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=fixture_json("spk_ipo_2024_sample.json"))

    with _client_from_fixture(fixture_json, max_retries=2) as client:
        result = client.fetch(2024)

    assert result.record_count == 2
    assert len(httpx_mock.get_requests()) == 3


def test_fetch_wraps_transport_error_after_retries_exhausted(httpx_mock, fixture_json):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=f"{FULL_URL}?yil=2024")
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=f"{FULL_URL}?yil=2024")

    with _client_from_fixture(fixture_json, max_retries=1) as client:
        with pytest.raises(SpkTransportError) as exc_info:
            client.fetch(2024)

    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_fetch_undocumented_key_produces_schema_issue_without_raising(httpx_mock, fixture_json):
    records = [{"ay": 1, "unexpectedField": "surprise"}]
    httpx_mock.add_response(url=f"{FULL_URL}?yil=2024", json=records)

    with _client_from_fixture(fixture_json) as client:
        result = client.fetch(2024)

    assert result.record_count == 1
    assert result.schema_validation.ok is False
    assert "unexpectedField" in result.schema_validation.undocumented_fields
