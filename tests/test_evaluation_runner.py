"""Tests for deterministic evaluation grading and the suite loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tabular_analytics_agent.application import LocalAnalysisApplication
from tabular_analytics_agent.evaluation import (
    ExpectedCalculation,
    ExpectedOutcome,
    ExpectedProfileFact,
    GoldenCase,
)
from tabular_analytics_agent.evaluation.runner import grade_run, load_cases, main, run_suite
from tabular_analytics_agent.model_gateway import FakeModelGateway, ModelProviderError
from tabular_analytics_agent.orchestration import AgentState

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "evaluation_cases"
HOLDOUT_CASES = ROOT / "tests" / "evaluation_cases_holdout"
CONTAMINATED_HOLDOUT_CASES = ROOT / "tests" / "evaluation_cases_holdout_contaminated"
HOLDOUT_V3_CASES = ROOT / "tests" / "evaluation_cases_holdout_v3"
HOLDOUT_V4_CASES = ROOT / "tests" / "evaluation_cases_holdout_v4"


def load_case(filename: str) -> GoldenCase:
    return GoldenCase.model_validate_json((CASES / filename).read_text(encoding="utf-8"))


def goal_output(**overrides: object) -> dict[str, object]:
    return {
        "goal_text": "Summarize revenue by region",
        "goal_family": "summary",
        "requested_metric_mappings": [],
        "semantic_annotations": [],
        "clarification_question": None,
        **overrides,
    }


def grouped_query_state(
    rows: list[list[object]],
    *,
    metric: str = "revenue",
    asserted_metrics: tuple[str, ...] = ("row[0].revenue", "row[1].revenue"),
    evidence_metrics: tuple[str, ...] = ("row[0].revenue", "row[1].revenue"),
) -> AgentState:
    columns = ["region", "revenue"]
    if metric == "units":
        columns = ["region", "revenue", "units"]
    evidence_values = []
    for reference in evidence_metrics:
        row_index, column = reference.removeprefix("row[").split("].", maxsplit=1)
        value = rows[int(row_index)][columns.index(column)]
        evidence_values.append({"metric": reference, "value": value})
    assertion = {
        "operator": "greater_than",
        "left_metric": asserted_metrics[0],
        "right_metric": asserted_metrics[1],
    }
    return {
        "status": "completed",
        "tool_actions": [
            {"action_id": "action-1", "status": "succeeded", "output_ref": "query-result:query-1"}
        ],
        "query_results": [
            {
                "query_id": "query-1",
                "columns": [{"name": column} for column in columns],
                "rows": rows,
                "group_by_columns": ["region"],
            }
        ],
        "verified_insights": [
            {
                "claim": "structured test claim",
                "assertion": assertion,
                "evidence": {"values": evidence_values, "tool_action_ids": ["action-1"]},
            }
        ],
    }


def test_every_committed_case_is_bound_to_its_dataset() -> None:
    development = load_cases(CASES)
    # Cases whose exact questions were tried manually are reported apart from clean holdouts.
    holdout = (*load_cases(HOLDOUT_CASES), *load_cases(CONTAMINATED_HOLDOUT_CASES))
    holdout_v3 = load_cases(HOLDOUT_V3_CASES)
    holdout_v4 = load_cases(HOLDOUT_V4_CASES)
    cases = (*development, *holdout, *holdout_v3, *holdout_v4)

    assert holdout_v3
    assert holdout_v4
    # Each holdout set's datasets were unseen by every earlier case set when it was committed.
    earlier_datasets = {case.dataset_path for case in (*development, *holdout)}
    assert not earlier_datasets & {case.dataset_path for case in holdout_v3}
    earlier_datasets |= {case.dataset_path for case in holdout_v3}
    assert not earlier_datasets & {case.dataset_path for case in holdout_v4}

    assert development
    assert load_cases(HOLDOUT_CASES)
    assert load_cases(CONTAMINATED_HOLDOUT_CASES)
    assert len({case.case_id for case in cases}) == len(cases)
    for case in cases:
        content = (ROOT / case.dataset_path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == case.dataset_sha256, case.case_id
    # Holdout datasets stay unseen by development cases.
    assert not {case.dataset_path for case in development} & {case.dataset_path for case in holdout}


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
    assert summary.provider_retries == 1
    assert sleeps == [60.0]


def test_query_grading_rejects_swapped_group_values() -> None:
    case = load_case("tiny_sales_summary.json")
    result = grade_run(
        case,
        grouped_query_state(
            [["North", 200.0], ["South", 350.0]],
        ),
    )

    assert not result.passed
    assert {check.name for check in result.checks if not check.passed} >= {
        "calculations",
        "insight_coverage",
    }


def test_query_grading_accepts_a_grouped_value_under_an_unlisted_alias() -> None:
    case = load_case("tiny_sales_summary.json")
    result = grade_run(
        case,
        grouped_query_state(
            [["North", 10.0, 350.0], ["South", 20.0, 200.0]],
            metric="units",
            asserted_metrics=("row[0].units", "row[1].units"),
            evidence_metrics=("row[0].units", "row[1].units"),
        ),
    )

    # Output names are model-chosen aliases; the group row and the value are what is graded.
    assert next(check for check in result.checks if check.name == "calculations").passed
    assert next(check for check in result.checks if check.name == "insight_coverage").passed


def query_only_state(
    columns: list[str], rows: list[list[object]], group_by: list[str]
) -> AgentState:
    return {
        "status": "completed",
        "tool_actions": [
            {"action_id": "action-1", "status": "succeeded", "output_ref": "query-result:query-1"}
        ],
        "query_results": [
            {
                "query_id": "query-1",
                "columns": [{"name": column} for column in columns],
                "rows": rows,
                "group_by_columns": group_by,
            }
        ],
        "verified_insights": [],
    }


def test_group_label_column_never_counts_as_the_measured_value() -> None:
    case = load_case("tiny_sales_summary.json").model_copy(
        update={
            "required_calculations": (
                ExpectedCalculation.model_validate(
                    {"metric": "count", "expected": 2, "group": {"field": "month", "value": 2}}
                ),
            )
        }
    )
    state = query_only_state(["month", "count"], [[2, 5], [3, 7]], ["month"])

    result = grade_run(case, state)

    assert not next(check for check in result.checks if check.name == "calculations").passed


def test_ungrouped_value_needs_a_single_row_or_the_named_metric() -> None:
    case = load_case("tiny_sales_summary.json").model_copy(
        update={"required_calculations": (ExpectedCalculation(metric="total", expected=7),)}
    )

    listing = grade_run(case, query_only_state(["month", "count"], [[2, 5], [3, 7]], []))
    single_row = grade_run(case, query_only_state(["sum_1"], [[7]], []))
    named = grade_run(case, query_only_state(["month", "total"], [[2, 5], [3, 7]], []))

    assert not next(check for check in listing.checks if check.name == "calculations").passed
    assert next(check for check in single_row.checks if check.name == "calculations").passed
    assert next(check for check in named.checks if check.name == "calculations").passed


@pytest.mark.parametrize(
    "assertion",
    [
        None,
        {"operator": "reports", "left_metric": "row[0].region"},
    ],
)
def test_insight_coverage_requires_assertion_operands(assertion: object) -> None:
    case = load_case("tiny_sales_summary.json")
    state = grouped_query_state(
        [["North", 350.0], ["South", 200.0]],
    )
    state["verified_insights"][0]["assertion"] = assertion

    result = grade_run(case, state)

    assert not result.passed
    assert not next(check for check in result.checks if check.name == "insight_coverage").passed


def test_profile_grading_uses_deterministic_facts_not_only_profile_flag() -> None:
    case = load_case("tiny_columns_profile.json")
    result = grade_run(
        case,
        {
            "status": "completed",
            "answered_from_profile": True,
            "data_profile": {
                "fields": [
                    {"name": "order_id"},
                    {"name": "region"},
                    {"name": "units"},
                ]
            },
        },
    )

    assert result.actual_outcome == "profile"
    assert not result.passed
    assert not next(check for check in result.checks if check.name == "profile_facts").passed


@pytest.mark.parametrize("output_ref", ["query-result:other-query", None])
def test_insight_coverage_requires_its_own_result(output_ref: str | None) -> None:
    state = grouped_query_state([["North", 350.0], ["South", 200.0]])
    case = load_case("tiny_sales_summary.json")
    assert next(c for c in grade_run(case, state).checks if c.name == "insight_coverage").passed
    state["tool_actions"][0]["output_ref"] = output_ref
    result = grade_run(case, state)
    assert next(c for c in result.checks if c.name == "calculations").passed
    assert not next(c for c in result.checks if c.name == "insight_coverage").passed


def test_group_labels_are_case_sensitive() -> None:
    state = grouped_query_state([["north", 350.0], ["South", 200.0]])
    result = grade_run(load_case("tiny_sales_summary.json"), state)
    assert not next(c for c in result.checks if c.name == "calculations").passed


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("spearman_rho", 0.9),
        ("pearson_r", 0.8),
        ("p_value", 0.01),
        ("adjusted_alpha", 0.05),
        ("statistically_significant", True),
        ("cohen_d", 0.6),
        ("confidence_interval.lower", 0.4),
        ("confidence_interval.upper", 0.95),
    ],
)
def test_statistical_grading_binds_metric_and_assertion(metric: str, expected: Any) -> None:
    case = load_case("tiny_sales_summary.json").model_copy(
        update={
            "required_calculations": (ExpectedCalculation(metric=metric, expected=expected),),
            "valid_chart_types": (),
        }
    )
    state: AgentState = {
        "status": "completed",
        "statistical_results": [
            {
                "result_id": "stats-1",
                "estimates": [{"metric": "spearman_rho", "value": 0.9}],
                "statistic_name": "pearson_r",
                "statistic": 0.8,
                "p_value": 0.01,
                "adjusted_alpha": 0.05,
                "statistically_significant": True,
                "effect_size": {"metric": "cohen_d", "value": 0.6},
                "confidence_interval": {"lower": 0.4, "upper": 0.95},
            }
        ],
        "tool_actions": [
            {
                "action_id": "stats-action",
                "status": "succeeded",
                "output_ref": "statistical-result:stats-1",
            }
        ],
        "verified_insights": [
            {
                "claim": "test statistic",
                "assertion": {"operator": "reports", "left_metric": metric},
                "evidence": {
                    "tool_action_ids": ["stats-action"],
                    "values": [{"metric": metric, "value": expected}],
                },
            }
        ],
    }
    assert grade_run(case, state).passed
    wrong_metric = case.model_copy(
        update={
            "required_calculations": (
                ExpectedCalculation(
                    metric="unrelated_metric", aliases=(metric,), expected=expected
                ),
            ),
        }
    )
    assert not grade_run(wrong_metric, state).passed
    state["tool_actions"][0]["output_ref"] = "statistical-result:other"
    assert not grade_run(case, state).passed


@pytest.mark.parametrize(
    "fact_data",
    [
        {"fact": "field_kind", "field": "revenue", "expected": "numeric"},
        {"fact": "missing_count", "field": "revenue", "expected": 0},
        {"fact": "mean", "field": "revenue", "expected": 110.0},
        {"fact": "row_count", "expected": 5},
    ],
)
def test_profile_fact_reads_correct_scope(fact_data: dict[str, Any]) -> None:
    case = load_case("tiny_columns_profile.json").model_copy(
        update={
            "required_profile_facts": (ExpectedProfileFact.model_validate(fact_data),),
        }
    )
    state: AgentState = {
        "status": "completed",
        "answered_from_profile": True,
        "data_profile": {
            "row_count": 5,
            "fields": [
                {
                    "name": "revenue",
                    "kind": "numeric",
                    "missing_count": 0,
                    "numeric_summary": {"mean": 110.0},
                }
            ],
        },
    }
    assert grade_run(case, state).passed
    state["data_profile"] = {"row_count": True, "fields": [{"name": "revenue"}]}
    assert not grade_run(case, state).passed


def test_numeric_group_does_not_match_boolean() -> None:
    state = grouped_query_state([[True, 350.0], ["South", 200.0]])
    case = load_case("tiny_sales_summary.json")
    calculation = case.required_calculations[0].model_dump()
    calculation["group"] = {"field": "region", "value": 1}
    case = case.model_copy(
        update={
            "required_calculations": (ExpectedCalculation.model_validate(calculation),),
        }
    )
    assert not next(c for c in grade_run(case, state).checks if c.name == "calculations").passed


def test_failed_run_cannot_reuse_profile_success_flag() -> None:
    result = grade_run(
        load_case("tiny_columns_profile.json"),
        {"status": "failed", "answered_from_profile": True, "data_profile": {"row_count": 5}},
    )
    assert result.actual_outcome == "failed"
    assert not result.passed


def test_grading_separates_refusals_provider_errors_and_forbidden_claims() -> None:
    refusal_case = load_case("tiny_sales_summary.json").model_copy(
        update={"expected_outcome": ExpectedOutcome.REFUSED, "forbidden_claims": ("causes",)}
    )

    refused = grade_run(
        refusal_case,
        {
            "status": "completed",
            "unsupported_claims": [
                {
                    "status": "unsupported",
                    "verification": {"status": "failed"},
                }
            ],
        },
    )
    failed = grade_run(
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

    assert refused.actual_outcome == "failed"
    assert not refused.passed
    assert failed.actual_outcome == "failed"
    assert not failed.passed
    assert provider.provider_error
    assert not provider.passed
    assert {check.name for check in overclaimed.checks if not check.passed} == {
        "outcome",
        "forbidden_claims",
    }


@pytest.mark.parametrize(
    "argv",
    [["--runs", "0"], ["--runs", "-1"], ["--rpm", "0"], ["--rpm", "nan"]],
)
def test_cli_rejects_invalid_pacing_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        main(argv)


def test_cli_rejects_empty_case_selection() -> None:
    with pytest.raises(SystemExit):
        main(["--case", "does-not-exist"])
