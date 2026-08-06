"""JSON schema for the Gemini structured-output analysis, and Python-side
validation of the model's response against it.

Validation deliberately does more than the JSON Schema alone can express
via Gemini's ``response_json_schema`` parameter: it also cross-checks
every ``source_references`` entry against the exact ``(disclosure_id,
page_number)`` pairs that were actually included in the prompt's
context — the model constraining its *shape* to the schema doesn't stop
it from citing a page or disclosure it was never shown, so that's
checked here and rejected if found.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import GeminiOutputError

SCHEMA_VERSION = "1"

PARTICIPATION_SIGNAL_VALUES: tuple[str, ...] = (
    "participate",
    "limited_participation",
    "skip",
    "insufficient_data",
)

ANALYSIS_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "company_summary": {"type": "string"},
        "offering_summary": {"type": "string"},
        "use_of_proceeds_summary": {"type": "string"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "positive_factors": {"type": "array", "items": {"type": "string"}},
        "negative_factors": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "data_conflicts": {"type": "array", "items": {"type": "string"}},
        "participation_signal": {"type": "string", "enum": list(PARTICIPATION_SIGNAL_VALUES)},
        "participation_rationale": {"type": "string"},
        "confidence": {"type": "number"},
        "source_references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "disclosure_id": {"type": "string"},
                    "page_number": {"type": "integer"},
                },
                "required": ["disclosure_id", "page_number"],
            },
        },
    },
    "required": [
        "company_summary",
        "offering_summary",
        "use_of_proceeds_summary",
        "key_risks",
        "positive_factors",
        "negative_factors",
        "missing_information",
        "data_conflicts",
        "participation_signal",
        "participation_rationale",
        "confidence",
        "source_references",
    ],
}

_REQUIRED_KEYS = tuple(ANALYSIS_JSON_SCHEMA["required"])
_STRING_FIELDS = ("company_summary", "offering_summary", "use_of_proceeds_summary", "participation_rationale")
_LIST_OF_STRING_FIELDS = (
    "key_risks",
    "positive_factors",
    "negative_factors",
    "missing_information",
    "data_conflicts",
)


@dataclass(frozen=True, slots=True)
class SourceReference:
    disclosure_id: str
    page_number: int


@dataclass(frozen=True, slots=True)
class AnalysisOutput:
    company_summary: str
    offering_summary: str
    use_of_proceeds_summary: str
    key_risks: tuple[str, ...]
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    missing_information: tuple[str, ...]
    data_conflicts: tuple[str, ...]
    participation_signal: str
    participation_rationale: str
    confidence: float
    source_references: tuple[SourceReference, ...]

    def as_dict(self) -> dict:
        return {
            "company_summary": self.company_summary,
            "offering_summary": self.offering_summary,
            "use_of_proceeds_summary": self.use_of_proceeds_summary,
            "key_risks": list(self.key_risks),
            "positive_factors": list(self.positive_factors),
            "negative_factors": list(self.negative_factors),
            "missing_information": list(self.missing_information),
            "data_conflicts": list(self.data_conflicts),
            "participation_signal": self.participation_signal,
            "participation_rationale": self.participation_rationale,
            "confidence": self.confidence,
            "source_references": [
                {"disclosure_id": r.disclosure_id, "page_number": r.page_number} for r in self.source_references
            ],
        }


def validate_analysis_output(data: object, *, allowed_references: set[tuple[str, int]]) -> AnalysisOutput:
    """Validate a parsed JSON object against the analysis schema.

    Raises :class:`~halka_arz_advisor.gemini.exceptions.GeminiOutputError`
    — with a specific, actionable reason — for any structural problem: a
    non-object top level, a missing or wrong-typed field, an
    out-of-enum ``participation_signal``, a ``confidence`` outside
    ``[0, 1]``, or a ``source_references`` entry naming a
    ``(disclosure_id, page_number)`` pair that wasn't part of
    ``allowed_references`` — i.e. wasn't actually sent to the model.
    """
    if not isinstance(data, dict):
        raise GeminiOutputError(f"expected a JSON object, got {type(data).__name__}")

    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise GeminiOutputError(f"response is missing required field(s): {missing}")

    for field_name in _STRING_FIELDS:
        if not isinstance(data[field_name], str):
            raise GeminiOutputError(
                f"field '{field_name}' must be a string, got {type(data[field_name]).__name__}"
            )

    for field_name in _LIST_OF_STRING_FIELDS:
        value = data[field_name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise GeminiOutputError(f"field '{field_name}' must be a list of strings")

    signal = data["participation_signal"]
    if signal not in PARTICIPATION_SIGNAL_VALUES:
        raise GeminiOutputError(
            f"field 'participation_signal' must be one of {PARTICIPATION_SIGNAL_VALUES}, got {signal!r}"
        )

    confidence = data["confidence"]
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not (0.0 <= float(confidence) <= 1.0)
    ):
        raise GeminiOutputError(f"field 'confidence' must be a number between 0 and 1, got {confidence!r}")

    raw_references = data["source_references"]
    if not isinstance(raw_references, list):
        raise GeminiOutputError("field 'source_references' must be a list")

    references: list[SourceReference] = []
    for i, item in enumerate(raw_references):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("disclosure_id"), str)
            or not isinstance(item.get("page_number"), int)
            or isinstance(item.get("page_number"), bool)
        ):
            raise GeminiOutputError(
                f"source_references[{i}] must be an object with a string 'disclosure_id' "
                "and integer 'page_number'"
            )
        disclosure_id = item["disclosure_id"]
        page_number = item["page_number"]
        if (disclosure_id, page_number) not in allowed_references:
            raise GeminiOutputError(
                f"source_references[{i}] cites disclosure_id={disclosure_id!r} page={page_number}, "
                "which was not part of the supplied context — rejecting invented citation"
            )
        references.append(SourceReference(disclosure_id=disclosure_id, page_number=page_number))

    return AnalysisOutput(
        company_summary=data["company_summary"],
        offering_summary=data["offering_summary"],
        use_of_proceeds_summary=data["use_of_proceeds_summary"],
        key_risks=tuple(data["key_risks"]),
        positive_factors=tuple(data["positive_factors"]),
        negative_factors=tuple(data["negative_factors"]),
        missing_information=tuple(data["missing_information"]),
        data_conflicts=tuple(data["data_conflicts"]),
        participation_signal=signal,
        participation_rationale=data["participation_rationale"],
        confidence=float(confidence),
        source_references=tuple(references),
    )
