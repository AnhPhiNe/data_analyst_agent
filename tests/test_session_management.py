"""Regression tests for durable Analysis Session lifecycle and storage safety."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.application import ApplicationError, LocalAnalysisApplication
from tabular_analytics_agent.application.models import AnalysisWorkspace
from tabular_analytics_agent.domain import AnalyticalArtifact, ArtifactStatus, SessionStatus
from tabular_analytics_agent.filesystem import delete_tree, resolve_within
from tabular_analytics_agent.model_gateway import FakeModelGateway
from tabular_analytics_agent.orchestration import AgentRunStatus, AgentState, ApprovalDecision


def _model_outputs() -> list[dict[str, object]]:
    return [
        {
            "goal_text": "Compare total revenue by region",
            "goal_family": "comparison",
            "requested_metric_mappings": [
                {
                    "requested_label": "revenue",
                    "status": "direct",
                    "source_fields": ["revenue"],
                }
            ],
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


def _sales_csv() -> bytes:
    return b"region,revenue\nNorth,100\nSouth,150\nNorth,50\n"


def _ingest(application: LocalAnalysisApplication, name: str = "sales.csv") -> AnalysisWorkspace:
    return application.ingest(application.stage_upload(name, _sales_csv()))


def _complete(
    application: LocalAnalysisApplication,
) -> tuple[AnalysisWorkspace, AgentState, AnalyticalArtifact]:
    workspace = _ingest(application)
    application.start(workspace, "Compare total revenue by region")
    state = application.resume(workspace, ApprovalDecision(approved=True))
    assert state["status"] == AgentRunStatus.COMPLETED
    artifact = application.publish_candidates(workspace, state)[0]
    pinned = application.pin(workspace, artifact.artifact_id)
    return workspace, state, pinned


def test_open_session_restores_checkpoint_and_dashboard_after_restart(tmp_path: Path) -> None:
    data_root = tmp_path / "app-data"
    application = LocalAnalysisApplication(data_root, FakeModelGateway(_model_outputs()))
    workspace, state, pinned = _complete(application)

    restarted = LocalAnalysisApplication(data_root, FakeModelGateway([]))
    restored, restored_state = restarted.open_session(workspace.session.session_id)

    assert restored.session.session_id == workspace.session.session_id
    assert restored.session.status is SessionStatus.COMPLETED
    assert restored_state["status"] == state["status"]
    assert restarted.list_dashboard(restored) == (pinned,)
    assert restarted.read_render_spec(pinned)["artifact_id"] == str(pinned.artifact_id)


def test_delete_session_removes_only_its_workspace_checkpoint_and_artifacts(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "app-data"
    application = LocalAnalysisApplication(data_root, FakeModelGateway(_model_outputs()))
    deleted_workspace, _, artifact = _complete(application)
    retained_workspace = _ingest(application, "retained.csv")
    deleted_directory = data_root / "sessions" / str(deleted_workspace.session.session_id)
    render_spec = data_root / "render-specs" / Path(artifact.render_spec_ref)
    assert artifact.status is ArtifactStatus.PINNED
    assert deleted_directory.is_dir()
    assert render_spec.is_file()

    application.delete_session(deleted_workspace.session.session_id)

    assert not deleted_directory.exists()
    assert not render_spec.exists()
    assert application.list_dashboard(retained_workspace) == ()
    assert tuple(summary.session_id for summary in application.list_sessions()) == (
        retained_workspace.session.session_id,
    )
    assert (data_root / "sessions" / str(retained_workspace.session.session_id)).is_dir()


def test_corrupt_workspace_is_rejected_and_omitted_from_listing(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    workspace = _ingest(application)
    workspace_path = (
        tmp_path / "app-data" / "sessions" / str(workspace.session.session_id) / "workspace.json"
    )
    workspace_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ApplicationError, match="metadata is missing or invalid"):
        application.open_session(workspace.session.session_id)
    assert application.list_sessions() == ()


def test_mismatched_workspace_identity_is_rejected(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    workspace = _ingest(application)
    workspace_path = (
        tmp_path / "app-data" / "sessions" / str(workspace.session.session_id) / "workspace.json"
    )
    payload = json.loads(workspace_path.read_text(encoding="utf-8"))
    payload["session"]["session_id"] = str(uuid4())
    workspace_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApplicationError, match="identity does not match"):
        application.open_session(workspace.session.session_id)


def test_corrupt_artifact_metadata_is_wrapped_at_application_boundary(tmp_path: Path) -> None:
    data_root = tmp_path / "app-data"
    application = LocalAnalysisApplication(data_root, FakeModelGateway(_model_outputs()))
    workspace, _, artifact = _complete(application)
    with sqlite3.connect(data_root / "artifacts.sqlite") as connection:
        connection.execute(
            "UPDATE analytical_artifacts SET payload_json = ? WHERE artifact_id = ?",
            ("{bad artifact", str(artifact.artifact_id)),
        )

    restarted = LocalAnalysisApplication(data_root, FakeModelGateway([]))
    with pytest.raises(ApplicationError, match="dashboard metadata"):
        restarted.open_session(workspace.session.session_id)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_dataset_id", str(uuid4()), "Source Dataset identity"),
        ("working_dataset_version", 2, "Working Dataset version"),
        ("data_profile_id", str(uuid4()), "Data Profile identity"),
        ("graph_checkpoint_id", str(uuid4()), "checkpoint identity"),
    ],
)
def test_workspace_scope_drift_is_rejected_without_deleting_data(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    workspace = _ingest(application)
    workspace_path = (
        tmp_path / "app-data" / "sessions" / str(workspace.session.session_id) / "workspace.json"
    )
    payload = json.loads(workspace_path.read_text(encoding="utf-8"))
    payload["session"][field] = value
    workspace_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApplicationError, match=message):
        application.open_session(workspace.session.session_id)
    with pytest.raises(ApplicationError, match=message):
        application.delete_session(workspace.session.session_id)
    assert application.list_sessions() == ()
    assert workspace.dataset_handle.source_path.is_file()
    assert workspace.dataset_handle.working_database_path.is_file()


@pytest.mark.parametrize("field", ["source_path", "working_database_path"])
def test_workspace_cannot_borrow_a_sibling_session_file(tmp_path: Path, field: str) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    workspace = _ingest(application)
    sibling = _ingest(application, "sibling.csv")
    workspace_path = (
        tmp_path / "app-data" / "sessions" / str(workspace.session.session_id) / "workspace.json"
    )
    payload = json.loads(workspace_path.read_text(encoding="utf-8"))
    payload["dataset_handle"][field] = str(getattr(sibling.dataset_handle, field))
    workspace_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApplicationError, match="path does not match"):
        application.open_session(workspace.session.session_id)
    with pytest.raises(ApplicationError, match="path does not match"):
        application.delete_session(workspace.session.session_id)
    assert application.open_session(sibling.session.session_id) == (sibling, {})


def test_cross_session_state_cannot_update_workspace_or_publish_artifacts(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "app-data"
    application = LocalAnalysisApplication(data_root, FakeModelGateway([]))
    workspace = _ingest(application)
    workspace_path = data_root / "sessions" / str(workspace.session.session_id) / "workspace.json"
    before = workspace_path.read_bytes()
    wrong_session_state: AgentState = {
        "session_id": str(uuid4()),
        "status": AgentRunStatus.COMPLETED,
    }

    with pytest.raises(ApplicationError, match="does not match"):
        application.sync_workspace(workspace, wrong_session_state)
    with pytest.raises(ApplicationError, match="does not match"):
        application.publish_candidates(workspace, wrong_session_state)

    assert workspace_path.read_bytes() == before
    assert application.list_sessions()[0].status is SessionStatus.RUNNING


def test_open_session_requires_checkpoint_for_terminal_workspace(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    workspace = _ingest(application)
    terminal = application.sync_workspace(
        workspace,
        {"session_id": str(workspace.session.session_id), "status": AgentRunStatus.REFUSED},
    )
    application._save_workspace(terminal)

    with pytest.raises(ApplicationError, match="checkpoint is missing"):
        application.open_session(workspace.session.session_id)
    assert terminal.session.status is SessionStatus.COMPLETED


def test_uuid_and_path_inputs_cannot_target_storage_root(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    root = tmp_path / "app-data" / "sessions"
    sentinel = root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    for value in ("../sessions", str(root), Path(root), str(uuid4()).upper()):
        with pytest.raises(ApplicationError, match="session_id"):
            application.delete_session(value)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="root cannot be deleted"):
        delete_tree(root, root, label="test storage")
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_empty_checkpoint_cannot_restore_a_terminal_session(tmp_path: Path) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    workspace = _ingest(application)
    # Reading a not-yet-started session creates an empty checkpoint database legitimately.
    assert application.get_state(workspace) == {}
    assert application.open_session(workspace.session.session_id) == (workspace, {})
    terminal = application.sync_workspace(
        workspace,
        {"session_id": str(workspace.session.session_id), "status": AgentRunStatus.REFUSED},
    )
    application._save_workspace(terminal)

    with pytest.raises(ApplicationError, match="checkpoint is empty"):
        application.open_session(workspace.session.session_id)


def test_symlinked_session_and_child_paths_are_rejected_without_following(
    tmp_path: Path,
) -> None:
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway([]))
    sessions_root = tmp_path / "app-data" / "sessions"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    session_id = uuid4()
    session_link = sessions_root / str(session_id)
    try:
        session_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows test environment")

    with pytest.raises(ApplicationError, match=r"unsafe|symlink"):
        application.delete_session(session_id)
    with pytest.raises(ValueError, match="symlink"):
        resolve_within(sessions_root, session_link, label="test session")
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
