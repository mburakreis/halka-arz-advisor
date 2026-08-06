import pytest

from halka_arz_advisor.spk.exceptions import SpkDiscoveryError, SpkSchemaError
from halka_arz_advisor.spk.openapi import (
    find_ipo_operations,
    parse_openapi_document,
    resolve_base_url,
    resolve_schema,
    validate_records_against_schema,
)


def test_parses_all_documented_operations(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    operations = parse_openapi_document(doc)

    assert len(operations) == 2
    paths = {op.path for op in operations}
    assert paths == {"/BorclanmaAraclari/api/IlkHalkaArzVerileri", "/AKBankaFaaliyet/api/AraciKurumListe"}

    ipo_op = next(op for op in operations if "IlkHalkaArz" in op.path)
    assert ipo_op.method == "GET"
    assert ipo_op.summary == "İlk Halka Arz Verileri"
    assert ipo_op.tags == ("BorclanmaAraclari",)
    assert len(ipo_op.parameters) == 1
    assert ipo_op.parameters[0].name == "yil"
    assert ipo_op.parameters[0].type == "integer"
    assert ipo_op.response_is_array is True
    assert ipo_op.response_schema_ref == "#/components/schemas/IlkHalkaArzVerileriBilgi"
    assert set(ipo_op.response_content_types) == {"text/plain", "application/json", "text/json"}
    assert ipo_op.security == ()


def test_parse_rejects_document_without_paths():
    with pytest.raises(SpkSchemaError, match="paths"):
        parse_openapi_document({"openapi": "3.0.1"})


def test_find_ipo_operations_matches_only_the_ipo_endpoint(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    operations = parse_openapi_document(doc)
    matched = find_ipo_operations(operations)

    assert len(matched) == 1
    assert matched[0].path == "/BorclanmaAraclari/api/IlkHalkaArzVerileri"
    assert matched[0].match_reasons  # non-empty: something explains the match


def test_find_ipo_operations_does_not_false_positive_on_embedded_substring():
    """Regression test for a real false positive found during live discovery:
    the schema name 'KurumsalYatirimciPortfoyBuyuklukleriBilgi' contains the
    literal substring 'ipo' (inside '...yatirimciPortfoy...'), but is a
    completely unrelated institutional-investor-portfolio endpoint. Keyword
    matching must operate on tokenized words, not raw substrings."""
    from halka_arz_advisor.spk.openapi import OpenApiOperation, OpenApiParameter

    unrelated_op = OpenApiOperation(
        method="GET",
        path="/KurumsalYatirimciVerileri/api/GetPysPortfoyBuyuklukleri",
        summary=None,
        tags=("KurumsalYatirimciVerileri",),
        parameters=(
            OpenApiParameter("yil", "query", False, "integer", "int32", None),
            OpenApiParameter("ay", "query", False, "integer", "int32", None),
        ),
        response_content_types=("application/json",),
        response_schema_ref="#/components/schemas/KurumsalYatirimciPortfoyBuyuklukleriBilgi",
        response_is_array=True,
        security=(),
    )
    assert "ipo" in unrelated_op.response_schema_ref.lower()  # the raw-substring trap

    matched = find_ipo_operations([unrelated_op])
    assert matched == []


def test_resolve_schema_extracts_all_fields(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")

    assert schema.name == "IlkHalkaArzVerileriBilgi"
    assert schema.additional_properties_allowed is False
    assert len(schema.fields) == 19

    ay_field = next(f for f in schema.fields if f.name == "ay")
    assert ay_field.type == "integer"
    assert ay_field.nullable is False

    donem_field = next(f for f in schema.fields if f.name == "donem")
    assert donem_field.type == "string"
    assert donem_field.nullable is True

    date_field = next(f for f in schema.fields if f.name == "borsadaIslemGormeTarihi")
    assert date_field.type == "string"
    assert date_field.format == "date-time"


def test_resolve_schema_raises_for_missing_reference(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    with pytest.raises(SpkDiscoveryError, match="not found"):
        resolve_schema(doc, "#/components/schemas/DoesNotExist")


def test_resolve_schema_raises_for_malformed_reference(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    with pytest.raises(SpkDiscoveryError, match="unsupported or missing"):
        resolve_schema(doc, "not-a-ref")


def test_resolve_base_url_with_no_servers_defaults_to_document_host(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    assert "servers" not in doc
    base_url = resolve_base_url(doc, "https://ws.spk.gov.tr/swagger/v2/swagger.json")
    assert base_url == "https://ws.spk.gov.tr"


def test_resolve_base_url_uses_explicit_server_when_present():
    doc = {"servers": [{"url": "https://example.test/api"}]}
    base_url = resolve_base_url(doc, "https://example.test/swagger/v2/swagger.json")
    assert base_url == "https://example.test/api"


def test_resolve_base_url_resolves_relative_server_url():
    doc = {"servers": [{"url": "/v3"}]}
    base_url = resolve_base_url(doc, "https://example.test/swagger/v2/swagger.json")
    assert base_url == "https://example.test/v3"


def test_validate_records_flags_undocumented_field(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")

    records = [{"ay": 1, "surpriseNewField": "value"}]
    result = validate_records_against_schema(records, schema)

    assert result.ok is False
    assert "surpriseNewField" in result.undocumented_fields
    assert any(i.issue == "undocumented_field" and i.field_name == "surpriseNewField" for i in result.issues)


def test_validate_records_flags_missing_optional_field(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")

    records = [{"ay": 1}]  # every other documented field simply absent
    result = validate_records_against_schema(records, schema)

    assert result.ok is False
    assert "donem" in result.fields_never_observed
    assert any(i.issue == "missing_documented_field" and i.field_name == "donem" for i in result.issues)


def test_validate_records_accepts_null_for_nullable_field(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")

    records = [{"ay": 1, "donem": None}]
    result = validate_records_against_schema(records, schema)

    assert not any(i.field_name == "donem" for i in result.issues)


def test_validate_records_flags_null_for_non_nullable_field(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")

    records = [{"ay": None}]
    result = validate_records_against_schema(records, schema)

    assert result.ok is False
    assert any(i.issue == "unexpected_null" and i.field_name == "ay" for i in result.issues)


def test_validate_records_flags_type_mismatch(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")

    records = [{"ay": "not-a-number"}]
    result = validate_records_against_schema(records, schema)

    assert result.ok is False
    assert any(i.issue == "type_mismatch" and i.field_name == "ay" for i in result.issues)


def test_validate_records_ok_for_fully_matching_record(fixture_json):
    doc = fixture_json("spk_openapi_sample.json")
    schema = resolve_schema(doc, "#/components/schemas/IlkHalkaArzVerileriBilgi")
    sample = fixture_json("spk_ipo_2024_sample.json")

    result = validate_records_against_schema(sample, schema)

    assert result.ok is True
    assert result.issues == ()
