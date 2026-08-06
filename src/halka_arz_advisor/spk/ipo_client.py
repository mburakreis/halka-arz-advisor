"""Discovery-driven client for SPK's documented IPO data endpoint.

Unlike :mod:`halka_arz_advisor.spk.client` (which hard-codes the base
URL/path it validated by hand in an earlier phase), this client is built
by calling :meth:`SpkIpoApiClient.discover`, which reads the endpoint
path, base URL, and response schema straight out of the live OpenAPI
document via :mod:`halka_arz_advisor.spk.openapi`. It deliberately
returns raw ``dict`` records rather than a business-domain dataclass —
that comes later, once the discovered schema has been reviewed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .exceptions import SpkDiscoveryError, SpkResponseError, SpkSchemaError, SpkTransportError
from .openapi import (
    OpenApiOperation,
    ResolvedSchema,
    SchemaValidationResult,
    fetch_openapi_document,
    find_ipo_operations,
    parse_openapi_document,
    resolve_base_url,
    resolve_schema,
    validate_records_against_schema,
)

YEAR_PARAM_NAME = "yil"


@dataclass(slots=True)
class SpkIpoApiResult:
    """One fetch's outcome: the untouched raw JSON plus a soft schema report.

    ``raw_json`` is exactly what ``response.json()`` returned — nothing
    is dropped, coerced, or renamed before this is constructed.
    """

    year: int
    requested_url: str
    http_status: int
    content_type: str | None
    elapsed_ms: float
    raw_json: list[dict]
    record_count: int
    schema_validation: SchemaValidationResult

    @property
    def is_empty(self) -> bool:
        return self.record_count == 0


class SpkIpoApiClient:
    """Client bound to one already-discovered IPO operation.

    Construct via :meth:`discover` rather than directly, so the
    base URL/path/schema always come from the OpenAPI document.
    """

    def __init__(
        self,
        *,
        base_url: str,
        operation: OpenApiOperation,
        schema: ResolvedSchema,
        config: ProbeConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.operation = operation
        self.schema = schema
        self._config = config or ProbeConfig()
        self._owns_client = client is None
        self._client = client or build_client(self._config)

    def __enter__(self) -> "SpkIpoApiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @property
    def endpoint_url(self) -> str:
        return f"{self.base_url}{self.operation.path}"

    @classmethod
    def discover(
        cls,
        config: ProbeConfig | None = None,
        *,
        client: httpx.Client | None = None,
        save_raw_openapi_to: Path | None = None,
    ) -> "SpkIpoApiClient":
        """Fetch the OpenAPI document, find the one documented GET IPO
        operation with a ``yil`` parameter, and resolve its response schema.

        Raises :class:`SpkDiscoveryError` if zero or more than one such
        operation is found, or its response schema can't be resolved —
        this never silently guesses which endpoint to use.
        """
        cfg = config or ProbeConfig()
        owns_client = client is None
        http_client = client or build_client(cfg)

        doc, document_url = fetch_openapi_document(cfg, client=http_client)

        if save_raw_openapi_to is not None:
            save_raw_openapi_to.mkdir(parents=True, exist_ok=True)
            (save_raw_openapi_to / "swagger.json").write_text(
                json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (save_raw_openapi_to / "meta.json").write_text(
                json.dumps({"document_url": document_url, "source_url": document_url}, indent=2),
                encoding="utf-8",
            )

        operations = parse_openapi_document(doc)
        ipo_candidates = find_ipo_operations(operations)
        get_candidates = [
            op
            for op in ipo_candidates
            if op.method == "GET" and any(p.name == YEAR_PARAM_NAME for p in op.parameters)
        ]

        if not get_candidates:
            if owns_client:
                http_client.close()
            raise SpkDiscoveryError(
                "no documented GET operation with a "
                f"'{YEAR_PARAM_NAME}' parameter matched IPO-related keywords; "
                f"{len(ipo_candidates)} IPO-keyword match(es) found in total: "
                f"{[f'{op.method} {op.path}' for op in ipo_candidates]}"
            )
        if len(get_candidates) > 1:
            if owns_client:
                http_client.close()
            raise SpkDiscoveryError(
                "multiple candidate GET IPO operations with a "
                f"'{YEAR_PARAM_NAME}' parameter were found; refusing to guess which one to use: "
                f"{[f'{op.method} {op.path}' for op in get_candidates]}"
            )

        operation = get_candidates[0]
        if not operation.response_schema_ref:
            if owns_client:
                http_client.close()
            raise SpkDiscoveryError(
                f"matched IPO operation {operation.method} {operation.path} has no response schema reference"
            )

        schema = resolve_schema(doc, operation.response_schema_ref)
        base_url = resolve_base_url(doc, document_url)

        instance = cls(base_url=base_url, operation=operation, schema=schema, config=cfg, client=http_client)
        # discover() (not __init__) is responsible for the client's lifecycle
        # when it built one itself, so transfer ownership onto the instance.
        instance._owns_client = owns_client
        return instance

    def fetch(self, year: int) -> SpkIpoApiResult:
        """Fetch and shape-check one year's IPO records.

        Raises :class:`SpkTransportError` (network failure after retries),
        :class:`SpkResponseError` (bad HTTP status, non-JSON content-type,
        unparsable JSON body), or :class:`SpkSchemaError` (top-level shape
        isn't a JSON array of objects). A schema that *parses* but doesn't
        match the documented fields is reported via ``schema_validation``
        on the successful result rather than raised — see
        :func:`halka_arz_advisor.spk.openapi.validate_records_against_schema`.
        """
        start = time.monotonic()
        try:
            response = fetch_with_retry(
                self._client,
                self.endpoint_url,
                self._config,
                params={YEAR_PARAM_NAME: year},
                headers={"Accept": "application/json"},
            )
        except httpx.TransportError as exc:
            raise SpkTransportError(
                f"transport failure fetching SPK IPO data for year {year} from {self.endpoint_url}: {exc}"
            ) from exc
        elapsed_ms = (time.monotonic() - start) * 1000

        if response.status_code >= 400:
            raise SpkResponseError(
                f"SPK IPO endpoint returned HTTP {response.status_code} for year {year}: "
                f"{response.text[:500]!r}"
            )

        content_type = response.headers.get("content-type")
        if not content_type or "json" not in content_type.lower():
            raise SpkResponseError(
                f"SPK IPO endpoint returned non-JSON content-type {content_type!r} for year {year}"
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpkResponseError(
                f"SPK IPO endpoint returned a body that is not valid JSON for year {year}: {exc}"
            ) from exc

        if not isinstance(payload, list):
            raise SpkSchemaError(
                f"expected a top-level JSON array from the SPK IPO endpoint for year {year}, "
                f"got {type(payload).__name__}"
            )
        for i, item in enumerate(payload):
            if not isinstance(item, dict):
                raise SpkSchemaError(
                    f"expected a JSON object at index {i} of the SPK IPO array for year {year}, "
                    f"got {type(item).__name__}"
                )

        validation = validate_records_against_schema(payload, self.schema)

        return SpkIpoApiResult(
            year=year,
            requested_url=str(response.url),
            http_status=response.status_code,
            content_type=content_type,
            elapsed_ms=elapsed_ms,
            raw_json=payload,
            record_count=len(payload),
            schema_validation=validation,
        )
