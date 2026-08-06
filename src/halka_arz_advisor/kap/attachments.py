"""Fetch a KAP disclosure's official detail data and resolve its attachments.

**Endpoints used** (confirmed live against real IPO disclosures — see
:mod:`halka_arz_advisor.kap.client` for the base-endpoint research trail):

- ``GET https://www.kap.org.tr/tr/api/notification/attachment-detail/{disclosureIndex}``
  returns ``[{"disclosure": {...}, "disclosureBody": [...], "attachments": [{"objId", "fileName", "fileExtension"}, ...]}]``.
- ``GET https://www.kap.org.tr/tr/api/file/download/{objId}`` is the direct
  download URL for one attachment (see :mod:`halka_arz_advisor.kap.pdf`
  for what the response actually contains — it is **not** a raw PDF).

No third-party KAP package is used — just ``httpx`` via the shared
:mod:`halka_arz_advisor.probe.http_client` conventions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .exceptions import KapResponseError, KapSchemaError, KapTransportError
from .text import fold_turkish

ATTACHMENT_DETAIL_URL_TEMPLATE = "https://www.kap.org.tr/tr/api/notification/attachment-detail/{index}"
DISCLOSURE_PAGE_URL_TEMPLATE = "https://www.kap.org.tr/tr/Bildirim/{index}"
FILE_DOWNLOAD_URL_TEMPLATE = "https://www.kap.org.tr/tr/api/file/download/{obj_id}"

_EXTENSION_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Filename keyword -> document_role, checked in order. Anything left over
# is a "primary_candidate" — the substantive document itself, as opposed
# to a signature page, cover sheet, appendix, or a third party's review
# of a *different* attachment.
#
# Deliberately does NOT treat a leading "EK <n>" ("appendix <n>") prefix
# as a signal on its own — confirmed live that KAP numbers *every*
# document in a filing bundle this way, including the primary one (e.g.
# QUICK's real Fiyat Tespit Raporu is filed as "EK5-Quick Sigorta Fiyat
# Tespit Raporu.pdf", and MASFN's as "EK 5 - FTR_Parça2.pdf"). Only the
# specific content keywords below (articles of association, audit
# report, signature, cover, third-party review) are reliable signals.
_ROLE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("imza", "signature"),
    ("kapak", "cover_page"),
    ("analist", "analyst_review"),
    ("degerlendirme", "analyst_review"),
    ("esas sozlesme", "appendix"),
    ("denetim raporu", "appendix"),
)


@dataclass(frozen=True, slots=True)
class KapAttachment:
    name: str
    url: str
    content_type: str | None
    document_role: str
    obj_id: str


def _classify_attachment_role(file_name: str) -> str:
    folded = fold_turkish(file_name)
    for keyword, role in _ROLE_KEYWORDS:
        if keyword in folded:
            return role
    return "primary_candidate"


def fetch_disclosure_detail(
    disclosure_index: int,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Fetch the raw attachment-detail payload for one disclosure.

    Returns the single element of the response array (the endpoint
    always wraps its result in a one-item JSON array).
    """
    cfg = config or ProbeConfig()
    owns_client = client is None
    http_client = client or build_client(cfg)

    url = ATTACHMENT_DETAIL_URL_TEMPLATE.format(index=disclosure_index)
    referer = DISCLOSURE_PAGE_URL_TEMPLATE.format(index=disclosure_index)
    try:
        try:
            response = fetch_with_retry(
                http_client, url, cfg, headers={"Accept": "application/json", "Referer": referer}
            )
        except httpx.TransportError as exc:
            raise KapTransportError(
                f"transport failure fetching KAP disclosure detail from {url}: {exc}"
            ) from exc
    finally:
        if owns_client:
            http_client.close()

    if response.status_code >= 400:
        raise KapResponseError(
            f"KAP disclosure detail endpoint returned HTTP {response.status_code} for index "
            f"{disclosure_index}: {response.text[:500]!r}"
        )

    content_type = response.headers.get("content-type") or ""
    if "json" not in content_type.lower():
        raise KapResponseError(
            f"KAP disclosure detail endpoint returned non-JSON content-type {content_type!r} "
            f"for index {disclosure_index}"
        )

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise KapResponseError(
            f"KAP disclosure detail endpoint returned a body that is not valid JSON for index "
            f"{disclosure_index}: {exc}"
        ) from exc

    if not isinstance(data, list) or not data:
        raise KapSchemaError(
            f"expected a non-empty JSON array from the KAP disclosure detail endpoint for index "
            f"{disclosure_index}, got {data!r}"
        )
    if not isinstance(data[0], dict):
        raise KapSchemaError(
            f"expected the first element of the KAP disclosure detail response to be an object "
            f"for index {disclosure_index}, got {type(data[0]).__name__}"
        )

    return data[0]


def resolve_attachments(
    disclosure_index: int,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
) -> list[KapAttachment]:
    """Fetch the disclosure detail and normalize its attachment list."""
    detail = fetch_disclosure_detail(disclosure_index, config=config, client=client)
    raw_attachments = detail.get("attachments")
    if raw_attachments is None:
        return []
    if not isinstance(raw_attachments, list):
        raise KapSchemaError(
            f"expected 'attachments' to be a list for disclosure index {disclosure_index}, "
            f"got {type(raw_attachments).__name__}"
        )

    result: list[KapAttachment] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            raise KapSchemaError(
                f"expected an object in 'attachments' for disclosure index {disclosure_index}, "
                f"got {type(item).__name__}"
            )
        obj_id = item.get("objId")
        file_name = item.get("fileName")
        if not obj_id or not isinstance(obj_id, str):
            raise KapSchemaError(
                f"attachment for disclosure index {disclosure_index} is missing a string 'objId'"
            )
        if not file_name or not isinstance(file_name, str):
            raise KapSchemaError(
                f"attachment {obj_id} for disclosure index {disclosure_index} is missing a string 'fileName'"
            )
        extension = item.get("fileExtension")
        content_type = _EXTENSION_CONTENT_TYPES.get(extension.lower()) if isinstance(extension, str) else None

        result.append(
            KapAttachment(
                name=file_name,
                url=FILE_DOWNLOAD_URL_TEMPLATE.format(obj_id=obj_id),
                content_type=content_type,
                document_role=_classify_attachment_role(file_name),
                obj_id=obj_id,
            )
        )
    return result


def select_primary_attachment(attachments: list[KapAttachment]) -> KapAttachment | None:
    """Pick the substantive document out of a disclosure's attachments.

    Returns the first ``primary_candidate`` (not a signature page, cover
    sheet, appendix, or a third party's review), or ``None`` if the
    disclosure has no attachments *or* every attachment it has is one of
    those non-primary roles — a signature page is never returned "as if"
    it were the primary document just because nothing better exists.
    """
    for attachment in attachments:
        if attachment.document_role == "primary_candidate":
            return attachment
    return None
