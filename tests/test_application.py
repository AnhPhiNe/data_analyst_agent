"""Integration tests for the local application workflow behind Streamlit."""

from __future__ import annotations

import copy
from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.application import ApplicationError, LocalAnalysisApplication
from tabular_analytics_agent.application.service import _require_current_query_evidence
from tabular_analytics_agent.data import (
    QueryResult,
    TabularDataCore,
    UnsafeFileError,
    UnsupportedFileError,
)
from tabular_analytics_agent.domain import ArtifactStatus, ArtifactType, SessionStatus, ToolAction
from tabular_analytics_agent.model_gateway import FakeModelGateway, ModelTask
from tabular_analytics_agent.orchestration import AgentRunStatus, ApprovalDecision


def model_outputs() -> list[dict[str, object]]:
    return [
        {
            "goal_text": "Compare total revenue by region",
            "goal_family": "comparison",
            "requested_metric_mappings": [],
            "semantic_annotations": [],
            "clarification_question": None,
        },
        {
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
        },
        {
            "sql": (
                "SELECT region, SUM(revenue) AS revenue FROM dataset "
                "GROUP BY region ORDER BY region"
            ),
        },
        {
            "insights": [
                {
                    "plan_step_id": "regional-totals",
                    "assertion": {
                        "operator": "equals",
                        "left_metric": "row[0].revenue",
                        "right_metric": "row[1].revenue",
                    },
                    "evidence_metrics": [
                        "row[0].region",
                        "row[0].revenue",
                        "row[1].region",
                        "row[1].revenue",
                    ],
                    "caveats": ["Totals cover the uploaded rows."],
                }
            ]
        },
        {
            "artifact_type": "bar",
            "analytical_purpose": "Compare regional totals",
            "x_field": "region",
            "y_fields": ["revenue"],
            "color_field": None,
            "aggregation": None,
            "title": "Revenue by region",
            "labels": {"region": "Region", "revenue": "Revenue"},
            "formatting_intent": {"show_legend": False},
            "validation_constraints": ["Use verified values only"],
        },
    ]


def semantic_model_outputs() -> list[dict[str, object]]:
    outputs = model_outputs()
    outputs[0] = {
        **outputs[0],
        "semantic_annotations": [
            {
                "field_name": "revenue",
                "meaning": "Gross sales",
                "unit": "USD",
                "role": "measure",
            }
        ],
        "clarification_question": "Should revenue be treated as gross sales in USD?",
    }
    return outputs


def sales_csv() -> bytes:
    return b"region,revenue\nNorth,100\nSouth,150\nNorth,50\n"


def test_end_to_end_application_publishes_and_pins_dashboard_artifact(
    tmp_path: Path,
) -> None:
    gateway = FakeModelGateway(model_outputs())
    application = LocalAnalysisApplication(tmp_path / "app-data", gateway)
    staged = application.stage_upload("../../sales.csv", sales_csv())
    assert staged.upload_path.name == "sales.csv"

    workspace = application.ingest(staged)
    paused = application.start(workspace, "Compare total revenue by region")
    assert paused["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL

    completed = application.resume(workspace, ApprovalDecision(approved=True))
    assert completed["status"] == AgentRunStatus.COMPLETED
    assert completed["artifact_error"] == ""
    assert gateway.requests[-1].task is ModelTask.CHART_INTENT

    first_publication = application.publish_candidates(workspace, completed)
    saved_at = application.load_workspace(workspace.session.session_id).session.updated_at
    repeated_publication = application.publish_candidates(workspace, completed)
    # Showing the same result again does not rewrite the session or reorder the session list.
    assert application.load_workspace(workspace.session.session_id).session.updated_at == saved_at
    assert len(first_publication) == 1
    assert repeated_publication == first_publication
    candidate = first_publication[0]
    assert candidate.status is ArtifactStatus.CANDIDATE
    assert candidate.insight_ids

    pinned = application.pin(workspace, candidate.artifact_id)
    assert application.list_candidates(workspace) == ()
    assert application.list_dashboard(workspace) == (pinned,)
    unpinned = application.unpin(workspace, candidate.artifact_id)
    assert unpinned.status is ArtifactStatus.CANDIDATE
    pinned = application.pin(workspace, candidate.artifact_id)

    restarted = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    restored = restarted.load_workspace(workspace.session.session_id)
    assert restored.session.status is SessionStatus.COMPLETED
    assert restarted.get_state(restored)["status"] == AgentRunStatus.COMPLETED
    assert restarted.list_dashboard(restored) == (pinned,)
    assert restarted.read_render_spec(pinned)["plotly_spec"]["data"][0]["type"] == "bar"


def test_confirmed_semantics_survive_chart_pin_and_reopen(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(
        tmp_path / "app-data",
        FakeModelGateway(semantic_model_outputs()),
    )
    staged = application.stage_upload("sales.csv", sales_csv())
    workspace = application.ingest(staged)

    semantic_pause = application.start(workspace, "Compare total revenue by region")
    assert semantic_pause["status"] == AgentRunStatus.AWAITING_SEMANTIC_REVIEW
    plan_pause = application.resume(
        workspace,
        {
            "approved": True,
            "annotations": [
                {
                    "field_name": "revenue",
                    "meaning": "Gross sales",
                    "unit": "USD",
                    "role": "measure",
                }
            ],
        },
    )
    assert plan_pause["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL

    completed = application.resume(workspace, True)
    assert completed["status"] == AgentRunStatus.COMPLETED
    assert completed["artifact_error"] == ""
    candidate = application.publish_candidates(workspace, completed)[0]
    current_workspace = application.load_workspace(workspace.session.session_id)
    pinned = application.pin(current_workspace, candidate.artifact_id)

    restarted = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    restored = restarted.load_workspace(workspace.session.session_id)

    assert restored.session.semantic_annotations[0].meaning == "Gross sales"
    assert restarted.list_dashboard(restored) == (pinned,)
    assert restarted.read_render_spec(pinned)["plotly_spec"]["data"][0]["type"] == "bar"


def test_upload_staging_rejects_unsupported_or_empty_files(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))

    with pytest.raises(UnsupportedFileError, match="CSV and XLSX"):
        application.stage_upload("notes.txt", b"hello")
    with pytest.raises(UnsafeFileError, match="empty"):
        application.stage_upload("empty.csv", b"")
    with pytest.raises(UnsafeFileError, match="unique after trimming"):
        application.stage_upload("....csv", b"value,value\n1,2\n")
    with pytest.raises(UnsafeFileError, match="ZIP archive"):
        application.stage_upload("renamed.csv", b"PK\x03\x04" + bytes(40))
    assert not tuple((tmp_path / "app-data" / "sessions").rglob("*.csv"))


def test_application_reports_restore_and_publication_preconditions(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))

    with pytest.raises(ApplicationError, match="restore Analysis Session"):
        application.load_workspace(uuid4())

    staged = application.stage_upload("sales.csv", sales_csv())
    workspace = application.ingest(staged)
    with pytest.raises(ApplicationError, match="completed agent run"):
        application.publish_candidates(
            workspace,
            {
                "session_id": str(workspace.session.session_id),
                "status": AgentRunStatus.PLANNING,
            },
        )

    failed_workspace = application.sync_workspace(
        workspace,
        {
            "session_id": str(workspace.session.session_id),
            "status": AgentRunStatus.FAILED,
        },
    )
    assert failed_workspace.session.status is SessionStatus.FAILED


def test_invalid_chart_proposal_falls_back_to_the_verified_table(tmp_path: Path) -> None:
    outputs = model_outputs()
    invalid_chart = outputs[-1]
    invalid_chart["artifact_type"] = "heatmap"
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway(outputs))
    staged = application.stage_upload("sales.csv", sales_csv())
    workspace = application.ingest(staged)

    application.start(workspace, "Compare total revenue by region")
    completed = application.resume(workspace, True)

    # The verified analysis stands, and the invalid proposal is replaced by the exact result table.
    assert completed["status"] == AgentRunStatus.COMPLETED
    assert completed["verified_insights"]
    assert completed["artifact_error"] == ""
    intent = completed["chart_renders"][0]["intent"]
    assert intent["artifact_type"] == "table"
    assert "proposed heatmap chart was invalid" in intent["validation_constraints"][-1]
    assert "not implemented" in intent["validation_constraints"][-1]
    assert len(application.publish_candidates(workspace, completed)) == 1


def test_saved_sql_tool_action_reruns_without_a_model_call(tmp_path: Path) -> None:
    gateway = FakeModelGateway(model_outputs())
    application = LocalAnalysisApplication(tmp_path / "app-data", gateway)
    workspace = application.ingest(application.stage_upload("sales.csv", sales_csv()))
    application.start(workspace, "Compare total revenue by region")
    completed = application.resume(workspace, True)
    action_id = str(completed["tool_actions"][0]["action_id"])
    model_calls = len(gateway.requests)
    tampered = copy.deepcopy(completed)
    tampered["query_results"][0]["rows"] = [["North", 1], ["South", 2]]

    rerun = application.rerun_tool_action(workspace, completed, action_id)
    changed = application.rerun_tool_action(workspace, tampered, action_id)

    assert rerun.reproduced
    assert rerun.detail == "2 rows"
    assert not changed.reproduced
    assert len(gateway.requests) == model_calls
    with pytest.raises(ApplicationError, match="only a succeeded Tool Action"):
        application.rerun_tool_action(workspace, completed, "missing-action")
    # Each run has its own id, and its graph steps are read back from the checkpoint history.
    assert completed["run_id"]
    assert application.run_history(workspace) == (
        "interpret_request",
        "create_plan",
        "approve_plan",
        "request_tool",
        "execute_tool",
        "synthesize_insights",
        "propose_artifact",
    )


def test_saved_statistical_tool_action_reruns_to_the_same_estimates(tmp_path: Path) -> None:
    outputs: list[dict[str, object]] = [
        {
            **model_outputs()[0],
            "goal_text": "Is x correlated with y?",
            "goal_family": "relationship",
        },
        {
            "steps": [
                {
                    "step_id": "correlation",
                    "description": "Correlate x and y",
                    "expected_tool": "statistical_analysis",
                    "statistical_operation": "correlation",
                    "required_fields": ["x", "y"],
                    "intended_output": "Correlation statistics",
                    "caveats": [],
                    "requires_approval": False,
                }
            ]
        },
        {"x_field": "x", "y_field": "y"},
        {
            "insights": [
                {
                    "plan_step_id": "correlation",
                    "assertion": {"operator": "reports", "left_metric": "pearson_r"},
                    "evidence_metrics": ["pearson_r"],
                    "caveats": [],
                }
            ]
        },
    ]
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway(outputs))
    workspace = application.ingest(
        application.stage_upload("xy.csv", b"x,y\n1,2\n2,4\n3,7\n4,8\n5,11\n6,12\n")
    )

    completed = application.start(workspace, "Is x correlated with y?")
    action_id = str(completed["tool_actions"][0]["action_id"])

    assert completed["status"] == AgentRunStatus.COMPLETED
    assert application.rerun_tool_action(workspace, completed, action_id).reproduced


def test_candidate_chart_type_changes_without_a_model_call(tmp_path: Path) -> None:
    profile_question: dict[str, object] = {
        "goal_text": "List the columns",
        "goal_family": "data_quality",
        "requested_metric_mappings": [],
        "semantic_annotations": [],
        "clarification_question": None,
        "answer_from_profile": True,
    }
    gateway = FakeModelGateway([*model_outputs(), profile_question])
    application = LocalAnalysisApplication(tmp_path / "app-data", gateway)
    workspace = application.ingest(application.stage_upload("sales.csv", sales_csv()))
    application.start(workspace, "Compare total revenue by region")
    completed = application.resume(workspace, True)
    candidate = application.publish_candidates(workspace, completed)[0]
    model_calls = len(gateway.requests)

    table = application.refine_candidate(workspace, candidate.artifact_id, ArtifactType.TABLE)
    line = application.refine_candidate(workspace, candidate.artifact_id, ArtifactType.LINE)

    assert (table.version, table.intent.artifact_type) == (2, ArtifactType.TABLE)
    assert (line.version, line.intent.x_field, line.intent.y_fields) == (3, "region", ("revenue",))
    assert application.read_render_spec(line)["plotly_spec"]["data"][0]["mode"] == "lines+markers"
    # Showing the run again keeps the refined candidate instead of adding the original chart.
    assert application.publish_candidates(workspace, completed) == (line,)
    with pytest.raises(ApplicationError, match="kpi chart cannot show"):
        application.refine_candidate(workspace, candidate.artifact_id, ArtifactType.KPI)
    assert len(gateway.requests) == model_calls

    # A later request clears the current results; the chart is re-rendered from checkpoints.
    application.start(workspace, "Which columns are there?")
    bar = application.refine_candidate(workspace, candidate.artifact_id, ArtifactType.BAR)
    assert (bar.version, bar.intent.artifact_type) == (4, ArtifactType.BAR)
    with pytest.raises(ApplicationError, match="could not pin"):
        application.pin(workspace, uuid4())


def test_ingest_failure_removes_new_session_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    staged = application.stage_upload("sales.csv", sales_csv())
    session_directory = tmp_path / "app-data" / "sessions" / str(staged.session_id)

    def fail_profile(
        _self: TabularDataCore, _handle: object, *, timeout_seconds: float | None = None
    ) -> object:
        del timeout_seconds
        raise RuntimeError("profile failed")

    monkeypatch.setattr(TabularDataCore, "profile", fail_profile)
    with pytest.raises(RuntimeError, match="profile failed"):
        application.ingest(staged)

    assert not session_directory.exists()


def test_chart_publication_guard_requires_current_query_provenance(
    tmp_path: Path,
) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway(model_outputs()))
    workspace = application.ingest(application.stage_upload("sales.csv", sales_csv()))
    application.start(workspace, "Compare total revenue by region")
    completed = application.resume(workspace, True)
    result = QueryResult.model_validate(completed["query_results"][0])
    action_payload = next(
        item for item in completed["tool_actions"] if item["tool_name"] == "read_only_sql"
    )
    verified_action = ToolAction.model_validate(action_payload)
    _require_current_query_evidence(workspace, result, verified_action)

    with pytest.raises(ApplicationError, match="rerun"):
        _require_current_query_evidence(
            workspace,
            result.model_copy(update={"inspection": None}),
            verified_action,
        )
    with pytest.raises(ApplicationError, match="rerun"):
        _require_current_query_evidence(
            workspace,
            result,
            verified_action.model_copy(
                update={
                    "inputs": {
                        **verified_action.inputs,
                        "sql": "SELECT 1",
                    }
                }
            ),
        )
