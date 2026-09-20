"""Golden numerical tests for the public statistical Tool Action interface."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from pydantic import ValidationError

from tabular_analytics_agent.statistics import (
    AlternativeHypothesis,
    AssumptionStatus,
    StatisticalAnalysisError,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
    analyze,
    parameter_guide,
)


def test_insufficient_group_size_reports_the_readable_group_value() -> None:
    frame = pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0, 5.0], "group": ["A", "B", "A", "B", "A"]})

    with pytest.raises(
        StatisticalAnalysisError, match="group 'B' of 'group' has only 2 usable values"
    ):
        analyze(
            frame,
            StatisticalRequest(
                operation=StatisticalOperation.T_TEST,
                value_field="value",
                group_field="group",
                group_order=("A", "B"),
            ),
        )


def test_parameter_guide_names_operation_specific_parameters() -> None:
    assert "t_test requires: value_field, group_field, group_order" in parameter_guide(
        StatisticalOperation.T_TEST
    )
    assert "positive_class" in parameter_guide(StatisticalOperation.LOGISTIC_REGRESSION)
    assert "alternative must stay two-sided" in parameter_guide(StatisticalOperation.ANOVA)


def analysis_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "value": [10, 11, 9, 10, 20, 21, 19, 22, 30, 29, 31, 32],
            "group": ["A"] * 4 + ["B"] * 4 + ["C"] * 4,
            "x": list(range(1, 13)),
            "y": [2.1, 3.8, 6.2, 7.9, 10.2, 11.8, 14.1, 16.2, 17.8, 20.1, 22.2, 23.9],
            "category": ["low"] * 4 + ["mid"] * 4 + ["high"] * 4,
            "segment": ["one", "one", "two", "two"] * 3,
            "outcome": [0, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1],
            "with_missing": [1.0, 2.0, None, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        }
    )


def estimates(result: StatisticalResult) -> dict[str, float]:
    return {item.metric: item.value for item in result.estimates}


def test_descriptive_statistics_and_confidence_interval_account_for_missing_rows() -> None:
    frame = analysis_frame()
    descriptive = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.DESCRIPTIVE,
            value_fields=("value", "with_missing"),
        ),
    )
    interval = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CONFIDENCE_INTERVAL,
            value_field="with_missing",
        ),
    )

    values = estimates(descriptive)
    assert values["value.mean"] == pytest.approx(20.3333333333)
    assert values["value.median"] == pytest.approx(20.5)
    assert descriptive.sample_size == 11
    assert descriptive.missing_row_count == 1
    assert interval.sample_size == 11
    assert interval.confidence_interval is not None
    assert interval.confidence_interval.lower < values["with_missing.mean"]
    assert interval.confidence_interval.upper > values["with_missing.mean"]
    assert interval.assumptions[0].name == "normality"


def test_correlation_reports_association_significance_effect_and_multiple_testing() -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION,
            x_field="x",
            y_field="y",
            multiple_testing_count=2,
        ),
    )

    values = estimates(result)
    assert values["pearson_r"] > 0.99
    assert values["spearman_rho"] > 0.99
    assert result.adjusted_alpha == pytest.approx(0.025)
    assert result.statistically_significant is True
    assert result.practically_significant is True
    assert any("does not establish causation" in warning for warning in result.warnings)
    assert any("Bonferroni" in warning for warning in result.warnings)


def test_correlation_honors_one_sided_alternative_hypotheses() -> None:
    frame = pd.DataFrame(
        {
            "x": [1, 2, 3, 4, 5, 6],
            "y": [12, 10, 9, 7, 5, 4],
        }
    )

    less = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION,
            x_field="x",
            y_field="y",
            alternative=AlternativeHypothesis.LESS,
        ),
    )
    greater = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION,
            x_field="x",
            y_field="y",
            alternative=AlternativeHypothesis.GREATER,
        ),
    )

    assert less.p_value is not None and less.p_value < 0.01
    assert greater.p_value is not None and greater.p_value > 0.99


@pytest.mark.parametrize(
    ("operation", "effect_metric", "statistic_name"),
    [
        (StatisticalOperation.T_TEST, "cohen_d", "welch_t"),
        (StatisticalOperation.MANN_WHITNEY, "rank_biserial", "mann_whitney_u"),
    ],
)
def test_two_group_tests_report_group_sizes_and_effects(
    operation: StatisticalOperation,
    effect_metric: str,
    statistic_name: str,
) -> None:
    frame = analysis_frame().query("group in ['A', 'B']")
    result = analyze(
        frame,
        StatisticalRequest(
            operation=operation,
            value_field="value",
            group_field="group",
            group_order=("A", "B"),
        ),
    )

    assert result.group_sizes == {'str:"A"': 4, 'str:"B"': 4}
    assert result.statistic_name == statistic_name
    assert result.effect_size is not None
    assert result.effect_size.metric == effect_metric
    assert result.statistically_significant is True
    assert set(estimates(result)) == {
        f'group[str:"A"].{"mean" if operation is StatisticalOperation.T_TEST else "median"}',
        f'group[str:"B"].{"mean" if operation is StatisticalOperation.T_TEST else "median"}',
    }


@pytest.mark.parametrize(
    "operation", [StatisticalOperation.MANN_WHITNEY, StatisticalOperation.KRUSKAL_WALLIS]
)
def test_rank_tests_check_that_group_spreads_are_similar(operation: StatisticalOperation) -> None:
    def spread_check(values: list[float]) -> tuple[AssumptionStatus, str]:
        frame = pd.DataFrame({"value": values, "group": ["A"] * 6 + ["B"] * 6})
        result = analyze(
            frame,
            StatisticalRequest(
                operation=operation,
                value_field="value",
                group_field="group",
                group_order=("A", "B") if operation is StatisticalOperation.MANN_WHITNEY else (),
            ),
        )
        check = next(check for check in result.assumptions if check.name == "similar_spread")
        return check.status, check.message

    similar = spread_check([10, 11, 9, 10, 12, 8, 20, 21, 19, 22, 18, 20])
    different = spread_check([10, 10.1, 9.9, 10, 10.05, 9.95, 1, 30, 5, 40, 2, 50])
    # Every value sits exactly one unit from its group median, so spread cannot be compared.
    undefined = spread_check([1, 1, 1, 3, 3, 3, 5, 5, 5, 7, 7, 7])

    assert similar[0] is AssumptionStatus.PASSED
    assert different[0] is AssumptionStatus.FAILED
    assert "rather than medians" in different[1]
    assert undefined[0] is AssumptionStatus.NOT_CHECKED


def test_chi_square_reports_expected_count_assumption_and_cramers_v() -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(
            operation=StatisticalOperation.CHI_SQUARE,
            x_field="category",
            y_field="segment",
        ),
    )

    assert result.statistic_name == "chi_square"
    assert result.effect_size is not None
    assert result.effect_size.metric == "cramers_v"
    assert result.assumptions[0].name == "expected_cell_counts"
    assert result.assumptions[0].status is AssumptionStatus.FAILED
    assert any("does not establish causation" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("operation", "effect_metric", "statistic_name"),
    [
        (StatisticalOperation.ANOVA, "eta_squared", "anova_f"),
        (StatisticalOperation.KRUSKAL_WALLIS, "epsilon_squared", "kruskal_h"),
    ],
)
def test_multi_group_tests_report_effect_size(
    operation: StatisticalOperation,
    effect_metric: str,
    statistic_name: str,
) -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(
            operation=operation,
            value_field="value",
            group_field="group",
        ),
    )

    assert result.group_sizes == {'str:"A"': 4, 'str:"B"': 4, 'str:"C"': 4}
    assert result.statistic_name == statistic_name
    assert result.effect_size is not None
    assert result.effect_size.metric == effect_metric
    assert result.statistically_significant is True
    if operation is StatisticalOperation.ANOVA:
        assert any(check.name == "equal_variance" for check in result.assumptions)


def test_linear_regression_reports_slope_fit_and_noncausal_warning() -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(
            operation=StatisticalOperation.LINEAR_REGRESSION,
            x_field="x",
            y_field="y",
        ),
    )

    values = estimates(result)
    assert values["slope"] == pytest.approx(2.0, abs=0.05)
    assert values["r_squared"] > 0.99
    assert result.effect_size is not None
    assert result.effect_size.metric == "r_squared"
    assert {check.name for check in result.assumptions} == {
        "linearity",
        "homoscedasticity",
        "residual_normality",
    }
    assert any("does not establish causation" in warning for warning in result.warnings)


def test_logistic_regression_reports_odds_ratio_and_binary_outcome_assumption() -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(
            operation=StatisticalOperation.LOGISTIC_REGRESSION,
            x_field="x",
            y_field="outcome",
            positive_class=1,
        ),
    )

    values = estimates(result)
    assert math.isfinite(values["coefficient"])
    assert values["odds_ratio"] > 0
    assert result.group_sizes == {"int:0": 5, "int:1": 7}
    assumptions = {check.name: check for check in result.assumptions}
    assert assumptions["binary_outcome_and_convergence"].status is AssumptionStatus.PASSED
    assert assumptions["linearity_of_logit"].status is AssumptionStatus.NOT_CHECKED
    assert assumptions["sparse_outcome"].status is AssumptionStatus.FAILED
    assert assumptions["separation"].status is AssumptionStatus.PASSED
    assert result.parameters["positive_class"] == 1


def test_logistic_group_sizes_use_collision_safe_typed_labels() -> None:
    frame = pd.DataFrame(
        {
            "x": [1, 2, 3, 4, 5, 6, 7, 8],
            "outcome": [1, "1", 1, "1", 1, "1", 1, "1"],
        }
    )

    result = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.LOGISTIC_REGRESSION,
            x_field="x",
            y_field="outcome",
            positive_class="1",
        ),
    )

    assert result.group_sizes == {"int:1": 4, 'str:"1"': 4}


def test_request_and_result_contracts_reject_incomplete_inputs() -> None:
    with pytest.raises(ValidationError, match="requires: x_field, y_field"):
        StatisticalRequest(operation=StatisticalOperation.CORRELATION)
    with pytest.raises(ValidationError, match="must be unique"):
        StatisticalRequest(
            operation=StatisticalOperation.DESCRIPTIVE,
            value_fields=("value", "value"),
        )
    with pytest.raises(ValidationError, match="requires: positive_class"):
        StatisticalRequest(
            operation=StatisticalOperation.LOGISTIC_REGRESSION,
            x_field="x",
            y_field="outcome",
        )
    with pytest.raises(ValidationError, match="only a two-sided alternative"):
        StatisticalRequest(
            operation=StatisticalOperation.CHI_SQUARE,
            x_field="category",
            y_field="segment",
            alternative=AlternativeHypothesis.GREATER,
        )


def test_result_contract_rejects_inconsistent_significance_status() -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION,
            x_field="x",
            y_field="y",
        ),
    )
    payload = result.model_dump(mode="json")
    payload["statistically_significant"] = not result.statistically_significant

    with pytest.raises(ValidationError, match="must match p-value"):
        StatisticalResult.model_validate(payload)


@pytest.mark.parametrize("missing", ["statistic_name", "statistic"])
def test_p_value_requires_its_test_identity(missing: str) -> None:
    result = analyze(
        analysis_frame(),
        StatisticalRequest(operation=StatisticalOperation.CORRELATION, x_field="x", y_field="y"),
    )
    payload = result.model_dump()
    payload[missing] = None
    with pytest.raises(ValidationError, match="identified test statistic"):
        StatisticalResult.model_validate(payload)


def test_mixed_type_group_labels_do_not_collide() -> None:
    frame = pd.DataFrame(
        {
            "value": [1, 2, 3, 10, 11, 12],
            "group": [1, 1, 1, "1", "1", "1"],
        }
    )

    result = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.T_TEST,
            value_field="value",
            group_field="group",
            group_order=(1, "1"),
        ),
    )

    assert sorted(result.group_sizes.values()) == [3, 3]
    assert set(result.group_sizes) == {"int:1", 'str:"1"'}


def test_one_sided_group_order_is_explicit_and_independent_of_row_order() -> None:
    frame = pd.DataFrame(
        {
            "value": [1, 2, 3, 10, 11, 12],
            "group": ["A", "A", "A", "B", "B", "B"],
        }
    )
    request = StatisticalRequest(
        operation=StatisticalOperation.T_TEST,
        value_field="value",
        group_field="group",
        group_order=("A", "B"),
        alternative=AlternativeHypothesis.LESS,
    )

    ordered = analyze(frame, request)
    reversed_rows = analyze(frame.iloc[::-1], request)

    assert ordered.parameters["group_order"] == ["A", "B"]
    assert ordered.statistic == pytest.approx(reversed_rows.statistic)
    assert ordered.p_value == pytest.approx(reversed_rows.p_value)
    assert ordered.p_value is not None and ordered.p_value < 0.01


@pytest.mark.parametrize(
    ("frame", "analysis_request", "message"),
    [
        (
            analysis_frame(),
            StatisticalRequest(
                operation=StatisticalOperation.CONFIDENCE_INTERVAL,
                value_field="unknown",
            ),
            "unknown statistical fields",
        ),
        (
            pd.DataFrame({"value": [1, "bad", 3]}),
            StatisticalRequest(
                operation=StatisticalOperation.DESCRIPTIVE,
                value_fields=("value",),
            ),
            "non-numeric",
        ),
        (
            pd.DataFrame({"value": [1, 2]}),
            StatisticalRequest(
                operation=StatisticalOperation.CONFIDENCE_INTERVAL,
                value_field="value",
            ),
            "at least 3",
        ),
        (
            analysis_frame(),
            StatisticalRequest(
                operation=StatisticalOperation.T_TEST,
                value_field="value",
                group_field="group",
                group_order=("A", "B"),
            ),
            "exactly two groups",
        ),
        (
            analysis_frame().assign(outcome="same"),
            StatisticalRequest(
                operation=StatisticalOperation.LOGISTIC_REGRESSION,
                x_field="x",
                y_field="outcome",
                positive_class="same",
            ),
            "binary outcome",
        ),
    ],
)
def test_invalid_statistical_inputs_fail_safely(
    frame: pd.DataFrame,
    analysis_request: StatisticalRequest,
    message: str,
) -> None:
    with pytest.raises(StatisticalAnalysisError, match=message):
        analyze(frame, analysis_request)


def test_degenerate_parametric_test_fails_with_safe_statistical_error() -> None:
    frame = pd.DataFrame(
        {
            "value": [1, 1, 1, 1, 1, 1],
            "group": ["A", "A", "A", "B", "B", "B"],
        }
    )

    with pytest.raises(StatisticalAnalysisError, match="requires variability"):
        analyze(
            frame,
            StatisticalRequest(
                operation=StatisticalOperation.T_TEST,
                value_field="value",
                group_field="group",
                group_order=("A", "B"),
            ),
        )


def test_perfect_linear_fit_fails_instead_of_reporting_invalid_inference() -> None:
    frame = pd.DataFrame({"x": [1, 2, 3, 4], "y": [2, 4, 6, 8]})

    with pytest.raises(StatisticalAnalysisError, match="perfect deterministic fit"):
        analyze(
            frame,
            StatisticalRequest(
                operation=StatisticalOperation.LINEAR_REGRESSION,
                x_field="x",
                y_field="y",
            ),
        )


# Rank tests check similar spread instead, and that check carries no group name.
@pytest.mark.parametrize("operation", [StatisticalOperation.ANOVA, StatisticalOperation.T_TEST])
def test_assumption_names_read_the_group_value_not_its_encoding(
    operation: StatisticalOperation,
) -> None:
    """A caveat names its group the way the data spells it, in any script.

    Group labels are encoded with their type so values of different types cannot collide. That
    encoding is storage, not prose: shown raw it turns 'Quan 1' into an escape sequence, which is
    unreadable in every language that is not plain ASCII.
    """
    groups = ["Quan 1", "Thu Duc", "Go Vap"]
    frame = pd.DataFrame(
        {
            "value": [3.0, 4.0, 5.0, 6.5, 2.0, 2.5, 3.5, 4.0, 7.0, 7.5, 8.0, 9.5],
            "group": [groups[index // 4] for index in range(12)],
        }
    )
    request = StatisticalRequest(
        operation=operation,
        value_field="value",
        group_field="group",
        group_order=("Quan 1", "Thu Duc") if operation is StatisticalOperation.T_TEST else (),
    )
    if operation is StatisticalOperation.T_TEST:
        frame = frame[frame["group"] != "Go Vap"]

    result = analyze(frame, request)

    normality = [check.name for check in result.assumptions if check.name.startswith("normality")]
    expected = groups[:2] if operation is StatisticalOperation.T_TEST else groups
    assert sorted(normality) == sorted(f"normality:{group}" for group in expected)
    assert not any("str:" in name for name in normality)
