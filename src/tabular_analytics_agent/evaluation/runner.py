"""Run golden evaluation cases through the local application and grade them deterministically.

Usage: python -m tabular_analytics_agent.evaluation.runner --runs 3
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from tabular_analytics_agent.application import (
    LocalAnalysisApplication,
    limits_from_environment,
    send_sample_values_from_environment,
)
from tabular_analytics_agent.evaluation.models import (
    EvaluationModel,
    ExpectedCalculation,
    ExpectedOutcome,
    ExpectedProfileFact,
    GoldenCase,
)
from tabular_analytics_agent.model_gateway import GeminiModelGateway, GeminiSettings
from tabular_analytics_agent.orchestration import AgentRunStatus, AgentState, RefusalCode

_MAX_PLAN_APPROVALS = 3
Scalar = str | int | float | bool | None
_GRADING_VERSION = "3"
_MISSING = object()


@dataclass(frozen=True)
class _OutputValue:
    metric: str
    value: Scalar
    evidence_metric: str
    result_ref: str
    group_values: tuple[tuple[str, Scalar], ...] = ()
    source: str = "query"
    result_row_count: int = 1


class CheckResult(EvaluationModel):
    name: str
    passed: bool
    detail: str


class CaseRunResult(EvaluationModel):
    case_id: str
    run_index: int
    expected_outcome: ExpectedOutcome
    actual_outcome: str
    status: str
    provider_error: bool
    passed: bool
    checks: tuple[CheckResult, ...]
    model_calls: int
    provider_requests: int
    total_tokens: int
    latency_ms: int
    claims: tuple[str, ...]
    error: str


class SuiteSummary(EvaluationModel):
    runs: int
    grading_version: str = _GRADING_VERSION
    passed: int
    provider_errors: int
    # Provider errors that were retried successfully are otherwise invisible in the results.
    provider_retries: int = 0
    pass_rate_excluding_provider_errors: float
    outcome_accuracy: float
    check_pass_rates: dict[str, float]
    average_model_calls: float
    average_total_tokens: float
    average_latency_ms: float
    failures: tuple[str, ...]


def load_cases(directory: Path) -> tuple[GoldenCase, ...]:
    return tuple(
        GoldenCase.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    )


def run_case(
    application: LocalAnalysisApplication,
    case: GoldenCase,
    *,
    project_root: Path,
    run_index: int = 0,
) -> CaseRunResult:
    """Upload the case dataset, ask the question, approve plans, and grade the result.

    A clarification pause is graded as it stands: confirming the agent's own hypothesis
    automatically would hide whether it asked for the right reason.
    """
    dataset = project_root / case.dataset_path
    content = dataset.read_bytes()
    if hashlib.sha256(content).hexdigest() != case.dataset_sha256:
        raise ValueError(f"dataset content does not match the hash recorded in {case.case_id}")
    workspace = application.ingest(application.stage_upload(dataset.name, content))
    state = application.start(workspace, case.user_request)
    for _ in range(_MAX_PLAN_APPROVALS):
        if state.get("status") != AgentRunStatus.AWAITING_PLAN_APPROVAL:
            break
        state = application.resume(workspace, True)
    return grade_run(case, state, run_index=run_index)


def grade_run(case: GoldenCase, state: AgentState, *, run_index: int = 0) -> CaseRunResult:
    actual = _actual_outcome(state)
    accepted = sorted(outcome.value for outcome in case.accepted_outcomes)
    claims = tuple(str(item.get("claim", "")) for item in state.get("verified_insights", []))
    forbidden = [
        text
        for text in case.forbidden_claims
        if any(text.casefold() in claim.casefold() for claim in claims)
    ]
    checks = [
        _check("outcome", actual in accepted, f"expected {' or '.join(accepted)}, got {actual}"),
        _check(
            "forbidden_claims",
            not forbidden,
            f"forbidden text present: {forbidden}" if forbidden else "no forbidden claims",
        ),
    ]
    if case.expected_outcome is ExpectedOutcome.ANSWERED:
        checks.extend(_answer_checks(case, state))
    elif case.expected_outcome is ExpectedOutcome.PROFILE:
        checks.extend(_profile_checks(case, state))
    traces = state.get("model_traces", [])
    return CaseRunResult(
        case_id=case.case_id,
        run_index=run_index,
        expected_outcome=case.expected_outcome,
        actual_outcome=actual,
        status=str(state.get("status", "")),
        provider_error=state.get("error_kind") == "provider",
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
        model_calls=len(traces),
        # Validation repairs and transport retries each consume provider quota.
        provider_requests=sum(
            1
            + int(trace.get("validation_repair_count", 0))
            + int(trace.get("provider_retry_count", 0))
            for trace in traces
        ),
        total_tokens=sum(int(trace.get("usage", {}).get("total_tokens", 0)) for trace in traces),
        latency_ms=sum(int(trace.get("latency_ms", 0)) for trace in traces),
        claims=claims,
        error=str(state.get("error", "")),
    )


def run_suite(
    application: LocalAnalysisApplication,
    cases: Sequence[GoldenCase],
    *,
    project_root: Path,
    runs: int,
    requests_per_minute: float,
    output_dir: Path,
    provider_retry_delay_seconds: float = 66.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SuiteSummary:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if not math.isfinite(requests_per_minute) or requests_per_minute <= 0:
        raise ValueError("requests_per_minute must be finite and greater than 0")
    if not cases:
        raise ValueError("at least one evaluation case is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[CaseRunResult] = []
    provider_retries = 0

    def paced_run(case: GoldenCase, run_index: int) -> CaseRunResult:
        started = clock()
        try:
            result = run_case(application, case, project_root=project_root, run_index=run_index)
        except Exception as exc:
            # One broken case is recorded as a failure instead of stopping the suite.
            result = _crashed_result(case, run_index, exc)
        # Space cases so the average request rate stays within the provider quota.
        wait = result.provider_requests * 60 / requests_per_minute - (clock() - started)
        if wait > 0:
            sleep(wait)
        return result

    with (output_dir / "results.jsonl").open("a", encoding="utf-8") as log:
        for run_index in range(runs):
            for case in cases:
                result = paced_run(case, run_index)
                if result.provider_error:
                    # Quota errors are retried once after the 65-second API key cooldown ends.
                    provider_retries += 1
                    sleep(provider_retry_delay_seconds)
                    result = paced_run(case, run_index)
                results.append(result)
                log.write(result.model_dump_json() + "\n")
                log.flush()
                _print_progress(result)
    summary = summarize(results).model_copy(update={"provider_retries": provider_retries})
    (output_dir / "summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return summary


def summarize(results: Sequence[CaseRunResult]) -> SuiteSummary:
    graded = [result for result in results if not result.provider_error]
    check_outcomes: dict[str, list[bool]] = {}
    for result in graded:
        for check in result.checks:
            check_outcomes.setdefault(check.name, []).append(check.passed)
    return SuiteSummary(
        runs=len(results),
        passed=sum(result.passed for result in results),
        provider_errors=len(results) - len(graded),
        pass_rate_excluding_provider_errors=_rate([result.passed for result in graded]),
        outcome_accuracy=_rate(check_outcomes.get("outcome", [])),
        check_pass_rates={name: _rate(values) for name, values in sorted(check_outcomes.items())},
        average_model_calls=_mean([result.model_calls for result in graded]),
        average_total_tokens=_mean([result.total_tokens for result in graded]),
        average_latency_ms=_mean([result.latency_ms for result in graded]),
        failures=tuple(
            f"{result.case_id}#{result.run_index}: "
            + "; ".join(
                f"{check.name}: {check.detail}" for check in result.checks if not check.passed
            )
            for result in results
            if not result.passed
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run golden evaluation cases against the model.")
    parser.add_argument("--cases", type=Path, default=Path("tests/evaluation_cases"))
    parser.add_argument("--case", action="append", default=[], help="Only run this case_id")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--rpm",
        type=float,
        default=None,
        help="Requests per minute to pace to (default: 12 per configured API key)",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.rpm is not None and (not math.isfinite(args.rpm) or args.rpm <= 0):
        parser.error("--rpm must be finite and greater than 0")

    cases = tuple(
        case for case in load_cases(args.cases) if not args.case or case.case_id in args.case
    )
    if not cases:
        parser.error("no evaluation cases selected")
    load_dotenv()
    output_dir: Path = args.output or Path(".eval") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    gateway = GeminiModelGateway(GeminiSettings.from_environment())
    # Each key serves up to 15 requests per minute; pace a little below the combined budget.
    requests_per_minute = args.rpm or 12.0 * gateway.api_key_count
    print(
        f"Using {gateway.api_key_count} Gemini API key(s); "
        f"pacing to {requests_per_minute:g} requests per minute"
    )
    limits, execution_budget = limits_from_environment()
    application = LocalAnalysisApplication(
        output_dir / "app-data",
        gateway,
        limits=limits,
        execution_budget=execution_budget,
        send_sample_values=send_sample_values_from_environment(),
    )
    summary = run_suite(
        application,
        cases,
        project_root=Path.cwd(),
        runs=args.runs,
        requests_per_minute=requests_per_minute,
        output_dir=output_dir,
    )
    print(summary.model_dump_json(indent=2))
    print(f"Results written to {output_dir}")
    return 0 if not summary.failures else 1


def _actual_outcome(state: AgentState) -> str:
    status = state.get("status")
    if _is_typed_refusal(state):
        return ExpectedOutcome.REFUSED.value
    if status == AgentRunStatus.AWAITING_SEMANTIC_REVIEW.value:
        return ExpectedOutcome.CLARIFICATION.value
    if (
        status == AgentRunStatus.COMPLETED.value
        and state.get("answered_from_profile")
        and state.get("data_profile")
    ):
        return ExpectedOutcome.PROFILE.value
    if status == AgentRunStatus.COMPLETED.value and state.get("verified_insights"):
        return ExpectedOutcome.ANSWERED.value
    # Neither FAILED nor rejected insight drafts establish a responsible refusal.
    return ExpectedOutcome.FAILED.value


def _is_typed_refusal(state: AgentState) -> bool:
    """Recognize only typed refusals, never an ordinary failure or its error message."""
    if state.get("status") != AgentRunStatus.REFUSED.value:
        return False
    if state.get("answered_from_profile") or state.get("verified_insights"):
        return False
    reason = state.get("refusal_reason")
    if not isinstance(reason, str) or not reason.strip():
        return False
    code = state.get("refusal_code")
    if code == RefusalCode.INSUFFICIENT_SAMPLE.value:
        return True
    if code != RefusalCode.UNAVAILABLE_METRIC.value:
        return False
    mappings = state.get("requested_metric_mappings")
    return isinstance(mappings, (list, tuple)) and any(
        isinstance(mapping, Mapping) and mapping.get("status") == "unavailable"
        for mapping in mappings
    )


def _answer_checks(case: GoldenCase, state: AgentState) -> list[CheckResult]:
    computed = _computed_values(state)
    missing_computed = [
        item for item in case.required_calculations if not _calculation_matches(item, computed)
    ]
    missing_coverage = [
        item
        for item in case.required_calculations
        if not _insight_covers_calculation(item, computed, state)
    ]
    allowed = {field.casefold() for field in case.allowed_fields}
    used = sorted(
        {
            str(field)
            for action in state.get("tool_actions", [])
            if action.get("status") == "succeeded"
            for field in action.get("inputs", {}).get("required_fields", [])
        }
    )
    outside = [field for field in used if field.casefold() not in allowed]
    checks = [
        _check("calculations", not missing_computed, _missing_detail(missing_computed)),
        _check("insight_coverage", not missing_coverage, _missing_detail(missing_coverage)),
        _check(
            "schema_grounding",
            not outside,
            f"fields outside the allowed set: {outside}" if outside else f"fields used: {used}",
        ),
    ]
    if case.valid_chart_types:
        rendered = [
            str(render["intent"]["artifact_type"]) for render in state.get("chart_renders", [])
        ]
        valid = {chart.value for chart in case.valid_chart_types}
        checks.append(
            _check(
                "chart",
                bool(rendered) and all(item in valid for item in rendered),
                f"rendered {rendered or 'no chart'} {state.get('artifact_error') or ''}".strip(),
            )
        )
    return checks


def _profile_checks(case: GoldenCase, state: AgentState) -> list[CheckResult]:
    missing = [
        fact for fact in case.required_profile_facts if not _profile_fact_matches(fact, state)
    ]
    return [
        _check(
            "profile_facts",
            not missing,
            _missing_profile_detail(missing),
        )
    ]


def _computed_values(state: AgentState) -> list[_OutputValue]:
    values: list[_OutputValue] = []
    for query_result in state.get("query_results", []):
        query_id = query_result.get("query_id")
        if not query_id:
            continue
        columns = [
            str(column["name"])
            for column in query_result.get("columns", [])
            if isinstance(column, Mapping) and isinstance(column.get("name"), str)
        ]
        group_columns = tuple(
            str(column)
            for column in query_result.get("group_by_columns", [])
            if isinstance(column, str)
        )
        if not columns:
            continue
        for row_index, row in enumerate(query_result.get("rows", [])):
            if not isinstance(row, (list, tuple)) or len(row) != len(columns):
                continue
            group_values = tuple(
                (column, row[columns.index(column)])
                for column in group_columns
                if column in columns
            )
            values.extend(
                _OutputValue(
                    metric=column,
                    value=row[column_index],
                    evidence_metric=f"row[{row_index}].{column}",
                    result_ref=f"query-result:{query_id}",
                    group_values=group_values,
                    source="query",
                    result_row_count=len(query_result.get("rows", [])),
                )
                for column_index, column in enumerate(columns)
            )
    for statistical_result in state.get("statistical_results", []):
        result_id = statistical_result.get("result_id")
        if not result_id:
            continue
        result_ref = f"statistical-result:{result_id}"
        for estimate in statistical_result.get("estimates", []):
            if isinstance(estimate, Mapping) and isinstance(estimate.get("metric"), str):
                values.append(
                    _OutputValue(
                        metric=estimate["metric"],
                        value=estimate.get("value"),
                        evidence_metric=estimate["metric"],
                        result_ref=result_ref,
                        source="statistical",
                    )
                )
        statistic_name = statistical_result.get("statistic_name")
        statistic = statistical_result.get("statistic")
        has_primary_statistic = (
            isinstance(statistic_name, str) and bool(statistic_name) and (statistic is not None)
        )
        if isinstance(statistic_name, str) and has_primary_statistic:
            values.append(
                _OutputValue(
                    metric=statistic_name,
                    value=statistic,
                    evidence_metric=statistic_name,
                    result_ref=result_ref,
                    source="statistical",
                )
            )
        if statistical_result.get("p_value") is not None and has_primary_statistic:
            values.append(
                _OutputValue(
                    metric="p_value",
                    value=statistical_result["p_value"],
                    evidence_metric="p_value",
                    result_ref=result_ref,
                    source="statistical",
                )
            )
            if statistical_result.get("adjusted_alpha") is not None:
                values.append(
                    _OutputValue(
                        metric="adjusted_alpha",
                        value=statistical_result["adjusted_alpha"],
                        evidence_metric="adjusted_alpha",
                        result_ref=result_ref,
                        source="statistical",
                    )
                )
            if statistical_result.get("statistically_significant") is not None:
                values.append(
                    _OutputValue(
                        metric="statistically_significant",
                        value=statistical_result["statistically_significant"],
                        evidence_metric="statistically_significant",
                        result_ref=result_ref,
                        source="statistical",
                    )
                )
        if isinstance(statistical_result.get("effect_size"), Mapping):
            effect = statistical_result["effect_size"]
            if isinstance(effect.get("metric"), str):
                values.append(
                    _OutputValue(
                        metric=effect["metric"],
                        value=effect.get("value"),
                        evidence_metric=effect["metric"],
                        result_ref=result_ref,
                        source="statistical",
                    )
                )
        if isinstance(statistical_result.get("confidence_interval"), Mapping):
            interval = statistical_result["confidence_interval"]
            for bound in ("lower", "upper"):
                if bound in interval:
                    metric = f"confidence_interval.{bound}"
                    values.append(
                        _OutputValue(
                            metric=metric,
                            value=interval[bound],
                            evidence_metric=metric,
                            result_ref=result_ref,
                            source="statistical",
                        )
                    )
    return values


def _calculation_matches(calculation: ExpectedCalculation, values: Sequence[_OutputValue]) -> bool:
    return any(
        _metric_matches(calculation, value)
        and _group_matches(calculation, value)
        and _value_matches(calculation.expected, value.value, calculation.absolute_tolerance)
        for value in values
    )


def _metric_matches(calculation: ExpectedCalculation, value: _OutputValue) -> bool:
    if value.source == "statistical":
        return _text_equal(calculation.metric, value.metric)
    if any(_text_equal(value.metric, field) for field, _ in value.group_values):
        # A GROUP BY label identifies the row; it is never the measured value.
        return False
    if calculation.group is not None or value.result_row_count == 1:
        # Query output names are model-chosen aliases, so a grouped row or a single-row result
        # is matched by its group and value rather than by guessing the alias.
        return True
    return any(
        _text_equal(metric, value.metric) for metric in (calculation.metric, *calculation.aliases)
    )


def _group_matches(calculation: ExpectedCalculation, value: _OutputValue) -> bool:
    if calculation.group is None:
        return True
    return any(
        _text_equal(calculation.group.field, field)
        and _scalar_equal(calculation.group.value, group_value)
        for field, group_value in value.group_values
    )


def _insight_covers_calculation(
    calculation: ExpectedCalculation,
    computed: Sequence[_OutputValue],
    state: AgentState,
) -> bool:
    """Require each expected output cell/estimate to be an assertion operand."""
    for insight in state.get("verified_insights", []):
        if not isinstance(insight, Mapping):
            continue
        assertion = insight.get("assertion")
        if not isinstance(assertion, Mapping):
            # Legacy persisted insights intentionally cannot establish coverage.
            continue
        assertion_metrics = {
            metric
            for key in ("left_metric", "right_metric")
            for metric in (assertion.get(key),)
            if isinstance(metric, str)
        }
        evidence = insight.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        result_refs = {
            action.get("output_ref")
            for action in state.get("tool_actions", [])
            if action.get("action_id") in evidence.get("tool_action_ids", [])
            and action.get("status") == "succeeded"
        }
        evidence_values = [
            (str(item["metric"]), item.get("value"))
            for item in evidence.get("values", [])
            if isinstance(item, Mapping) and isinstance(item.get("metric"), str)
        ]
        for value in computed:
            if value.result_ref not in result_refs:
                continue
            if not _calculation_matches(calculation, (value,)):
                continue
            if not any(_text_equal(metric, value.evidence_metric) for metric in assertion_metrics):
                continue
            if any(
                _text_equal(metric, value.evidence_metric)
                and _value_matches(
                    value.value,
                    evidence_value,
                    calculation.absolute_tolerance,
                )
                for metric, evidence_value in evidence_values
            ):
                return True
    return False


def _profile_fact_matches(fact: ExpectedProfileFact, state: AgentState) -> bool:
    actual = _profile_fact_value(fact, state)
    return actual is not _MISSING and _value_matches(
        fact.expected,
        actual,
        fact.absolute_tolerance,
    )


def _profile_fact_value(fact: ExpectedProfileFact, state: AgentState) -> object:
    profile = state.get("data_profile")
    if not isinstance(profile, Mapping):
        return _MISSING
    if fact.fact in {"field_names", "row_count", "duplicate_row_count"}:
        if fact.fact == "field_names":
            fields = profile.get("fields")
            if not isinstance(fields, (list, tuple)):
                return _MISSING
            names = [
                field.get("name")
                for field in fields
                if isinstance(field, Mapping) and isinstance(field.get("name"), str)
            ]
            return tuple(names) if len(names) == len(fields) else _MISSING
        return profile.get(fact.fact, _MISSING)

    fields = profile.get("fields")
    if not isinstance(fields, (list, tuple)) or fact.field is None:
        return _MISSING
    field = next(
        (
            item
            for item in fields
            if isinstance(item, Mapping)
            and isinstance(item.get("name"), str)
            and _text_equal(item["name"], fact.field)
        ),
        None,
    )
    if not isinstance(field, Mapping):
        return _MISSING
    if fact.fact == "field_kind":
        return field.get("kind", _MISSING)
    if fact.fact in {"missing_count", "missing_rate", "unique_count"}:
        return field.get(fact.fact, _MISSING)
    summary = field.get("numeric_summary")
    if not isinstance(summary, Mapping):
        return _MISSING
    return summary.get(fact.fact, _MISSING)


def _value_matches(expected: object, actual: object, tolerance: float) -> bool:
    if isinstance(expected, tuple):
        return (
            isinstance(actual, (list, tuple))
            and len(expected) == len(actual)
            and all(
                _value_matches(item, value, tolerance)
                for item, value in zip(expected, actual, strict=True)
            )
        )
    if isinstance(expected, bool) or not isinstance(expected, int | float):
        return _scalar_equal(expected, actual)
    return (
        isinstance(actual, int | float)
        and not isinstance(actual, bool)
        and abs(actual - expected) <= tolerance
    )


def _scalar_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _text_equal(left: str, right: str) -> bool:
    return (
        unicodedata.normalize("NFC", left).casefold()
        == unicodedata.normalize("NFC", right).casefold()
    )


def _crashed_result(case: GoldenCase, run_index: int, error: Exception) -> CaseRunResult:
    detail = f"{type(error).__name__}: {error}"
    return CaseRunResult(
        case_id=case.case_id,
        run_index=run_index,
        expected_outcome=case.expected_outcome,
        actual_outcome="crashed",
        status="crashed",
        provider_error=False,
        passed=False,
        checks=(_check("execution", False, detail),),
        model_calls=0,
        provider_requests=0,
        total_tokens=0,
        latency_ms=0,
        claims=(),
        error=detail,
    )


def _check(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


def _calculation_label(calculation: ExpectedCalculation) -> str:
    if calculation.group is None:
        return calculation.metric
    return f"{calculation.metric} where {calculation.group.field}={calculation.group.value}"


def _missing_detail(missing: Sequence[ExpectedCalculation]) -> str:
    labels = [_calculation_label(item) for item in missing]
    return f"missing: {', '.join(labels)}" if labels else "all expected values found"


def _missing_profile_detail(missing: Sequence[ExpectedProfileFact]) -> str:
    labels = [f"{item.fact}{f'[{item.field}]' if item.field else ''}" for item in missing]
    return f"missing: {', '.join(labels)}" if labels else "all expected profile facts found"


def _rate(values: Sequence[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _print_progress(result: CaseRunResult) -> None:
    mark = "PASS" if result.passed else ("PROVIDER" if result.provider_error else "FAIL")
    print(
        f"[{mark}] {result.case_id}#{result.run_index} outcome={result.actual_outcome} "
        f"calls={result.model_calls} tokens={result.total_tokens}",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
