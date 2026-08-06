import pytest

from halka_arz_advisor.ollama.exceptions import OllamaOutputError
from halka_arz_advisor.ollama.schema import validate_analysis_output

ALLOWED = {("d1", 1), ("d1", 2)}


def valid_payload(**overrides) -> dict:
    payload = {
        "company_summary": "Şirket özeti.",
        "offering_summary": "Halka arz özeti.",
        "use_of_proceeds_summary": "Fon kullanım özeti.",
        "key_risks": ["Risk 1"],
        "positive_factors": ["Olumlu 1"],
        "negative_factors": ["Olumsuz 1"],
        "missing_information": [],
        "data_conflicts": [],
        "participation_signal": "participate",
        "participation_rationale": "Gerekçe.",
        "confidence": 0.7,
        "source_references": [{"disclosure_id": "d1", "page_number": 1}],
    }
    payload.update(overrides)
    return payload


def test_valid_payload_round_trips():
    output = validate_analysis_output(valid_payload(), allowed_references=ALLOWED)
    assert output.participation_signal == "participate"
    assert output.confidence == 0.7
    assert output.source_references[0].disclosure_id == "d1"
    assert output.source_references[0].page_number == 1
    # as_dict() round-trips back to the original shape
    assert output.as_dict()["source_references"] == [{"disclosure_id": "d1", "page_number": 1}]


def test_rejects_invalid_participation_signal():
    with pytest.raises(OllamaOutputError, match="participation_signal"):
        validate_analysis_output(valid_payload(participation_signal="buy_now"), allowed_references=ALLOWED)


def test_rejects_source_reference_not_in_allowed_set():
    """A disclosure_id/page_number the model was never shown must be rejected
    even though it's shape-valid — this is the invented-citation guard."""
    payload = valid_payload(source_references=[{"disclosure_id": "d2", "page_number": 99}])
    with pytest.raises(OllamaOutputError, match="not part of the supplied context"):
        validate_analysis_output(payload, allowed_references=ALLOWED)


def test_rejects_missing_required_field():
    payload = valid_payload()
    del payload["confidence"]
    with pytest.raises(OllamaOutputError, match="missing required field"):
        validate_analysis_output(payload, allowed_references=ALLOWED)


def test_rejects_confidence_out_of_range():
    with pytest.raises(OllamaOutputError, match="confidence"):
        validate_analysis_output(valid_payload(confidence=1.5), allowed_references=ALLOWED)


def test_rejects_non_object_top_level():
    with pytest.raises(OllamaOutputError, match="JSON object"):
        validate_analysis_output(["not", "an", "object"], allowed_references=ALLOWED)
