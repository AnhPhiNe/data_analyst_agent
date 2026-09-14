"""Focused tests for typed requested-metric grounding and refusal boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from tabular_analytics_agent.data import TabularDataCore
from tabular_analytics_agent.domain import GoalFamily
from tabular_analytics_agent.evaluation.models import ExpectedOutcome, GoldenCase
from tabular_analytics_agent.evaluation.runner import _actual_outcome, grade_run
from tabular_analytics_agent.model_gateway import (
    FakeModelGateway,
    RequestedMetricMapping,
    RequestedMetricStatus,
)
from tabular_analytics_agent.model_gateway.schemas import GoalInterpretation
from tabular_analytics_agent.orchestration import (
    AgentOrchestrator,
    AgentRunRequest,
    AgentRunStatus,
    AgentState,
)


def _request(tmp_path: Path, user_request: str = "Compare sales by region") -> AgentRunRequest:
    core = TabularDataCore(tmp_path / "session-data")
    dataset = tmp_path / "sales.csv"
    dataset.write_text(
        "region,revenue,units\nNorth,100,2\nSouth,150,3\nNorth,50,1\n",
        encoding="utf-8",
        newline="",
    )
    handle = core.ingest(dataset)
    return AgentRunRequest(
        session_id="semantic-grounding-session",
        user_request=user_request,
        dataset_handle=handle,
        data_profile=core.profile(handle),
    )


def _goal_output(
    mappings: list[dict[str, Any]],
    *,
    clarification_question: str | None = None,
    semantic_annotations: list[dict[str, Any]] | None = None,
    answer_from_profile: bool = False,
) -> dict[str, object]:
    return {
        "goal_text": "Compare sales by region",
        "goal_family": "comparison",
        "requested_metric_mappings": mappings,
        "semantic_annotations": semantic_annotations or [],
        "clarification_question": clarification_question,
        "answer_from_profile": answer_from_profile,
    }


def test_goal_generation_requires_explicit_metric_mappings() -> None:
    payload = _goal_output([])
    payload.pop("requested_metric_mappings")

    with pytest.raises(ValidationError):
        GoalInterpretation.model_validate(payload)

    explicit_empty = GoalInterpretation.model_validate(_goal_output([]))
    assert explicit_empty.requested_metric_mappings == ()


@pytest.mark.parametrize(
    ("mapping", "expected_error"),
    [
        pytest.param(
            {"requested_label": "sales", "status": "direct"},
            "direct metric mappings require source_fields",
            id="direct-requires-fields",
        ),
        pytest.param(
            {
                "requested_label": "sales",
                "status": "direct",
                "source_fields": [" "],
            },
            "source_fields must contain non-empty field names",
            id="fields-must-be-nonempty",
        ),
        pytest.param(
            {
                "requested_label": "sales",
                "status": "direct",
                "source_fields": ["revenue", "REVENUE"],
            },
            "source_fields must be unique",
            id="fields-must-be-unique",
        ),
        pytest.param(
            {
                "requested_label": "sales",
                "status": "direct",
                "source_fields": ["revenue"],
                "derivation": "SUM(revenue)",
            },
            "direct metric mappings cannot declare a derivation",
            id="direct-has-no-derivation",
        ),
        pytest.param(
            {
                "requested_label": "sales",
                "status": "direct",
                "source_fields": ["revenue"],
                "reason": "No safe mapping is available.",
            },
            "only unavailable metric mappings may declare a reason",
            id="direct-has-no-unavailable-reason",
        ),
        pytest.param(
            {
                "requested_label": "average order value",
                "status": "derived",
                "derivation": "SUM(revenue) / SUM(units)",
            },
            "derived metric mappings require source_fields",
            id="derived-requires-fields",
        ),
        pytest.param(
            {
                "requested_label": "average order value",
                "status": "derived",
                "source_fields": ["revenue", "units"],
            },
            "derived metric mappings require an explicit derivation",
            id="derived-requires-derivation",
        ),
        pytest.param(
            {
                "requested_label": "average order value",
                "status": "derived",
                "source_fields": ["revenue", "units"],
                "derivation": "SUM(revenue) / SUM(units)",
                "reason": "No safe mapping is available.",
            },
            "only unavailable metric mappings may declare a reason",
            id="derived-has-no-unavailable-reason",
        ),
        pytest.param(
            {
                "requested_label": "profit",
                "status": "unavailable",
                "source_fields": ["revenue"],
            },
            "unavailable metric mappings cannot reference source fields",
            id="unavailable-has-no-source-fields",
        ),
        pytest.param(
            {
                "requested_label": "profit",
                "status": "unavailable",
                "derivation": "SUM(revenue)",
            },
            "unavailable metric mappings cannot reference source fields",
            id="unavailable-has-no-derivation",
        ),
    ],
)
def test_requested_metric_mapping_rejects_invalid_contracts(
    mapping: dict[str, object], expected_error: str
) -> None:
    with pytest.raises(ValidationError, match=expected_error):
        RequestedMetricMapping.model_validate(mapping)


def test_goal_interpretation_rejects_duplicate_requested_metric_labels() -> None:
    payload = _goal_output(
        [
            {"requested_label": "sales", "status": "direct", "source_fields": ["revenue"]},
            {"requested_label": "Sales", "status": "direct", "source_fields": ["revenue"]},
        ]
    )

    with pytest.raises(ValidationError, match="requested metric mapping labels must be unique"):
        GoalInterpretation.model_validate(payload)


def _plan_output(required_fields: list[str]) -> dict[str, object]:
    return {
        "steps": [
            {
                "step_id": "metric-by-region",
                "description": "Compare the requested metric by region",
                "expected_tool": "read_only_sql",
                "required_fields": required_fields,
                "intended_output": "A grouped comparison",
                "caveats": [],
                "requires_approval": True,
            }
        ]
    }


@pytest.mark.parametrize("requested_label", ["sales", "ventas"])
def test_direct_synonym_mapping_is_preserved_in_state(tmp_path: Path, requested_label: str) -> None:
    request = _request(tmp_path, f"Compare {requested_label} by region")
    gateway = FakeModelGateway(
        [
            _goal_output(
                [
                    {
                        "requested_label": requested_label,
                        "status": "direct",
                        "source_fields": ["revenue"],
                    }
                ]
            ),
            _plan_output(["region", "revenue"]),
        ]
    )

    agent = AgentOrchestrator(gateway, _core_for_request(request), checkpointer=InMemorySaver())
    paused = agent.start(request)

    assert paused["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL
    assert paused["requested_metric_mappings"] == [
        {
            "requested_label": requested_label,
            "status": "direct",
            "source_fields": ["revenue"],
            "derivation": None,
            "reason": None,
        }
    ]


def _core_for_request(request: AgentRunRequest) -> TabularDataCore:
    """Return the data core rooted at the handle's session data directory."""
    # TabularDataCore stores the root on the handle's source path; this helper is only used to
    # avoid creating a second dataset in a different namespace during the pause test.
    return TabularDataCore(request.dataset_handle.source_path.parent.parent)


def test_legitimate_derived_metric_requires_all_source_fields_and_derivation(
    tmp_path: Path,
) -> None:
    mapping = RequestedMetricMapping(
        requested_label="average order value",
        status=RequestedMetricStatus.DERIVED,
        source_fields=("revenue", "units"),
        derivation="SUM(revenue) / SUM(units)",
    )
    assert mapping.status is RequestedMetricStatus.DERIVED
    assert mapping.source_fields == ("revenue", "units")
    assert mapping.derivation == "SUM(revenue) / SUM(units)"

    request = _request(tmp_path, "Compare average order value by region")
    core = _core_for_request(request)
    gateway = FakeModelGateway(
        [
            _goal_output(
                [mapping.model_dump(mode="json")],
                answer_from_profile=True,
            ),
            _plan_output(["region", "revenue", "units"]),
        ]
    )
    paused = AgentOrchestrator(gateway, core, checkpointer=InMemorySaver()).start(request)

    assert paused["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL
    assert paused["answered_from_profile"] is False
    assert paused["requested_metric_mappings"][0]["status"] == "derived"
    assert paused["plan"]["steps"][0]["required_fields"] == ["region", "revenue", "units"]


@pytest.mark.parametrize("metric", ["profit", "cost"])
def test_unavailable_metric_is_clarified_before_explicit_refusal(
    tmp_path: Path, metric: str
) -> None:
    request = _request(tmp_path, f"What is {metric} by region?")
    core = _core_for_request(request)
    gateway = FakeModelGateway(
        [
            _goal_output(
                [
                    {
                        "requested_label": metric,
                        "status": "unavailable",
                        "source_fields": [],
                        "reason": f"No {metric} field or safe derivation is available.",
                    }
                ],
                answer_from_profile=True,
                clarification_question="Can I use revenue instead?",
            )
        ]
    )
    agent = AgentOrchestrator(gateway, core, checkpointer=InMemorySaver())

    clarification = agent.start(request)
    assert clarification["status"] == AgentRunStatus.AWAITING_SEMANTIC_REVIEW
    assert clarification["clarification_question"] == (
        f"The requested metric {metric} is unavailable from the dataset. "
        "Would you like to correct the request or confirm that it cannot be answered?"
    )
    assert clarification["refusal_reason"] == ""
    assert clarification["answered_from_profile"] is False
    assert len(gateway.requests) == 1

    refused = agent.resume(request.session_id, True)
    assert refused["status"] == AgentRunStatus.REFUSED
    assert refused["refusal_code"] == "unavailable_metric"
    assert refused["refusal_reason"] == f"No {metric} field or safe derivation is available."
    assert refused["tool_actions"] == []
    assert len(gateway.requests) == 1


def test_corrected_request_reinterprets_old_unavailable_mapping(tmp_path: Path) -> None:
    request = _request(tmp_path, "What is profit by region?")
    core = _core_for_request(request)
    gateway = FakeModelGateway(
        [
            _goal_output(
                [
                    {
                        "requested_label": "profit",
                        "status": "unavailable",
                        "source_fields": [],
                    }
                ],
                clarification_question=(
                    "Profit is unavailable. Correct the metric or confirm inability."
                ),
            ),
            _goal_output(
                [
                    {
                        "requested_label": "revenue",
                        "status": "direct",
                        "source_fields": ["revenue"],
                    }
                ]
            ),
            _plan_output(["region", "revenue"]),
        ]
    )
    agent = AgentOrchestrator(gateway, core, checkpointer=InMemorySaver())

    agent.start(request)
    revised = agent.resume(
        request.session_id,
        {"approved": False, "corrected_request": "What is revenue by region?"},
    )

    assert revised["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL
    assert revised["requested_metric_mappings"] == [
        {
            "requested_label": "revenue",
            "status": "direct",
            "source_fields": ["revenue"],
            "derivation": None,
            "reason": None,
        }
    ]
    assert len(gateway.requests) == 3


def test_plan_cannot_bypass_mapping_but_can_include_dimensions(tmp_path: Path) -> None:
    request = _request(tmp_path)
    core = _core_for_request(request)
    mapping = {
        "requested_label": "sales",
        "status": "direct",
        "source_fields": ["revenue"],
    }
    bypass_gateway = FakeModelGateway([_goal_output([mapping]), _plan_output(["region"])])
    failed = AgentOrchestrator(bypass_gateway, core, checkpointer=InMemorySaver()).start(request)

    assert failed["status"] == AgentRunStatus.FAILED
    assert "source fields" in failed["error"]
    assert failed["tool_actions"] == []


def test_invalid_mapping_source_fails_closed_without_crashing(tmp_path: Path) -> None:
    request = _request(tmp_path)
    core = _core_for_request(request)
    gateway = FakeModelGateway(
        [
            _goal_output(
                [
                    {
                        "requested_label": "sales",
                        "status": "direct",
                        "source_fields": ["not_a_column"],
                    }
                ]
            )
        ]
    )

    failed = AgentOrchestrator(gateway, core, checkpointer=InMemorySaver()).start(request)

    assert failed["status"] == AgentRunStatus.FAILED
    assert "Unknown fields requested" in failed["error"]
    assert len(gateway.requests) == 1


def test_grader_recognizes_only_typed_refusal_and_keeps_crash_failed() -> None:
    case = GoldenCase(
        case_id="typed-refusal",
        dataset_path="unused.csv",
        dataset_sha256="0" * 64,
        user_request="What is profit?",
        goal_family=GoalFamily.SUMMARY,
        expected_outcome=ExpectedOutcome.REFUSED,
    )
    typed: AgentState = {
        "status": "refused",
        "refusal_code": "unavailable_metric",
        "refusal_reason": "Profit is unavailable.",
        "requested_metric_mappings": [
            {"requested_label": "profit", "status": "unavailable", "source_fields": []}
        ],
    }
    crashed: AgentState = {"status": "crashed", "error": "RuntimeError"}
    legacy_failed: AgentState = {
        "status": "failed",
        "error": "Unsupported statistical request",
    }
    legacy_refusal: AgentState = {"status": "refused"}
    profile_answer: AgentState = {**typed, "answered_from_profile": True}
    verified_answer: AgentState = {
        **typed,
        "verified_insights": [{"claim": "Profit was 12."}],
    }

    assert _actual_outcome(typed) == ExpectedOutcome.REFUSED
    assert grade_run(case, typed).passed
    assert _actual_outcome(crashed) == ExpectedOutcome.FAILED
    assert not grade_run(case, crashed).passed
    assert _actual_outcome(legacy_failed) == ExpectedOutcome.FAILED
    assert _actual_outcome(legacy_refusal) == ExpectedOutcome.FAILED
    assert _actual_outcome(profile_answer) == ExpectedOutcome.FAILED
    assert _actual_outcome(verified_answer) == ExpectedOutcome.FAILED

    sample_refusal: AgentState = {
        "status": "refused",
        "refusal_code": "insufficient_sample",
        "refusal_reason": "Group South has only 2 usable values.",
    }
    unknown_code: AgentState = {**sample_refusal, "refusal_code": "not_a_refusal_code"}
    assert _actual_outcome(sample_refusal) == ExpectedOutcome.REFUSED
    assert _actual_outcome({**sample_refusal, "refusal_reason": ""}) == ExpectedOutcome.FAILED
    assert _actual_outcome(unknown_code) == ExpectedOutcome.FAILED


def test_legacy_state_without_metric_mappings_is_not_a_refusal() -> None:
    assert _actual_outcome({"status": "failed", "error": "old checkpoint failure"}) == (
        ExpectedOutcome.FAILED
    )
