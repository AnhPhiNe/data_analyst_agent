"""Resource limits and execution budgets configured from environment variables."""

from __future__ import annotations

from pathlib import Path

import pytest

from tabular_analytics_agent.application import (
    ApplicationError,
    LocalAnalysisApplication,
    limit_variable,
    limits_from_environment,
    send_sample_values_from_environment,
)
from tabular_analytics_agent.data import DataCoreLimits, UnsafeFileError
from tabular_analytics_agent.domain import ExecutionBudget
from tabular_analytics_agent.model_gateway import FakeModelGateway


def _goal() -> dict[str, object]:
    return {
        "goal_text": "Compare total revenue by region",
        "goal_family": "comparison",
        "requested_metric_mappings": [],
        "semantic_annotations": [],
        "clarification_question": None,
    }


def _plan() -> dict[str, object]:
    return {
        "steps": [
            {
                "step_id": "regional-totals",
                "description": "Aggregate revenue by region",
                "expected_tool": "read_only_sql",
                "required_fields": ["region", "revenue"],
                "intended_output": "One total per region",
                "caveats": [],
                "requires_approval": True,
            }
        ]
    }


def _variables() -> list[str]:
    return [
        limit_variable(field_name)
        for model in (DataCoreLimits, ExecutionBudget)
        for field_name in model.model_fields
    ]


def test_unset_variables_keep_the_default_limits() -> None:
    assert limits_from_environment({}) == (DataCoreLimits(), ExecutionBudget())


def test_environment_overrides_data_limits_and_run_budget() -> None:
    limits, budget = limits_from_environment(
        {
            "TABULAR_AGENT_MAX_QUERY_ROWS": "500",
            "TABULAR_AGENT_MAX_XLSX_COMPRESSION_RATIO": "50.5",
            "TABULAR_AGENT_MAX_TOOL_ACTIONS": " 4 ",
            "TABULAR_AGENT_RUN_TIMEOUT_SECONDS": "120",
            "TABULAR_AGENT_MODEL_TIMEOUT_SECONDS": "45",
            "TABULAR_AGENT_MAX_REPAIRS_PER_ACTION": "",
        }
    )

    assert limits.max_query_rows == 500
    assert limits.max_xlsx_compression_ratio == 50.5
    assert limits.query_timeout_seconds == DataCoreLimits().query_timeout_seconds
    assert budget.max_tool_actions == 4
    assert budget.run_timeout_seconds == 120
    assert budget.model_call_timeout_seconds == 45
    assert budget.max_repairs_per_action == ExecutionBudget().max_repairs_per_action


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TABULAR_AGENT_MAX_TOOL_ACTIONS", "0"),
        ("TABULAR_AGENT_MAX_FILE_BYTES", "ten"),
        ("TABULAR_AGENT_MODEL_TIMEOUT_SECONDS", "30.5"),
    ],
)
def test_invalid_limit_error_names_the_variable(variable: str, value: str) -> None:
    with pytest.raises(ApplicationError, match=variable):
        limits_from_environment({variable: value})


def test_every_limit_has_a_distinct_documented_variable() -> None:
    variables = _variables()
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")

    assert len(set(variables)) == len(variables)
    assert [variable for variable in variables if f"{variable}=" not in example] == []


def test_application_applies_configured_limits_and_budget(tmp_path: Path) -> None:
    gateway = FakeModelGateway([_goal(), _plan()])
    application = LocalAnalysisApplication(
        tmp_path / "app-data",
        gateway,
        limits=DataCoreLimits(max_file_bytes=64),
        execution_budget=ExecutionBudget(model_call_timeout_seconds=25),
    )

    with pytest.raises(UnsafeFileError, match="64-byte"):
        application.stage_upload("large.csv", b"value\n" + b"1\n" * 64)
    workspace = application.ingest(
        application.stage_upload("sales.csv", b"region,revenue\nNorth,100\nSouth,150\n")
    )
    paused = application.start(workspace, "Compare total revenue by region")

    # The real clock spends a few microseconds of the budget before each call.
    timeouts = [request.timeout_seconds for request in gateway.requests]
    assert timeouts == pytest.approx([25.0, 25.0], abs=0.5)
    assert paused["plan"]["budget"]["model_call_timeout_seconds"] == 25
    assert application.sends_sample_values


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", True), ("true", True), (" Yes ", True), ("1", True), ("false", False), ("OFF", False)],
)
def test_sample_value_option_parses_true_and_false(value: str, expected: bool) -> None:
    environment = {"TABULAR_AGENT_SEND_SAMPLE_VALUES": value}

    assert send_sample_values_from_environment(environment) is expected
    assert send_sample_values_from_environment({}) is True


def test_invalid_sample_value_option_names_the_variable() -> None:
    with pytest.raises(ApplicationError, match="TABULAR_AGENT_SEND_SAMPLE_VALUES"):
        send_sample_values_from_environment({"TABULAR_AGENT_SEND_SAMPLE_VALUES": "maybe"})

    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "TABULAR_AGENT_SEND_SAMPLE_VALUES=" in example
