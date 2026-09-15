"""Integration tests for the local application workflow behind Streamlit."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.application import ApplicationError, LocalAnalysisApplication
from tabular_analytics_agent.data import UnsafeFileError, UnsupportedFileError
from tabular_analytics_agent.domain import ArtifactStatus, SessionStatus
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
    repeated_publication = application.publish_candidates(workspace, completed)
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
