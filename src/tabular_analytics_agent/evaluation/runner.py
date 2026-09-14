"""Run golden evaluation cases through the local application and grade them deterministically.

Usage: python -m tabular_analytics_agent.evaluation.runner --runs 3
"""

from __future__ import annotations

import argparse
import hashlib
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from tabular_analytics_agent.application import LocalAnalysisApplication
from tabular_analytics_agent.evaluation.models import (
    EvaluationModel,
    ExpectedCalculation,
    ExpectedOutcome,
    GoldenCase,
)
from tabular_analytics_agent.model_gateway import GeminiModelGateway, GeminiSettings
from tabular_analytics_agent.orchestration import AgentRunStatus, AgentState

_MAX_APPROVALS = 4
Scalar = str | int | float | bool | None


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
    passed: int
    provider_errors: int
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
    """Upload the case dataset, ask the question, approve any pause, and grade the result."""
    dataset = project_root / case.dataset_path
    content = dataset.read_bytes()
    if hashlib.sha256(content).hexdigest() != case.dataset_sha256:
        raise ValueError(f"dataset content does not match the hash recorded in {case.case_id}")
    workspace = application.ingest(application.stage_upload(dataset.name, content))
    state = application.start(workspace, case.user_request)
    asked_clarification = False
    for _ in range(_MAX_APPROVALS):
        status = state.get("status")
        if status == AgentRunStatus.AWAITING_SEMANTIC_REVIEW:
            asked_clarification = True
            state = application.resume(workspace, {"approved": True})
        elif status == AgentRunStatus.AWAITING_PLAN_APPROVAL:
            state = application.resume(workspace, True)
        else:
            break
    return grade_run(case, state, asked_clarification=asked_clarification, run_index=run_index)


def grade_run(
    case: GoldenCase,
    state: AgentState,
    *,
    asked_clarification: bool,
    run_index: int = 0,
) -> CaseRunResult:
    actual = _actual_outcome(state)
    claims = tuple(str(item.get("claim", "")) for item in state.get("verified_insights", []))
    forbidden = [
        text
        for text in case.forbidden_claims
        if any(text.casefold() in claim.casefold() for claim in claims)
    ]
    checks = [
        _check(
            "outcome",
            actual == case.expected_outcome.value,
            f"expected {case.expected_outcome.value}, got {actual}",
        ),
        _check(
            "clarification",
            asked_clarification == case.clarification_required,
            f"clarification requested: {asked_clarification}",
        ),
        _check(
            "forbidden_claims",
            not forbidden,
            f"forbidden text present: {forbidden}" if forbidden else "no forbidden claims",
        ),
    ]
    if case.expected_outcome is ExpectedOutcome.ANSWERED:
        checks.extend(_answer_checks(case, state))
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
    provider_retry_delay_seconds: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SuiteSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[CaseRunResult] = []

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
                    # Quota and overload errors are retried once after the rate window resets.
                    sleep(provider_retry_delay_seconds)
                    result = paced_run(case, run_index)
                results.append(result)
                log.write(result.model_dump_json() + "\n")
                log.flush()
                _print_progress(result)
    summary = summarize(results)
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
        outcome_accuracy=_rate(
            [result.actual_outcome == result.expected_outcome.value for result in graded]
        ),
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
        default=12.0,
        help="Requests per minute to pace to; keep headroom below the provider quota",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    load_dotenv()
    cases = tuple(
        case for case in load_cases(args.cases) if not args.case or case.case_id in args.case
    )
    output_dir: Path = args.output or Path(".eval") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    application = LocalAnalysisApplication(
        output_dir / "app-data",
        GeminiModelGateway(GeminiSettings.from_environment()),
    )
    summary = run_suite(
        application,
        cases,
        project_root=Path.cwd(),
        runs=args.runs,
        requests_per_minute=args.rpm,
        output_dir=output_dir,
    )
    print(summary.model_dump_json(indent=2))
    print(f"Results written to {output_dir}")
    return 0


def _actual_outcome(state: AgentState) -> str:
    if state.get("answered_from_profile"):
        return ExpectedOutcome.PROFILE.value
    if state.get("status") == AgentRunStatus.COMPLETED and state.get("verified_insights"):
        return ExpectedOutcome.ANSWERED.value
    return ExpectedOutcome.REFUSED.value


def _answer_checks(case: GoldenCase, state: AgentState) -> list[CheckResult]:
    computed = _computed_values(state)
    reported: list[Scalar] = [
        value["value"]
        for insight in state.get("verified_insights", [])
        for value in insight.get("evidence", {}).get("values", [])
    ]
    missing_computed = [
        item.metric for item in case.required_calculations if not _matches(item, computed)
    ]
    missing_reported = [
        item.metric for item in case.required_calculations if not _matches(item, reported)
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
        _check("insight_coverage", not missing_reported, _missing_detail(missing_reported)),
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


def _computed_values(state: AgentState) -> list[Scalar]:
    values: list[Scalar] = []
    for query_result in state.get("query_results", []):
        for row in query_result.get("rows", []):
            values.extend(row)
    for statistical_result in state.get("statistical_results", []):
        values.extend(estimate["value"] for estimate in statistical_result.get("estimates", []))
        for key in ("statistic", "p_value"):
            if statistical_result.get(key) is not None:
                values.append(statistical_result[key])
        if statistical_result.get("effect_size"):
            values.append(statistical_result["effect_size"]["value"])
        if statistical_result.get("confidence_interval"):
            interval = statistical_result["confidence_interval"]
            values.extend((interval["lower"], interval["upper"]))
    return values


def _matches(calculation: ExpectedCalculation, values: Sequence[Scalar]) -> bool:
    expected = calculation.expected
    if isinstance(expected, bool) or not isinstance(expected, int | float):
        return expected in values
    return any(
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and abs(value - expected) <= calculation.absolute_tolerance
        for value in values
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


def _missing_detail(missing: Sequence[str]) -> str:
    return f"missing: {', '.join(missing)}" if missing else "all expected values found"


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
