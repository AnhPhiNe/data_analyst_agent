"""Run golden evaluation cases through the local application; grading lives in `grading`.

Usage: python -m tabular_analytics_agent.evaluation.runner --runs 3
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from tabular_analytics_agent.application import (
    LocalAnalysisApplication,
    limits_from_environment,
    send_sample_values_from_environment,
)
from tabular_analytics_agent.evaluation.grading import (
    CaseRunResult,
    CheckResult,
    SuiteSummary,
    grade_run,
    summarize,
)
from tabular_analytics_agent.evaluation.models import GoldenCase
from tabular_analytics_agent.model_gateway import GeminiModelGateway, GeminiSettings
from tabular_analytics_agent.orchestration import AgentRunStatus

_MAX_PLAN_APPROVALS = 3


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
        checks=(CheckResult(name="execution", passed=False, detail=detail),),
        model_calls=0,
        provider_requests=0,
        total_tokens=0,
        latency_ms=0,
        claims=(),
        error=detail,
    )


def _print_progress(result: CaseRunResult) -> None:
    mark = "PASS" if result.passed else ("PROVIDER" if result.provider_error else "FAIL")
    print(
        f"[{mark}] {result.case_id}#{result.run_index} outcome={result.actual_outcome} "
        f"calls={result.model_calls} tokens={result.total_tokens}",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
