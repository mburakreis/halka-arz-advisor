from halka_arz_advisor.decision.catalog import FEATURE_CATALOG, get_feature
from halka_arz_advisor.decision.models import CATEGORIES

_VALID_NAMESPACES = (
    "kap_extraction",
    "spk_ipo_record",
    "spk_application",
    "kap_document",
    "financial_series",
    "derived_financial",
    "market_data",
    "kap_sector",
)


def test_no_duplicate_feature_ids_and_valid_categories():
    ids = [spec.feature_id for spec in FEATURE_CATALOG]
    assert len(ids) == len(set(ids))
    assert all(spec.category in CATEGORIES for spec in FEATURE_CATALOG)


def test_derivation_dependencies_reference_real_feature_ids_with_no_cycles():
    by_id = {spec.feature_id: spec for spec in FEATURE_CATALOG}

    def _walk(feature_id: str, seen: frozenset[str]) -> None:
        assert feature_id not in seen, f"cycle detected involving {feature_id!r}"
        for dep_id in by_id[feature_id].derivation_dependencies:
            assert dep_id in by_id, f"{feature_id} depends on unknown feature_id {dep_id!r}"
            _walk(dep_id, seen | {feature_id})

    for spec in FEATURE_CATALOG:
        _walk(spec.feature_id, frozenset())


def test_required_source_fields_use_a_recognized_namespace():
    for spec in FEATURE_CATALOG:
        for field_ref in spec.required_source_fields:
            assert field_ref.split(".", 1)[0] in _VALID_NAMESPACES


def test_get_feature_returns_the_matching_spec():
    assert get_feature("offering_price").category == "valuation"
