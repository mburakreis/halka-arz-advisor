import pytest

from halka_arz_advisor.decision.engine import DecisionResult, FeatureContribution, HardRuleResult
from halka_arz_advisor.gemini.exceptions import GeminiOutputError
from halka_arz_advisor.gemini.grounding import validate_grounding
from halka_arz_advisor.gemini.schema import AnalysisOutput


def _decision_result(**overrides) -> DecisionResult:
    defaults = dict(
        signal="participate",
        total_score=70.0,
        confidence_score=75.0,
        category_scores=(),
        # capital_increase_ratio (real catalog title "Sermaye artırım
        # oranı") is the only precomputed positive contribution here.
        feature_contributions=(
            FeatureContribution("valuation", "capital_increase_ratio", "AVAILABLE", 80.0, 90.0, 40.0, True),
        ),
        confidence_components=(),
        hard_rules=(
            HardRuleResult(
                "unresolved_critical_conflict",
                "insufficient_data",
                True,
                "conflicting values for critical field(s): offering_price",
            ),
        ),
        warnings=(),
        evidence_references=(),
        rule_version="expert_v0",
        weight_set_version="expert_v0",
    )
    defaults.update(overrides)
    return DecisionResult(**defaults)


def _output(**overrides) -> AnalysisOutput:
    defaults = dict(
        company_summary="Şirket özeti.",
        offering_summary="Halka arz özeti.",
        use_of_proceeds_summary="Fon kullanım özeti.",
        key_risks=(),
        positive_factors=(),
        negative_factors=(),
        missing_information=(),
        data_conflicts=(),
        decision_explanation="Katıl sinyaline dayanan karar motoru açıklaması.",
        source_references=(),
    )
    defaults.update(overrides)
    return AnalysisOutput(**defaults)


def test_grounded_positive_and_negative_factors_pass():
    output = _output(
        positive_factors=("Sermaye artırım oranının yüksek olması olumlu.",),
        negative_factors=("unresolved_critical_conflict kuralı tetiklendi.",),
    )
    validate_grounding(output, _decision_result())  # must not raise


def test_positive_factor_absent_from_precomputed_contributions_is_rejected():
    output = _output(positive_factors=("Şirketin marka bilinirliği çok yüksek.",))
    with pytest.raises(GeminiOutputError, match="positive_factors"):
        validate_grounding(output, _decision_result())


def test_negative_factor_absent_from_contributions_and_hard_rules_is_rejected():
    output = _output(negative_factors=("Yönetim kurulu deneyimsiz görünüyor.",))
    with pytest.raises(GeminiOutputError, match="negative_factors"):
        validate_grounding(output, _decision_result())


def test_decision_explanation_must_reference_the_given_signal():
    output = _output(decision_explanation="Bu şirket hakkında genel bir değerlendirme yapılmıştır.")
    with pytest.raises(GeminiOutputError, match="decision_explanation"):
        validate_grounding(output, _decision_result())


def test_no_precomputed_contributions_means_factors_must_be_empty():
    empty_result = _decision_result(feature_contributions=(), hard_rules=())
    output = _output(positive_factors=("Herhangi bir olumlu faktör.",))
    with pytest.raises(GeminiOutputError, match="positive_factors"):
        validate_grounding(output, empty_result)
