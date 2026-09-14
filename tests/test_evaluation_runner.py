"""Tests for deterministic evaluation grading and the suite loop."""

from __future__ import annotations

import json
from pathlib import Path

from tabular_analytics_agent.application import LocalAnalysisApplication
from tabular_analytics_agent.evaluation import ExpectedOutcome, GoldenCase
from tabular_analytics_agent.evaluation.runner import grade_run, load_cases, run_suite
from tabular_analytics_agent.model_gateway import FakeModelGateway, ModelProviderError

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "evaluation_cases"


def load_case(filename: str) -> GoldenCase:
    return GoldenCase.model_validate_json((CASES / filename).read_text(encoding="utf-8"))


def goal_output(**overrides: object) -> dict[str, object]:
    return {
        "goal_text": "Summarize revenue by region",
        "goal_family": "summary",
        "semantic_annotations": [],
        "clarification_question": None,
        **overrides,
    }


def test_every_committed_case_is_bound_to_its_dataset() -> None:
    cases = load_cases(CASES)

    assert cases
    assert len({case.case_id for case in cases}) == len(cases)
    assert all((ROOT / case.dataset_path).is_file() for case in cases)


def test_suite_runs_a_case_through_the_application_and_grades_it(tmp_path: Path) -> None:
    gateway = FakeModelGateway(
        [
            goal_output(),
            {
                "steps": [
                    {
                        "step_id": "totals",
                        "description": "Total revenue by region",
                        "expected_tool": "read_only_sql",
                        "required_fields": ["region", "revenue"],
                        "intended_output": "Regional revenue totals",
                        "caveats": [],
                        "requires_approval": False,
                    }
                ]
            },
            {
                "sql": (
                    "SELECT region, SUM(revenue) AS revenue FROM dataset "
                    "GROUP BY region ORDER BY region"
                )
            },
            {
                "insights": [
                    {
                        "plan_step_id": "totals",
                        "assertion": {
                            "operator": "greater_than",
                            "left_metric": "row[0].revenue",
                            "right_metric": "row[1].revenue",
                        },
                        "evidence_metrics": ["row[0].revenue", "row[1].revenue"],
                        "caveats": [],
                    }
                ]
            },
            {
                "artifact_type": "bar",
                "analytical_purpose": "Compare regional revenue",
                "x_field": "region",
                "y_fields": ["revenue"],
                "title": "Revenue by region",
            },
        ]
    )
    sleeps: list[float] = []

    summary = run_suite(
        LocalAnalysisApplication(tmp_path / "app-data", gateway),
        (load_case("tiny_sales_summary.json"),),
        project_root=ROOT,
        runs=1,
        requests_per_minute=60_000,
        output_dir=tmp_path / "eval",
        sleep=sleeps.append,
    )

    result = json.loads((tmp_path / "eval" / "results.jsonl").read_text(encoding="utf-8"))
    assert summary.passed == 1, summary.failures
    assert result["actual_outcome"] == "answered"
    assert result["provider_requests"] == 5
    assert {check["name"] for check in result["checks"]} == {
        "outcome",
        "forbidden_claims",
        "calculations",
        "insight_coverage",
        "schema_grounding",
        "chart",
    }
    assert (tmp_path / "eval" / "summary.json").is_file()
    assert sleeps == []


def test_clarification_pause_is_graded_without_confirming_the_hypothesis(tmp_path: Path) -> None:
    gateway = FakeModelGateway(
        [goal_output(clarification_question="There is no profit field. Use revenue?")]
    )

    summary = run_suite(
        LocalAnalysisApplication(tmp_path / "app-data", gateway),
        (load_case("tiny_missing_field_refusal.json"),),
        project_root=ROOT,
        runs=1,
        requests_per_minute=60_000,
        output_dir=tmp_path / "eval",
        sleep=lambda _: None,
    )

    assert summary.passed == 1, summary.failures
    assert len(gateway.requests) == 1


def test_provider_error_is_retried_once_after_the_rate_window(tmp_path: Path) -> None:
    gateway = FakeModelGateway(
        [ModelProviderError("quota exhausted"), goal_output(answer_from_profile=True)]
    )
    sleeps: list[float] = []

    summary = run_suite(
        LocalAnalysisApplication(tmp_path / "app-data", gateway),
        (load_case("tiny_columns_profile.json"),),
        project_root=ROOT,
        runs=1,
        requests_per_minute=60_000,
        output_dir=tmp_path / "eval",
        provider_retry_delay_seconds=60.0,
        sleep=sleeps.append,
    )

    assert summary.passed == 1, summary.failures
    assert summary.provider_errors == 0
    assert sleeps == [60.0]


def test_grading_separates_refusals_provider_errors_and_forbidden_claims() -> None:
    refusal_case = load_case("tiny_sales_summary.json").model_copy(
        update={"expected_outcome": ExpectedOutcome.REFUSED, "forbidden_claims": ("causes",)}
    )

    refused = grade_run(
        refusal_case,
        {"status": "failed", "error": "group too small", "error_kind": "analysis"},
    )
    provider = grade_run(
        load_case("tiny_sales_summary.json"),
        {"status": "failed", "error": "quota exhausted", "error_kind": "provider"},
    )
    overclaimed = grade_run(
        refusal_case,
        {
            "status": "completed",
            "verified_insights": [{"claim": "Region causes revenue", "evidence": {"values": []}}],
        },
    )

    assert refused.passed
    assert provider.provider_error
    assert not provider.passed
    assert {check.name for check in overclaimed.checks if not check.passed} == {
        "outcome",
        "forbidden_claims",
    }
