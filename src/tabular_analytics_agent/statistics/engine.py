"""Deterministic statistical operations over validated tabular inputs."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import stats

from tabular_analytics_agent.statistics.errors import (
    InsufficientSampleError,
    StatisticalAnalysisError,
)
from tabular_analytics_agent.statistics.models import (
    AssumptionCheck,
    AssumptionStatus,
    ConfidenceInterval,
    StatisticalEstimate,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
)

_MIN_SAMPLE_SIZE = 3
_PRACTICAL_THRESHOLDS = {
    "pearson_r": 0.1,
    "cohen_d": 0.2,
    "rank_biserial": 0.1,
    "cramers_v": 0.1,
    "eta_squared": 0.01,
    "epsilon_squared": 0.01,
    "r_squared": 0.02,
    "odds_ratio_log": math.log(1.2),
}


def analyze(frame: pd.DataFrame, request: StatisticalRequest) -> StatisticalResult:
    """Run one allowlisted statistical operation without model-generated calculations."""
    _require_columns(frame, request.source_fields)
    operations: dict[
        StatisticalOperation,
        Callable[[pd.DataFrame, StatisticalRequest], dict[str, Any]],
    ] = {
        StatisticalOperation.DESCRIPTIVE: _descriptive,
        StatisticalOperation.CORRELATION: _correlation,
        StatisticalOperation.CONFIDENCE_INTERVAL: _confidence_interval,
        StatisticalOperation.T_TEST: _t_test,
        StatisticalOperation.MANN_WHITNEY: _mann_whitney,
        StatisticalOperation.CHI_SQUARE: _chi_square,
        StatisticalOperation.ANOVA: _anova,
        StatisticalOperation.KRUSKAL_WALLIS: _kruskal_wallis,
        StatisticalOperation.LINEAR_REGRESSION: _linear_regression,
        StatisticalOperation.LOGISTIC_REGRESSION: _logistic_regression,
    }
    try:
        payload = operations[request.operation](frame, request)
    except StatisticalAnalysisError:
        raise
    except (ArithmeticError, ValueError) as exc:
        raise StatisticalAnalysisError(
            f"{request.operation.value} could not be computed for the supplied data"
        ) from exc
    warnings = list(payload.pop("warnings", ()))
    if request.multiple_testing_count > 1:
        warnings.append(
            "Bonferroni correction applied because multiple statistical tests were requested."
        )
    return StatisticalResult(
        result_id=uuid4(),
        operation=request.operation,
        source_fields=request.source_fields,
        parameters=request.reproducible_parameters(),
        warnings=tuple(warnings),
        **payload,
    )


def _descriptive(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    estimates: list[StatisticalEstimate] = []
    sample_sizes: list[int] = []
    for field in request.value_fields:
        values = _numeric(frame[field], field)
        clean = np.asarray(values.dropna().to_numpy(), dtype=np.float64)
        _require_sample(clean, f"field {field!r}")
        sample_sizes.append(len(clean))
        metrics = {
            "count": float(len(clean)),
            "mean": float(np.mean(clean)),
            "standard_deviation": float(np.std(clean, ddof=1)),
            "minimum": float(np.min(clean)),
            "first_quartile": float(np.quantile(clean, 0.25)),
            "median": float(np.median(clean)),
            "third_quartile": float(np.quantile(clean, 0.75)),
            "maximum": float(np.max(clean)),
        }
        estimates.extend(
            StatisticalEstimate(metric=f"{field}.{name}", value=value)
            for name, value in metrics.items()
        )
    return {
        "sample_size": min(sample_sizes),
        "missing_row_count": int(frame[list(request.value_fields)].isna().any(axis=1).sum()),
        "missing_data_handling": "Available-case analysis per numeric field.",
        "estimates": tuple(estimates),
    }


def _confidence_interval(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    field = _required(request.value_field)
    clean, missing = _one_numeric(frame, field)
    mean = float(np.mean(clean))
    standard_error = float(stats.sem(clean))
    critical = float(stats.t.ppf((1.0 + request.confidence_level) / 2.0, len(clean) - 1))
    interval = ConfidenceInterval(
        confidence_level=request.confidence_level,
        lower=mean - critical * standard_error,
        upper=mean + critical * standard_error,
    )
    return {
        "sample_size": len(clean),
        "missing_row_count": missing,
        "missing_data_handling": "Rows missing the analyzed value were excluded.",
        "estimates": (
            StatisticalEstimate(metric=f"{field}.mean", value=mean),
            StatisticalEstimate(metric=f"{field}.standard_error", value=standard_error),
        ),
        "confidence_interval": interval,
        "assumptions": (_normality_check(clean),),
    }


def _correlation(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    x_field, y_field = _required(request.x_field), _required(request.y_field)
    complete, missing = _complete_numeric(frame, (x_field, y_field))
    x, y = complete[x_field].to_numpy(), complete[y_field].to_numpy()
    if np.std(x) == 0 or np.std(y) == 0:
        raise StatisticalAnalysisError("correlation requires non-constant numeric fields")
    pearson = stats.pearsonr(x, y, alternative=request.alternative.value)
    spearman = stats.spearmanr(x, y, alternative=request.alternative.value)
    effect = StatisticalEstimate(metric="pearson_r", value=float(pearson.statistic))
    return _tested_payload(
        request,
        sample_size=len(complete),
        missing=missing,
        estimates=(
            effect,
            StatisticalEstimate(metric="spearman_rho", value=float(spearman.statistic)),
        ),
        statistic_name="pearson_r",
        statistic=float(pearson.statistic),
        p_value=float(pearson.pvalue),
        effect=effect,
        assumptions=(
            _normality_check(x, name=f"normality:{x_field}"),
            _normality_check(y, name=f"normality:{y_field}"),
        ),
        warnings=(
            "Correlation measures association and does not establish causation.",
            "The p-value and significance decision refer to Pearson correlation. "
            "Spearman correlation is a separate descriptive estimate without a reported p-value.",
        ),
    )


def _t_test(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    groups, missing = _two_groups(frame, request)
    first, second = groups.values()
    if np.std(first) == 0 or np.std(second) == 0:
        raise StatisticalAnalysisError("t_test requires variability within each group")
    test = stats.ttest_ind(first, second, equal_var=False, alternative=request.alternative.value)
    pooled = math.sqrt(
        ((len(first) - 1) * np.var(first, ddof=1) + (len(second) - 1) * np.var(second, ddof=1))
        / (len(first) + len(second) - 2)
    )
    d = 0.0 if pooled == 0 else float((np.mean(first) - np.mean(second)) / pooled)
    effect = StatisticalEstimate(metric="cohen_d", value=d)
    return _tested_payload(
        request,
        sample_size=len(first) + len(second),
        missing=missing,
        group_sizes={name: len(values) for name, values in groups.items()},
        estimates=(_group_mean_estimates(groups)),
        statistic_name="welch_t",
        statistic=float(test.statistic),
        p_value=float(test.pvalue),
        effect=effect,
        assumptions=tuple(
            _normality_check(values, name=f"normality:{name}") for name, values in groups.items()
        ),
    )


def _mann_whitney(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    groups, missing = _two_groups(frame, request)
    first, second = groups.values()
    test = stats.mannwhitneyu(first, second, alternative=request.alternative.value)
    rank_biserial = 1.0 - (2.0 * float(test.statistic)) / (len(first) * len(second))
    effect = StatisticalEstimate(metric="rank_biserial", value=rank_biserial)
    return _tested_payload(
        request,
        sample_size=len(first) + len(second),
        missing=missing,
        group_sizes={name: len(values) for name, values in groups.items()},
        estimates=_group_median_estimates(groups),
        statistic_name="mann_whitney_u",
        statistic=float(test.statistic),
        p_value=float(test.pvalue),
        effect=effect,
    )


def _chi_square(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    x_field, y_field = _required(request.x_field), _required(request.y_field)
    complete = frame[[x_field, y_field]].dropna()
    _require_rows(complete)
    table = pd.crosstab(complete[x_field], complete[y_field])
    if table.shape[0] < 2 or table.shape[1] < 2:
        raise StatisticalAnalysisError("chi-square requires at least two levels in each field")
    statistic, p_value, _, expected = stats.chi2_contingency(table)
    denominator = len(complete) * min(table.shape[0] - 1, table.shape[1] - 1)
    cramers_v = math.sqrt(float(statistic) / denominator)
    effect = StatisticalEstimate(metric="cramers_v", value=cramers_v)
    low_expected_rate = float(np.mean(expected < 5))
    assumption = AssumptionCheck(
        name="expected_cell_counts",
        status=AssumptionStatus.PASSED if low_expected_rate <= 0.2 else AssumptionStatus.FAILED,
        message=f"{low_expected_rate:.1%} of expected cell counts are below 5.",
    )
    return _tested_payload(
        request,
        sample_size=len(complete),
        missing=len(frame) - len(complete),
        estimates=(StatisticalEstimate(metric="contingency_cells", value=float(table.size)),),
        statistic_name="chi_square",
        statistic=float(statistic),
        p_value=float(p_value),
        effect=effect,
        assumptions=(assumption,),
        warnings=("Chi-square measures association and does not establish causation.",),
    )


def _anova(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    groups, missing = _many_groups(frame, request)
    test = stats.f_oneway(*groups.values())
    all_values = np.concatenate(tuple(groups.values()))
    grand_mean = np.mean(all_values)
    between = sum(len(values) * (np.mean(values) - grand_mean) ** 2 for values in groups.values())
    total = float(np.sum((all_values - grand_mean) ** 2))
    eta = 0.0 if total == 0 else float(between / total)
    effect = StatisticalEstimate(metric="eta_squared", value=eta)
    assumptions = [
        *(_normality_check(values, name=f"normality:{name}") for name, values in groups.items()),
        _variance_check(groups),
    ]
    return _tested_payload(
        request,
        sample_size=len(all_values),
        missing=missing,
        group_sizes={name: len(values) for name, values in groups.items()},
        estimates=_group_mean_estimates(groups),
        statistic_name="anova_f",
        statistic=float(test.statistic),
        p_value=float(test.pvalue),
        effect=effect,
        assumptions=tuple(assumptions),
    )


def _kruskal_wallis(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    groups, missing = _many_groups(frame, request)
    test = stats.kruskal(*groups.values())
    total = sum(len(values) for values in groups.values())
    epsilon = max(0.0, float((test.statistic - len(groups) + 1) / (total - len(groups))))
    effect = StatisticalEstimate(metric="epsilon_squared", value=epsilon)
    return _tested_payload(
        request,
        sample_size=total,
        missing=missing,
        group_sizes={name: len(values) for name, values in groups.items()},
        estimates=_group_median_estimates(groups),
        statistic_name="kruskal_h",
        statistic=float(test.statistic),
        p_value=float(test.pvalue),
        effect=effect,
    )


def _linear_regression(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    x_field, y_field = _required(request.x_field), _required(request.y_field)
    complete, missing = _complete_numeric(frame, (x_field, y_field))
    result = stats.linregress(
        complete[x_field], complete[y_field], alternative=request.alternative.value
    )
    if result.stderr == 0:
        raise StatisticalAnalysisError(
            "linear regression inference is undefined for a perfect deterministic fit"
        )
    effect = StatisticalEstimate(metric="r_squared", value=float(result.rvalue**2))
    return _tested_payload(
        request,
        sample_size=len(complete),
        missing=missing,
        estimates=(
            StatisticalEstimate(metric="slope", value=float(result.slope)),
            StatisticalEstimate(metric="intercept", value=float(result.intercept)),
            effect,
        ),
        statistic_name="slope_t_test",
        statistic=float(result.slope / result.stderr),
        p_value=float(result.pvalue),
        effect=effect,
        assumptions=_linear_regression_checks(
            complete[x_field].to_numpy(),
            complete[y_field].to_numpy(),
            result.slope,
            result.intercept,
        ),
        warnings=("Linear regression describes association and does not establish causation.",),
    )


def _logistic_regression(frame: pd.DataFrame, request: StatisticalRequest) -> dict[str, Any]:
    x_field, y_field = _required(request.x_field), _required(request.y_field)
    complete = frame[[x_field, y_field]].dropna()
    _require_rows(complete)
    x = _numeric(complete[x_field], x_field).to_numpy(dtype=float)
    classes = list(pd.unique(complete[y_field]))
    if len(classes) != 2:
        raise StatisticalAnalysisError("logistic regression requires a binary outcome field")
    positive = request.positive_class
    if positive is None:
        raise StatisticalAnalysisError("logistic regression requires an explicit positive_class")
    if positive not in classes:
        raise StatisticalAnalysisError("positive_class is not present in the outcome field")
    y = np.asarray((complete[y_field] == positive).to_numpy(), dtype=np.float64)
    if np.std(x) == 0:
        raise StatisticalAnalysisError("logistic regression requires a non-constant predictor")
    design = np.column_stack((np.ones(len(x)), x))
    beta = np.zeros(2)
    converged = False
    covariance = np.eye(2)
    for _ in range(100):
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(design @ beta, -30, 30)))
        weights = np.clip(probabilities * (1.0 - probabilities), 1e-9, None)
        hessian = design.T @ (weights[:, None] * design)
        try:
            covariance = np.linalg.inv(hessian)
        except np.linalg.LinAlgError as exc:
            raise StatisticalAnalysisError("logistic regression design is singular") from exc
        step = covariance @ design.T @ (y - probabilities)
        beta += step
        if float(np.max(np.abs(step))) < 1e-8:
            converged = True
            break
    if not converged:
        raise StatisticalAnalysisError("logistic regression did not converge")
    standard_error = math.sqrt(float(covariance[1, 1]))
    z_score = float(beta[1] / standard_error)
    p_value = float(2.0 * stats.norm.sf(abs(z_score)))
    odds_ratio = float(math.exp(float(np.clip(beta[1], -700, 700))))
    effect = StatisticalEstimate(metric="odds_ratio_log", value=float(beta[1]))
    assumptions = _logistic_regression_checks(y, probabilities)
    return _tested_payload(
        request,
        sample_size=len(complete),
        missing=len(frame) - len(complete),
        group_sizes={
            _typed_group_label(value): int((complete[y_field] == value).sum()) for value in classes
        },
        estimates=(
            StatisticalEstimate(metric="coefficient", value=float(beta[1])),
            StatisticalEstimate(metric="intercept", value=float(beta[0])),
            StatisticalEstimate(metric="odds_ratio", value=odds_ratio),
        ),
        statistic_name="wald_z",
        statistic=z_score,
        p_value=p_value,
        effect=effect,
        assumptions=assumptions,
        warnings=("Logistic regression describes association and does not establish causation.",),
    )


def _tested_payload(
    request: StatisticalRequest,
    *,
    sample_size: int,
    missing: int,
    estimates: tuple[StatisticalEstimate, ...],
    statistic_name: str,
    statistic: float,
    p_value: float,
    effect: StatisticalEstimate,
    group_sizes: dict[str, int] | None = None,
    assumptions: tuple[AssumptionCheck, ...] = (),
    warnings: tuple[str, ...] = (),
) -> dict[str, Any]:
    numeric_outputs = (
        statistic,
        p_value,
        effect.value,
        *(estimate.value for estimate in estimates),
    )
    if not all(math.isfinite(value) for value in numeric_outputs):
        raise StatisticalAnalysisError(
            "statistical test is undefined for constant or degenerate data"
        )
    adjusted_alpha = request.alpha / request.multiple_testing_count
    threshold = _PRACTICAL_THRESHOLDS[effect.metric]
    return {
        "sample_size": sample_size,
        "missing_row_count": missing,
        "missing_data_handling": "Complete-case analysis across required fields.",
        "group_sizes": group_sizes or {},
        "estimates": estimates,
        "statistic_name": statistic_name,
        "statistic": statistic,
        "p_value": min(1.0, max(0.0, p_value)),
        "adjusted_alpha": adjusted_alpha,
        "statistically_significant": p_value < adjusted_alpha,
        "practically_significant": abs(effect.value) >= threshold,
        "effect_size": effect,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _required(value: str | None) -> str:
    if value is None:
        raise StatisticalAnalysisError("required field is missing")
    return value


def _require_columns(frame: pd.DataFrame, fields: tuple[str, ...]) -> None:
    unknown = sorted(set(fields) - set(frame.columns))
    if unknown:
        raise StatisticalAnalysisError(f"unknown statistical fields: {', '.join(unknown)}")


def _numeric(series: pd.Series[Any], field: str) -> pd.Series[float]:
    converted = pd.to_numeric(series, errors="coerce")
    invalid = series.notna() & converted.isna()
    if invalid.any():
        raise StatisticalAnalysisError(f"field {field!r} contains non-numeric values")
    return converted.astype(float)


def _require_sample(values: NDArray[np.float64], subject: str) -> None:
    if len(values) < _MIN_SAMPLE_SIZE:
        raise InsufficientSampleError(
            f"{subject} has only {len(values)} usable values; "
            f"at least {_MIN_SAMPLE_SIZE} are required"
        )


def display_group_label(label: str) -> str:
    """Return the original group value encoded in a collision-safe group label."""
    _, separator, encoded = label.partition(":")
    if not separator:
        return label
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError:
        return label
    return str(decoded)


def _require_rows(frame: pd.DataFrame) -> None:
    if len(frame) < _MIN_SAMPLE_SIZE:
        raise InsufficientSampleError(
            f"complete-case analysis requires at least {_MIN_SAMPLE_SIZE} rows"
        )


def _one_numeric(frame: pd.DataFrame, field: str) -> tuple[NDArray[np.float64], int]:
    values = _numeric(frame[field], field)
    clean = np.asarray(values.dropna().to_numpy(), dtype=np.float64)
    _require_sample(clean, f"field {field!r}")
    return clean, int(values.isna().sum())


def _complete_numeric(frame: pd.DataFrame, fields: tuple[str, ...]) -> tuple[pd.DataFrame, int]:
    converted = pd.DataFrame({field: _numeric(frame[field], field) for field in fields})
    complete = converted.dropna()
    _require_rows(complete)
    return complete, len(frame) - len(complete)


def _grouped(
    frame: pd.DataFrame, request: StatisticalRequest
) -> tuple[dict[str, NDArray[np.float64]], int]:
    value_field, group_field = _required(request.value_field), _required(request.group_field)
    converted = pd.DataFrame(
        {value_field: _numeric(frame[value_field], value_field), group_field: frame[group_field]}
    )
    complete = converted.dropna()
    groups = {
        _typed_group_label(name): np.asarray(values[value_field].to_numpy(), dtype=np.float64)
        for name, values in complete.groupby(group_field, sort=False)
    }
    for name, values in groups.items():
        _require_sample(values, f"group {display_group_label(name)!r} of {group_field!r}")
    return groups, len(frame) - len(complete)


def _two_groups(
    frame: pd.DataFrame, request: StatisticalRequest
) -> tuple[dict[str, NDArray[np.float64]], int]:
    groups, missing = _grouped(frame, request)
    if len(groups) != 2:
        raise StatisticalAnalysisError("two-group comparison requires exactly two groups")
    ordered_labels = tuple(_typed_group_label(value) for value in request.group_order)
    if set(ordered_labels) != set(groups):
        raise StatisticalAnalysisError(
            "ordered group identities must match exactly the two observed groups"
        )
    return {label: groups[label] for label in ordered_labels}, missing


def _typed_group_label(value: object) -> str:
    normalized = value.item() if isinstance(value, np.generic) else value
    encoded = json.dumps(normalized, ensure_ascii=True, sort_keys=True, default=str)
    return f"{type(normalized).__name__}:{encoded}"


def _many_groups(
    frame: pd.DataFrame, request: StatisticalRequest
) -> tuple[dict[str, NDArray[np.float64]], int]:
    groups, missing = _grouped(frame, request)
    if len(groups) < 2:
        raise StatisticalAnalysisError("group comparison requires at least two groups")
    return groups, missing


def _normality_check(values: NDArray[np.float64], *, name: str = "normality") -> AssumptionCheck:
    if np.ptp(values) == 0:
        return AssumptionCheck(
            name=name,
            status=AssumptionStatus.FAILED,
            message="Normality cannot be established for constant values.",
        )
    if len(values) > 5_000:
        return AssumptionCheck(
            name=name,
            status=AssumptionStatus.NOT_CHECKED,
            message="Shapiro-Wilk was not run above 5,000 observations.",
        )
    p_value = float(stats.shapiro(values).pvalue)
    return AssumptionCheck(
        name=name,
        status=AssumptionStatus.PASSED if p_value >= 0.05 else AssumptionStatus.FAILED,
        message=f"Shapiro-Wilk p-value={p_value:.6g}.",
    )


def _variance_check(groups: dict[str, NDArray[np.float64]]) -> AssumptionCheck:
    p_value = float(stats.levene(*groups.values()).pvalue)
    return AssumptionCheck(
        name="equal_variance",
        status=AssumptionStatus.PASSED if p_value >= 0.05 else AssumptionStatus.FAILED,
        message=f"Levene p-value={p_value:.6g}.",
    )


def _linear_regression_checks(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    slope: float,
    intercept: float,
) -> tuple[AssumptionCheck, ...]:
    fitted = intercept + slope * x
    residuals = y - fitted
    absolute_residuals = np.abs(residuals)
    if np.ptp(absolute_residuals) == 0:
        homoscedasticity = AssumptionCheck(
            name="homoscedasticity",
            status=AssumptionStatus.NOT_CHECKED,
            message="Residual magnitudes are constant; a variance-pattern test is not informative.",
        )
    else:
        variance_pattern = stats.spearmanr(fitted, absolute_residuals)
        p_value = float(variance_pattern.pvalue)
        if not math.isfinite(p_value):
            homoscedasticity = AssumptionCheck(
                name="homoscedasticity",
                status=AssumptionStatus.NOT_CHECKED,
                message="Residual variance pattern was not testable for this sample.",
            )
        else:
            homoscedasticity = AssumptionCheck(
                name="homoscedasticity",
                status=(AssumptionStatus.PASSED if p_value >= 0.05 else AssumptionStatus.FAILED),
                message=(
                    "Spearman test of fitted values versus absolute residuals "
                    f"has p-value={p_value:.6g}."
                ),
            )
    return (
        AssumptionCheck(
            name="linearity",
            status=AssumptionStatus.NOT_CHECKED,
            message=(
                "Linearity requires residual-plot diagnostics and was not asserted automatically."
            ),
        ),
        homoscedasticity,
        _normality_check(residuals, name="residual_normality"),
    )


def _logistic_regression_checks(
    outcome: NDArray[np.float64],
    probabilities: NDArray[np.float64],
) -> tuple[AssumptionCheck, ...]:
    class_counts = np.bincount(outcome.astype(int), minlength=2)
    sparse = int(np.min(class_counts)) < 10
    extreme_probability = bool(np.any(probabilities <= 1e-6) or np.any(probabilities >= 1.0 - 1e-6))
    return (
        AssumptionCheck(
            name="binary_outcome_and_convergence",
            status=AssumptionStatus.PASSED,
            message="Outcome is binary and Newton-Raphson optimization converged.",
        ),
        AssumptionCheck(
            name="linearity_of_logit",
            status=AssumptionStatus.NOT_CHECKED,
            message=(
                "Linearity of the continuous predictor in the logit requires dedicated diagnostics."
            ),
        ),
        AssumptionCheck(
            name="sparse_outcome",
            status=AssumptionStatus.FAILED if sparse else AssumptionStatus.PASSED,
            message=(
                f"Smallest outcome class has {int(np.min(class_counts))} observations; "
                "at least 10 per class is recommended."
            ),
        ),
        AssumptionCheck(
            name="separation",
            status=(AssumptionStatus.FAILED if extreme_probability else AssumptionStatus.PASSED),
            message=(
                "Extreme fitted probabilities indicate possible separation."
                if extreme_probability
                else "No extreme fitted probabilities indicated complete separation."
            ),
        ),
    )


def _group_mean_estimates(
    groups: dict[str, NDArray[np.float64]],
) -> tuple[StatisticalEstimate, ...]:
    return tuple(
        StatisticalEstimate(metric=f"group[{name}].mean", value=float(np.mean(values)))
        for name, values in groups.items()
    )


def _group_median_estimates(
    groups: dict[str, NDArray[np.float64]],
) -> tuple[StatisticalEstimate, ...]:
    return tuple(
        StatisticalEstimate(metric=f"group[{name}].median", value=float(np.median(values)))
        for name, values in groups.items()
    )
