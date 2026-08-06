"""Fetch and parse SPK's OpenAPI document, and generically validate raw
records against whatever schema it discovers there.

The point of this module is to avoid hard-coding assumptions about the
IPO endpoint or its response fields: the path, base URL, and field list
are all read out of the OpenAPI document at runtime, not written down as
Python constants. Only the document's own URL
(``https://ws.spk.gov.tr/swagger/v2/swagger.json``, the one the official
Swagger UI itself references) is fixed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from urllib.parse import urljoin

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .exceptions import SpkDiscoveryError, SpkResponseError, SpkSchemaError, SpkTransportError

SPK_OPENAPI_URL = "https://ws.spk.gov.tr/swagger/v2/swagger.json"

# Keyword *phrases* used to flag an operation/schema as IPO-related,
# matched as consecutive whole tokens (see _tokenize/_contains_phrase)
# rather than as raw substrings. SPK's path/tag/schema names are
# PascalCase identifiers glued together with no separators (e.g.
# "IlkHalkaArzVerileri"), so a naive substring check on a short keyword
# like "ipo" produces false positives: "KurumsalYatirimciPortfoyBuyuklukleriBilgi"
# contains the literal substring "ipo" (".. yatirim-CI-PO-rtfoy ..") despite
# being a completely unrelated institutional-investor-portfolio endpoint —
# found via live discovery, not hypothetically.
IPO_KEYWORDS: tuple[str, ...] = ("ilk halka arz", "halka arz", "ipo")

_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")
_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(text: str) -> list[str]:
    """Split identifier-ish or human-readable text into lowercase word tokens.

    Handles both PascalCase identifiers (``IlkHalkaArzVerileri`` ->
    ``["ilk", "halka", "arz", "verileri"]``) and space-separated text
    (``"İlk Halka Arz Verileri"`` -> the same), so a keyword phrase like
    "halka arz" can be matched as consecutive whole tokens instead of a
    raw substring.
    """
    if not text:
        return []
    normalized = text.replace("İ", "i").replace("I", "i")
    tokens: list[str] = []
    for chunk in _NON_ALNUM_RE.split(normalized):
        if chunk:
            tokens.extend(_CASE_BOUNDARY_RE.split(chunk))
    return [t.lower() for t in tokens if t]


def _contains_phrase(tokens: list[str], phrase: str) -> bool:
    phrase_tokens = phrase.split()
    n = len(phrase_tokens)
    if n == 0 or n > len(tokens):
        return False
    return any(tokens[i : i + n] == phrase_tokens for i in range(len(tokens) - n + 1))

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

_JSON_TYPE_TO_PY: dict[str, tuple[type, ...]] = {
    "integer": (int,),
    "number": (int, float),
    "string": (str,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def fetch_openapi_document(
    config: ProbeConfig | None = None, *, client: httpx.Client | None = None
) -> tuple[dict, str]:
    """GET the SPK OpenAPI document and return ``(parsed_doc, document_url)``.

    Uses the same shared retry/timeout/User-Agent conventions as the rest
    of the project (:mod:`halka_arz_advisor.probe.http_client`).
    """
    cfg = config or ProbeConfig()
    owns_client = client is None
    http_client = client or build_client(cfg)
    try:
        try:
            response = fetch_with_retry(
                http_client, SPK_OPENAPI_URL, cfg, headers={"Accept": "application/json"}
            )
        except httpx.TransportError as exc:
            raise SpkTransportError(
                f"transport failure fetching OpenAPI document from {SPK_OPENAPI_URL}: {exc}"
            ) from exc
    finally:
        if owns_client:
            http_client.close()

    if response.status_code >= 400:
        raise SpkResponseError(f"OpenAPI document endpoint returned HTTP {response.status_code}")

    content_type = response.headers.get("content-type") or ""
    if "json" not in content_type.lower():
        raise SpkResponseError(
            f"OpenAPI document endpoint returned non-JSON content-type {content_type!r}"
        )

    try:
        doc = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise SpkResponseError(f"OpenAPI document is not valid JSON: {exc}") from exc

    if not isinstance(doc, dict) or "paths" not in doc:
        raise SpkSchemaError("OpenAPI document is missing a top-level 'paths' object")

    return doc, str(response.url)


# --------------------------------------------------------------------------
# Parsing: paths -> operations
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenApiParameter:
    name: str
    location: str  # OpenAPI's "in": query, path, header, cookie
    required: bool
    type: str | None
    format: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class OpenApiOperation:
    method: str  # "GET", "POST", ...
    path: str
    summary: str | None
    tags: tuple[str, ...]
    parameters: tuple[OpenApiParameter, ...]
    response_content_types: tuple[str, ...]
    response_schema_ref: str | None
    response_is_array: bool
    security: tuple[dict, ...]
    match_reasons: tuple[str, ...] = ()


def _extract_parameters(raw_params: list) -> tuple[OpenApiParameter, ...]:
    result = []
    for p in raw_params:
        if not isinstance(p, dict):
            continue
        schema = p.get("schema") if isinstance(p.get("schema"), dict) else {}
        result.append(
            OpenApiParameter(
                name=p.get("name", ""),
                location=p.get("in", ""),
                required=bool(p.get("required", False)),
                type=schema.get("type"),
                format=schema.get("format"),
                description=p.get("description"),
            )
        )
    return tuple(result)


def _extract_response_schema(responses: dict) -> tuple[str | None, bool, tuple[str, ...]]:
    if not isinstance(responses, dict):
        return None, False, ()

    success_response = None
    for status in sorted(k for k in responses if k.startswith("2")):
        success_response = responses[status]
        if status == "200":
            break
    if not isinstance(success_response, dict):
        return None, False, ()

    content = success_response.get("content")
    if not isinstance(content, dict):
        return None, False, ()

    content_types = tuple(content.keys())
    preferred_order = ("application/json", "text/json", "text/plain")
    chosen = None
    for ct in preferred_order:
        if ct in content and isinstance(content[ct], dict):
            chosen = content[ct]
            break
    if chosen is None:
        for value in content.values():
            if isinstance(value, dict):
                chosen = value
                break
    if chosen is None:
        return None, False, content_types

    schema = chosen.get("schema")
    if not isinstance(schema, dict):
        return None, False, content_types

    is_array = schema.get("type") == "array"
    if is_array:
        items = schema.get("items")
        ref = items.get("$ref") if isinstance(items, dict) else None
    else:
        ref = schema.get("$ref")
    return ref, is_array, content_types


def parse_openapi_document(doc: dict) -> list[OpenApiOperation]:
    """Turn every documented operation into an :class:`OpenApiOperation`.

    Does not filter or interpret anything — that's :func:`find_ipo_operations`.
    """
    if not isinstance(doc, dict) or "paths" not in doc:
        raise SpkSchemaError("OpenAPI document is missing a top-level 'paths' object")

    paths = doc.get("paths") or {}
    if not isinstance(paths, dict):
        raise SpkSchemaError("OpenAPI document's 'paths' is not an object")

    global_security = doc.get("security")
    operations: list[OpenApiOperation] = []

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            ref, is_array, content_types = _extract_response_schema(op.get("responses"))
            security = op.get("security", global_security) or []
            if not isinstance(security, list):
                security = []
            operations.append(
                OpenApiOperation(
                    method=method.upper(),
                    path=path,
                    summary=op.get("summary"),
                    tags=tuple(op.get("tags") or []),
                    parameters=_extract_parameters(op.get("parameters") or []),
                    response_content_types=content_types,
                    response_schema_ref=ref,
                    response_is_array=is_array,
                    security=tuple(s for s in security if isinstance(s, dict)),
                )
            )
    return operations


def _match_reasons(operation: OpenApiOperation) -> tuple[str, ...]:
    reasons: list[str] = []
    haystacks = {
        "path": operation.path,
        "summary": operation.summary or "",
        "tags": " ".join(operation.tags),
    }
    for label, text in haystacks.items():
        tokens = _tokenize(text)
        for kw in IPO_KEYWORDS:
            if _contains_phrase(tokens, kw):
                reasons.append(f"{label} contains '{kw}'")
                break

    if operation.response_schema_ref:
        schema_name = operation.response_schema_ref.rsplit("/", 1)[-1]
        tokens = _tokenize(schema_name)
        for kw in IPO_KEYWORDS:
            if _contains_phrase(tokens, kw):
                reasons.append(f"response schema name contains '{kw}'")
                break

    return tuple(reasons)


def find_ipo_operations(operations: list[OpenApiOperation]) -> list[OpenApiOperation]:
    """Return every operation whose path/summary/tags/response-schema name
    looks IPO-related. Deliberately returns a list, not a single result —
    callers must decide how to handle zero or multiple matches."""
    matched = []
    for op in operations:
        reasons = _match_reasons(op)
        if reasons:
            matched.append(replace(op, match_reasons=reasons))
    return matched


# --------------------------------------------------------------------------
# Base URL resolution
# --------------------------------------------------------------------------


def resolve_base_url(doc: dict, document_url: str) -> str:
    """Determine the API base URL per the OpenAPI 3.0 rules for ``servers``.

    If the document declares one or more servers, the first one's URL is
    used (resolved against ``document_url`` if it's relative — server
    URLs are allowed to be relative per the spec). If ``servers`` is
    absent or empty, the spec's default applies: a single server with
    URL ``"/"``, resolved against wherever the document itself was
    fetched from.
    """
    servers = doc.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        server_url = first.get("url") if isinstance(first, dict) else None
        if server_url:
            return urljoin(document_url, server_url).rstrip("/")
    return urljoin(document_url, "/").rstrip("/")


# --------------------------------------------------------------------------
# Component schema resolution
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaField:
    name: str
    type: str | None
    format: str | None
    nullable: bool
    required: bool
    description: str | None
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedSchema:
    name: str
    ref: str
    fields: tuple[SchemaField, ...]
    additional_properties_allowed: bool
    raw: dict


def resolve_schema(doc: dict, ref: str) -> ResolvedSchema:
    """Resolve a ``#/components/schemas/...`` reference into a :class:`ResolvedSchema`.

    Raises :class:`SpkDiscoveryError` if the reference is malformed or
    doesn't exist in the document — never invents a schema.
    """
    if not ref or not isinstance(ref, str) or not ref.startswith("#/"):
        raise SpkDiscoveryError(f"unsupported or missing schema reference: {ref!r}")

    parts = ref.lstrip("#/").split("/")
    node: object = doc
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise SpkDiscoveryError(f"referenced schema not found in OpenAPI document: {ref}")
        node = node[part]

    if not isinstance(node, dict):
        raise SpkDiscoveryError(f"referenced schema at {ref} is not an object")

    properties = node.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required_names = set(node.get("required") or [])

    fields = []
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        fields.append(
            SchemaField(
                name=prop_name,
                type=prop_schema.get("type"),
                format=prop_schema.get("format"),
                nullable=bool(prop_schema.get("nullable", False)),
                required=prop_name in required_names,
                description=prop_schema.get("description"),
                ref=prop_schema.get("$ref"),
            )
        )

    additional_properties = node.get("additionalProperties", True)
    additional_properties_allowed = additional_properties is not False

    return ResolvedSchema(
        name=parts[-1],
        ref=ref,
        fields=tuple(fields),
        additional_properties_allowed=additional_properties_allowed,
        raw=node,
    )


# --------------------------------------------------------------------------
# Generic, schema-driven record validation (soft: reports issues, doesn't raise)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaFieldIssue:
    record_index: int
    field_name: str
    issue: str
    detail: str


@dataclass(frozen=True, slots=True)
class SchemaValidationResult:
    ok: bool
    documented_fields: tuple[str, ...]
    observed_fields: frozenset
    undocumented_fields: frozenset
    fields_never_observed: frozenset
    issues: tuple[SchemaFieldIssue, ...]


def validate_records_against_schema(records: list, schema: ResolvedSchema) -> SchemaValidationResult:
    """Check ``records`` against ``schema`` field-by-field, without raising.

    This is deliberately observational (feeds the field-shape profiler
    and the CLI's "schema OK"/"schema issues" summary) rather than a hard
    gate — the hard gate (must be a JSON array of objects) already
    happened before this is called.
    """
    documented = {f.name: f for f in schema.fields}
    observed_fields: set[str] = set()
    issues: list[SchemaFieldIssue] = []

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append(
                SchemaFieldIssue(index, "<record>", "not_an_object", f"expected object, got {type(record).__name__}")
            )
            continue

        observed_fields.update(record.keys())

        undocumented_note = (
            "key not present in the OpenAPI schema"
            if not schema.additional_properties_allowed
            else "key not present in the OpenAPI schema (schema allows additionalProperties)"
        )
        for key in record.keys():
            if key not in documented:
                issues.append(SchemaFieldIssue(index, key, "undocumented_field", undocumented_note))

        for name, field_spec in documented.items():
            present = name in record
            if not present:
                issues.append(
                    SchemaFieldIssue(index, name, "missing_documented_field", "documented field absent from this record")
                )
                continue

            value = record[name]
            if value is None:
                if not field_spec.nullable:
                    issues.append(
                        SchemaFieldIssue(index, name, "unexpected_null", "field is null but schema does not mark it nullable")
                    )
                continue

            expected_types = _JSON_TYPE_TO_PY.get(field_spec.type) if field_spec.type else None
            if expected_types is None:
                continue
            is_bool = isinstance(value, bool)
            if is_bool and bool not in expected_types:
                issues.append(
                    SchemaFieldIssue(index, name, "type_mismatch", f"expected {field_spec.type}, got bool")
                )
            elif not is_bool and not isinstance(value, expected_types):
                issues.append(
                    SchemaFieldIssue(
                        index, name, "type_mismatch",
                        f"expected {field_spec.type}, got {type(value).__name__}: {value!r}",
                    )
                )

    fields_never_observed = frozenset(documented.keys()) - observed_fields
    undocumented_fields = observed_fields - set(documented.keys())

    return SchemaValidationResult(
        ok=not issues,
        documented_fields=tuple(documented.keys()),
        observed_fields=frozenset(observed_fields),
        undocumented_fields=undocumented_fields,
        fields_never_observed=fields_never_observed,
        issues=tuple(issues),
    )
