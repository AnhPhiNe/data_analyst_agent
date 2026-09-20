"""Deterministic publication gates for insight drafts and their Evidence Trails."""

from __future__ import annotations

import math
import re
from typing import Literal
from uuid import uuid4

from tabular_analytics_agent.data import QueryResult, generated_alias_function
from tabular_analytics_agent.domain import (
    ActionStatus,
    DataProfile,
    EvidenceTrail,
    EvidenceValue,
    FilterScope,
    InsightAssertion,
    InsightOperator,
    SemanticAnnotation,
    ToolAction,
    UnsupportedClaim,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    VerifiedInsight,
    display_float,
    fingerprint_semantic_annotations,
)
from tabular_analytics_agent.statistics import (
    AssumptionStatus,
    StatisticalResult,
    display_group_label,
)
from tabular_analytics_agent.verification.provenance import (
    EvidenceResult,
    query_claim_status,
    query_provenance_status,
    result_reference,
    statistical_action_status,
    statistical_full_data_status,
)
from tabular_analytics_agent.verification.query_evidence import (
    verified_dataset_count_matches_profile,
)

InsightPublication = VerifiedInsight | UnsupportedClaim


def available_evidence_values(result: EvidenceResult) -> tuple[EvidenceValue, ...]:
    """Expose exact metric identifiers that an insight draft may reference."""
    if isinstance(result, QueryResult):
        cells = tuple(
            EvidenceValue(metric=f"row[{row_index}].{column.name}", value=row[column_index])
            for row_index, row in enumerate(result.rows)
            for column_index, column in enumerate(result.columns)
        )
        if result.truncated:
            # A truncated result only knows how many rows it returned, not how many matched.
            return cells
        return (*cells, EvidenceValue(metric="result.row_count", value=result.row_count))

    values: dict[str, EvidenceValue] = {
        estimate.metric: EvidenceValue(
            metric=estimate.metric, value=estimate.value, unit=estimate.unit
        )
        for estimate in result.estimates
    }
    if result.statistic_name is not None and result.statistic is not None:
        values[result.statistic_name] = EvidenceValue(
            metric=result.statistic_name,
            value=result.statistic,
        )
    if result.p_value is not None:
        values["p_value"] = EvidenceValue(metric="p_value", value=result.p_value)
    if result.adjusted_alpha is not None:
        values["adjusted_alpha"] = EvidenceValue(
            metric="adjusted_alpha", value=result.adjusted_alpha
        )
    if result.confidence_interval is not None:
        values["confidence_interval.lower"] = EvidenceValue(
            metric="confidence_interval.lower", value=result.confidence_interval.lower
        )
        values["confidence_interval.upper"] = EvidenceValue(
            metric="confidence_interval.upper", value=result.confidence_interval.upper
        )
    if result.effect_size is not None:
        values[result.effect_size.metric] = EvidenceValue(
            metric=result.effect_size.metric,
            value=result.effect_size.value,
            unit=result.effect_size.unit,
        )
    return tuple(values.values())


def publish_insight(
    *,
    assertion: InsightAssertion,
    evidence_metrics: tuple[str, ...],
    caveats: tuple[str, ...],
    profile: DataProfile,
    action: ToolAction,
    result: EvidenceResult,
    current_working_dataset_version: int,
    semantic_annotations: tuple[SemanticAnnotation, ...] = (),
) -> InsightPublication:
    """Publish only claims grounded in current, exactly named deterministic evidence."""
    available = {value.metric: value for value in available_evidence_values(result)}
    missing_metrics = sorted(set(evidence_metrics) - set(available))
    claim, assertion_supported, assertion_message = _evaluate_assertion(
        assertion, available, result, selected_metrics=set(evidence_metrics)
    )
    source_fields = _source_fields(action, result)
    profile_fields = {field.name for field in profile.fields}
    unknown_fields = sorted(set(source_fields) - profile_fields)
    verified_dataset_count = isinstance(result, QueryResult) and (
        verified_dataset_count_matches_profile(profile, result)
    )
    schema_grounding_passed = (bool(source_fields) and not unknown_fields) or (
        not source_fields and verified_dataset_count
    )
    schema_grounding_message = (
        "All referenced source fields exist in the Data Profile."
        if source_fields and not unknown_fields
        else (
            "Whole-dataset COUNT(*) matches the profiled row count."
            if verified_dataset_count
            else "Unknown or absent evidence fields: "
            f"{', '.join(unknown_fields) or 'none supplied'}."
        )
    )
    dataset_count_scope: Literal["whole_dataset"] | None = (
        "whole_dataset" if verified_dataset_count else None
    )
    action_verification_passed = bool(action.verification_results) and all(
        item.status is VerificationStatus.PASSED for item in action.verification_results
    )
    result_matches_dataset = not isinstance(result, QueryResult) or (
        result.dataset_id == profile.dataset.dataset_id
    )
    result_version = (
        result.working_dataset_version
        if isinstance(result, QueryResult)
        else action.working_dataset_version
    )
    result_reference_matches = action.output_ref == result_reference(result)
    if isinstance(result, QueryResult):
        provenance_passed, provenance_message = query_provenance_status(result)
        claimed_metrics = tuple(
            dict.fromkeys(
                (
                    *evidence_metrics,
                    assertion.left_metric,
                    *((assertion.right_metric,) if assertion.right_metric else ()),
                )
            )
        )
        lineage_passed, lineage_message = query_claim_status(
            result,
            claimed_metrics,
            profile_fields={field.casefold() for field in profile_fields},
        )
    else:
        provenance_passed, provenance_message = statistical_full_data_status(result)
        lineage_passed, lineage_message = statistical_action_status(action, result)
    checks = (
        VerificationCheck(
            name="tool_action",
            passed=action.status is ActionStatus.SUCCEEDED and action_verification_passed,
            message=(
                "Tool Action succeeded and passed its Verification Gates."
                if action.status is ActionStatus.SUCCEEDED and action_verification_passed
                else "Tool Action did not succeed with passed Verification Gates."
            ),
        ),
        VerificationCheck(
            name="result_binding",
            passed=result_reference_matches,
            message=(
                "Tool Action output reference identifies this deterministic result."
                if result_reference_matches
                else "Tool Action output reference does not identify this deterministic result."
            ),
        ),
        VerificationCheck(
            name="dataset_identity",
            passed=result_matches_dataset,
            message=(
                "Evidence belongs to the profiled Source Dataset."
                if result_matches_dataset
                else "Evidence belongs to a different Source Dataset."
            ),
        ),
        VerificationCheck(
            name="working_dataset_version",
            passed=(
                action.working_dataset_version == current_working_dataset_version
                and result_version == current_working_dataset_version
            ),
            message=(
                "Evidence uses the current Working Dataset version."
                if action.working_dataset_version == current_working_dataset_version
                and result_version == current_working_dataset_version
                else "Evidence is stale relative to the current Working Dataset."
            ),
        ),
        VerificationCheck(
            name="provenance",
            passed=provenance_passed,
            message=provenance_message,
        ),
        VerificationCheck(
            name="execution_scope",
            passed=lineage_passed,
            message=lineage_message,
        ),
        VerificationCheck(
            name="schema_grounding",
            passed=schema_grounding_passed,
            message=schema_grounding_message,
        ),
        VerificationCheck(
            name="evidence_metrics",
            passed=bool(evidence_metrics) and not missing_metrics,
            message=(
                "Every requested evidence metric matches deterministic output."
                if evidence_metrics and not missing_metrics
                else (
                    "Missing deterministic evidence metrics: "
                    f"{', '.join(missing_metrics) or 'none supplied'}."
                )
            ),
        ),
        VerificationCheck(
            name="claim_scope",
            passed=assertion_supported,
            message=assertion_message,
        ),
    )
    status = (
        VerificationStatus.PASSED
        if all(check.passed for check in checks)
        else VerificationStatus.FAILED
    )
    verification = VerificationResult(status=status, checks=checks)
    evidence = None
    if evidence_metrics and not missing_metrics and (source_fields or verified_dataset_count):
        evidence = EvidenceTrail(
            trail_id=uuid4(),
            dataset_id=profile.dataset.dataset_id,
            working_dataset_version=action.working_dataset_version,
            semantic_annotation_fingerprint=fingerprint_semantic_annotations(semantic_annotations),
            source_fields=source_fields,
            filters=_filters(action, result),
            filter_scopes=_filter_scopes(result),
            dataset_count_scope=dataset_count_scope,
            source_row_count=profile.row_count,
            result_row_count=_result_row_count(result),
            missing_data_handling=_missing_data_handling(result),
            tool_action_ids=(action.action_id,),
            tool_parameters=(action.inputs,),
            values=tuple(available[metric] for metric in evidence_metrics),
            caveats=tuple(dict.fromkeys((*_result_caveats(result), *caveats))),
            provenance_version=(
                result.inspection.provenance_version
                if isinstance(result, QueryResult) and result.inspection is not None
                else "v1"
                if isinstance(result, StatisticalResult) and result.sampled is False
                else None
            ),
            dataset_row_count=(
                result.dataset_row_count if isinstance(result, StatisticalResult) else None
            ),
            population_row_count=(
                result.population_row_count if isinstance(result, StatisticalResult) else None
            ),
            rows_loaded=result.rows_loaded if isinstance(result, StatisticalResult) else None,
            sample_size=result.sample_size if isinstance(result, StatisticalResult) else None,
            missing_row_count=(
                result.missing_row_count if isinstance(result, StatisticalResult) else None
            ),
            sampled=result.sampled if isinstance(result, StatisticalResult) else None,
            sampling_method=(
                result.sampling_method if isinstance(result, StatisticalResult) else None
            ),
            sampling_seed=(result.sampling_seed if isinstance(result, StatisticalResult) else None),
            partial=result.partial if isinstance(result, StatisticalResult) else None,
            truncated=(result.truncated if isinstance(result, StatisticalResult) else None),
        )
    if status is VerificationStatus.FAILED:
        failed_messages = "; ".join(check.message for check in checks if not check.passed)
        return UnsupportedClaim(
            claim_id=uuid4(),
            claim=claim,
            reason=failed_messages,
            verification=verification,
            evidence=evidence,
        )
    if evidence is None:
        raise AssertionError("passed publication requires complete evidence")
    return VerifiedInsight(
        insight_id=uuid4(),
        claim=claim,
        assertion=assertion,
        evidence=evidence,
        verification=verification,
    )


def reject_insight_draft(*, claim: str, reason: str) -> UnsupportedClaim:
    """Record a malformed insight draft as unsupported without discarding other drafts."""
    return UnsupportedClaim(
        claim_id=uuid4(),
        claim=claim,
        reason=reason,
        verification=VerificationResult(
            status=VerificationStatus.FAILED,
            checks=(VerificationCheck(name="draft_contract", passed=False, message=reason),),
        ),
    )


def _source_fields(action: ToolAction, result: EvidenceResult) -> tuple[str, ...]:
    if isinstance(result, StatisticalResult):
        return result.source_fields
    raw = action.inputs.get("required_fields", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(field) for field in raw)


def _filters(action: ToolAction, result: EvidenceResult) -> tuple[str, ...]:
    if isinstance(result, QueryResult) and result.filters:
        return result.filters
    raw = action.inputs.get("filters", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(value) for value in raw)


def _filter_scopes(result: EvidenceResult) -> tuple[FilterScope, ...]:
    if isinstance(result, QueryResult) and result.filter_scopes:
        return result.filter_scopes
    return ()


def _result_row_count(result: EvidenceResult) -> int:
    return result.row_count if isinstance(result, QueryResult) else result.sample_size


def _missing_data_handling(result: EvidenceResult) -> str:
    if isinstance(result, StatisticalResult):
        return result.missing_data_handling
    return "Missing values follow the recorded SQL query semantics."


def _result_caveats(result: EvidenceResult) -> tuple[str, ...]:
    if not isinstance(result, StatisticalResult):
        return ()
    assumption_caveats = tuple(
        f"{check.name}: {check.message}"
        for check in result.assumptions
        if check.status is not AssumptionStatus.PASSED
    )
    return (*result.warnings, *assumption_caveats)


def _evaluate_assertion(
    assertion: InsightAssertion,
    available: dict[str, EvidenceValue],
    result: EvidenceResult,
    *,
    selected_metrics: set[str],
) -> tuple[str, bool, str]:
    left = available.get(assertion.left_metric)
    right = available.get(assertion.right_metric or "")
    fallback = (
        f"{assertion.left_metric} {assertion.operator.value} {assertion.right_metric or ''}"
    ).strip()
    if (
        isinstance(result, StatisticalResult)
        and "p_value" in selected_metrics
        and (not result.statistic_name or result.statistic is None)
    ):
        return fallback, False, "P-value evidence requires an identified test statistic."
    if left is None or (assertion.right_metric and right is None):
        return fallback, False, "Insight assertion references unavailable deterministic evidence."

    operator = assertion.operator
    if operator is InsightOperator.REPORTS:
        row_match = re.fullmatch(r"row\[(\d+)]\.(.+)", left.metric)
        if (
            row_match
            and isinstance(result, QueryResult)
            and row_match.group(2) in result.group_by_columns
        ):
            return (
                fallback,
                False,
                "A GROUP BY label identifies a result row; report a measured value instead.",
            )
        return (
            f"{_sentence_start(_display_metric(left.metric, result))} is "
            f"{_format_evidence_value(left.value)}.",
            True,
            "Reported value comes directly from deterministic evidence.",
        )
    if operator in {
        InsightOperator.EQUALS,
        InsightOperator.GREATER_THAN,
        InsightOperator.LESS_THAN,
    }:
        if right is None:
            return fallback, False, "Comparison requires two deterministic evidence values."
        supported = _comparison_holds(left.value, right.value, operator)
        symbol = {
            InsightOperator.EQUALS: "equals",
            InsightOperator.GREATER_THAN: "is greater than",
            InsightOperator.LESS_THAN: "is less than",
        }[operator]
        claim = (
            f"{_sentence_start(_display_metric(left.metric, result))} {symbol} "
            f"{_display_metric(right.metric, result)} "
            f"({_format_evidence_value(left.value)} versus "
            f"{_format_evidence_value(right.value)})."
        )
        return (
            claim,
            supported,
            (
                "Structured comparison is confirmed by deterministic evidence."
                if supported
                else "Structured comparison contradicts deterministic evidence."
            ),
        )
    if operator in {InsightOperator.POSITIVE, InsightOperator.NEGATIVE}:
        numeric = _numeric_evidence(left.value)
        supported = numeric is not None and (
            numeric > 0 if operator is InsightOperator.POSITIVE else numeric < 0
        )
        direction = "positive" if operator is InsightOperator.POSITIVE else "negative"
        return (
            f"{_sentence_start(_display_metric(left.metric, result))} is {direction} "
            f"({_format_evidence_value(left.value)}).",
            supported,
            "Direction is confirmed by deterministic evidence."
            if supported
            else "Requested direction contradicts deterministic evidence.",
        )
    if operator in {
        InsightOperator.STATISTICALLY_SIGNIFICANT,
        InsightOperator.NOT_STATISTICALLY_SIGNIFICANT,
    }:
        adjusted_alpha = available.get("adjusted_alpha")
        if (
            not isinstance(result, StatisticalResult)
            or left.metric != "p_value"
            or adjusted_alpha is None
            or "adjusted_alpha" not in selected_metrics
        ):
            return (
                fallback,
                False,
                "Significance assertions require selected p-value and adjusted-alpha evidence.",
            )
        expected = operator is InsightOperator.STATISTICALLY_SIGNIFICANT
        p_value = _numeric_evidence(left.value)
        alpha = _numeric_evidence(adjusted_alpha.value)
        supported = p_value is not None and alpha is not None and (p_value < alpha) is expected
        qualifier = "statistically significant" if expected else "not statistically significant"
        return (
            f"The test for {_display_metric(result.statistic_name or '', result)} "
            f"is {qualifier} (p-value {_format_evidence_value(left.value)}, "
            f"adjusted alpha {_format_evidence_value(adjusted_alpha.value)}).",
            supported,
            "Significance assertion matches the adjusted deterministic test result."
            if supported
            else "Significance assertion contradicts the adjusted deterministic test result.",
        )
    return fallback, False, "Unsupported insight assertion operator."


def _comparison_holds(
    left: str | int | float | bool | None,
    right: str | int | float | bool | None,
    operator: InsightOperator,
) -> bool:
    if operator is InsightOperator.EQUALS:
        left_number, right_number = _numeric_evidence(left), _numeric_evidence(right)
        if left_number is not None and right_number is not None:
            return math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-9)
        return left == right
    left_number, right_number = _numeric_evidence(left), _numeric_evidence(right)
    if left_number is None or right_number is None:
        return False
    if operator is InsightOperator.GREATER_THAN:
        return left_number > right_number
    return left_number < right_number


def _numeric_evidence(value: str | int | float | bool | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _format_evidence_value(value: str | int | float | bool | None) -> str:
    if isinstance(value, float):
        # The digits a KPI shows, without binary noise such as 3.1000000000000005.
        return display_float(value)
    return str(value)


# Labels are written as they appear inside a sentence, so names and acronyms keep their case.
_METRIC_LABELS = {
    "adjusted_alpha": "adjusted alpha",
    "anova_f": "ANOVA F statistic",
    "chi_square": "chi-square statistic",
    "cohen_d": "Cohen's d",
    "cramers_v": "Cramér's V",
    "epsilon_squared": "epsilon squared",
    "eta_squared": "eta squared",
    "kruskal_h": "Kruskal-Wallis H statistic",
    "mann_whitney_u": "Mann-Whitney U statistic",
    "odds_ratio": "odds ratio",
    "odds_ratio_log": "log odds ratio",
    "p_value": "p-value",
    "pearson_r": "Pearson correlation",
    "r_squared": "R-squared",
    "rank_biserial": "rank-biserial correlation",
    "slope_t_test": "slope t statistic",
    "spearman_rho": "Spearman correlation",
    "wald_z": "Wald z statistic",
    "welch_t": "Welch t statistic",
}
# Labels for the functions named by aliases the SQL policy generates, such as count_1.
_GENERATED_ALIAS_LABELS = {
    "avg": "average",
    "count": "count",
    "max": "maximum",
    "median": "median",
    "min": "minimum",
    "stddev": "standard deviation",
    "sum": "sum",
    "value": "value",
}


def _display_metric(metric: str, result: EvidenceResult) -> str:
    """Describe a metric as it reads inside a sentence."""
    if metric == "result.row_count":
        return "result row count"
    if (
        metric == "p_value"
        and isinstance(result, StatisticalResult)
        and result.statistic_name
        and result.statistic_name != metric
    ):
        return f"p-value for {_display_metric(result.statistic_name, result)}"
    if metric in _METRIC_LABELS:
        if metric in {"adjusted_alpha", "p_value"}:
            return _METRIC_LABELS[metric]
        return _METRIC_LABELS[metric] + _statistical_scope(result)
    row_match = re.fullmatch(r"row\[(\d+)]\.(.+)", metric)
    if row_match:
        row_index = int(row_match.group(1))
        column = row_match.group(2)
        context = _row_context(result, row_index, column)
        # SQL conditions stay in the Evidence Trail, which the dashboard shows beneath the claim.
        if context:
            return f"{_humanize_identifier(column)} for {context}"
        if isinstance(result, QueryResult) and result.row_count == 1:
            return _humanize_identifier(column)
        return f"{_humanize_identifier(column)} in result row {row_index + 1}"
    group_match = re.fullmatch(r"group\[([^]]+)]\.(mean|median)", metric)
    if group_match:
        return f"{group_match.group(2)} for group {display_group_label(group_match.group(1))}"
    field_metric_match = re.fullmatch(
        r"(.+)\.(count|mean|standard_deviation|minimum|first_quartile|median|"
        r"third_quartile|maximum|standard_error)",
        metric,
    )
    if field_metric_match:
        statistic = _humanize_identifier(field_metric_match.group(2))
        field = _humanize_identifier(field_metric_match.group(1))
        return f"{statistic} {field}"
    return _humanize_identifier(metric)


def _statistical_scope(result: EvidenceResult) -> str:
    """Name the two related fields, for example ``between Sleep_Hours and Exam_Score``."""
    if not isinstance(result, StatisticalResult):
        return ""
    x_field, y_field = result.parameters.get("x_field"), result.parameters.get("y_field")
    if isinstance(x_field, str) and isinstance(y_field, str):
        return f" between {x_field} and {y_field}"
    return ""


def _humanize_identifier(value: str) -> str:
    label = _GENERATED_ALIAS_LABELS.get(generated_alias_function(value) or "")
    return label or value.replace("_", " ").strip().lower()


def _row_context(result: EvidenceResult, row_index: int, metric_column: str) -> str:
    """Describe a result row by its GROUP BY keys, for example ``year = 2024``.

    Ungrouped results fall back to their text columns.
    """
    if not isinstance(result, QueryResult) or row_index >= len(result.rows):
        return ""
    row = dict(zip((column.name for column in result.columns), result.rows[row_index], strict=True))
    keys = result.group_by_columns or tuple(
        name for name, value in row.items() if isinstance(value, str)
    )
    return ", ".join(
        f"{name} = {row[name]}" for name in keys if name != metric_column and name in row
    )


def _sentence_start(value: str) -> str:
    return value[:1].upper() + value[1:]
