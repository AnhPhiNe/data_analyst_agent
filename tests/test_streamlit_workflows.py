"""Streamlit integration checks with local synthetic data and no live provider."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import dotenv
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import tabular_analytics_agent.model_gateway as gateway_module
from tabular_analytics_agent.application import AnalysisWorkspace, LocalAnalysisApplication
from tabular_analytics_agent.application.exports import ExportRequest, export_verified_insights_json
from tabular_analytics_agent.model_gateway import FakeModelGateway

ROOT = Path(__file__).resolve().parents[1]


def query_outputs() -> list[dict[str, object]]:
    return [
        {
            "goal_text": "Total revenue",
            "goal_family": "summary",
            "requested_metric_mappings": [
                {"requested_label": "revenue", "status": "direct", "source_fields": ["revenue"]}
            ],
        },
        {
            "steps": [
                {
                    "step_id": "total",
                    "description": "Sum revenue",
                    "expected_tool": "read_only_sql",
                    "required_fields": ["revenue"],
                    "intended_output": "Total revenue",
                    "requires_approval": True,
                }
            ]
        },
        {"sql": "SELECT SUM(revenue) AS total FROM dataset"},
        {
            "insights": [
                {
                    "plan_step_id": "total",
                    "assertion": {"operator": "reports", "left_metric": "row[0].total"},
                    "evidence_metrics": ["row[0].total"],
                }
            ]
        },
        {
            "artifact_type": "kpi",
            "analytical_purpose": "Report total revenue",
            "y_fields": ["total"],
            "title": "Total revenue",
        },
    ]


@pytest.fixture
def ui_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway]]:
    gateway = FakeModelGateway(query_outputs())
    data_root = tmp_path / "app-data"
    monkeypatch.setenv("TABULAR_AGENT_DATA_DIR", str(data_root))
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-test-key-not-a-credential")
    monkeypatch.setenv("TABULAR_AGENT_MODEL", "offline-test")
    monkeypatch.setenv("TABULAR_AGENT_MODEL_TIMEOUT_SECONDS", "30")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: False)
    monkeypatch.setattr(gateway_module, "GeminiModelGateway", lambda settings: gateway)
    st.cache_resource.clear()
    application = LocalAnalysisApplication(data_root, gateway)
    workspace = application.ingest(
        application.stage_upload("sales.csv", b"region,revenue\nNorth,100\nSouth,200\n")
    )
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15)
    app.session_state["workspace"] = workspace
    yield app, workspace, application, gateway
    st.cache_resource.clear()


def test_profile_shows_numeric_statistics_without_approval_or_model_calls(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, _, _, gateway = ui_workspace
    app.session_state["agent_state"] = {"status": "completed", "answered_from_profile": True}
    app.run()

    assert not app.exception
    tables = [element.value for element in app.dataframe]
    profile = next(table for table in tables if "mean" in table.columns)
    revenue = profile.loc[profile["field"] == "revenue"].iloc[0]
    assert revenue["mean"] == 150
    assert revenue["min"] == 100
    assert revenue["max"] == 200
    assert not any(button.label == "Approve and run" for button in app.button)
    assert not gateway.requests


def test_data_overview_renders_descriptive_charts_without_model_calls(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, _, _, gateway = ui_workspace
    app.run()

    assert not app.exception
    assert any(expander.label == "Data overview" for expander in app.expander)
    assert any("No model was called" in caption.value for caption in app.caption)
    assert not gateway.requests


def test_data_explorer_builds_a_grouped_chart_without_model_calls(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, _, _, gateway = ui_workspace
    app.run()

    app.selectbox(key="explorer-aggregation").set_value("sum").run()
    app.selectbox(key="explorer-group").set_value("region").run()

    assert not app.exception
    assert not app.error
    assert any("at most 20 groups" in caption.value for caption in app.caption)
    assert not gateway.requests


def test_suggested_goal_starts_analysis_like_a_typed_question(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, _, _, gateway = ui_workspace
    app.run()
    goal = "What is the average revenue for each region?"
    assert not gateway.requests

    next(button for button in app.button if button.label == goal).click().run()

    assert not app.exception
    assert app.session_state["messages"][0]["content"] == goal
    assert app.session_state["agent_state"]["user_request"] == goal
    assert not any(button.label == goal for button in app.button)


def test_staged_upload_opens_profile_workspace_without_model_calls(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, previous, application, gateway = ui_workspace
    staged = application.stage_upload("another.csv", b"month,units\n1,10\n2,20\n")
    app.session_state["workspace"] = None
    app.session_state["agent_state"] = None
    app.session_state["staged_upload"] = staged
    app.run()
    assert not app.exception
    next(button for button in app.button if button.label == "Ingest and profile").click().run()
    assert not app.exception
    restored = app.session_state["workspace"]
    assert restored.session.session_id != previous.session.session_id
    assert restored.dataset_handle.dataset.original_filename == "another.csv"
    assert restored.data_profile.row_count == 2
    assert not gateway.requests


def test_analysis_approval_and_dashboard_pinning(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, workspace, application, gateway = ui_workspace
    app.run()
    app.chat_input[0].set_value("What is total revenue?").run()
    assert not app.exception
    assert app.session_state["agent_state"]["status"] == "awaiting_plan_approval"
    next(button for button in app.button if button.label == "Approve and run").click().run()
    assert not app.exception
    state = app.session_state["agent_state"]
    assert state["status"] == "completed"
    assert "300" in state["verified_insights"][0]["claim"]
    assert len(gateway.requests) == 5
    # The Audit tab shows the exact SQL that produced the verified result.
    assert any("SELECT" in element.value for element in app.code)
    # A saved Tool Action reruns from its recorded SQL without another model call.
    next(button for button in app.button if button.label == "Rerun without the model").click().run()
    assert not app.exception
    assert any("Reproduced" in item.value for item in app.success)
    assert len(gateway.requests) == 5
    export_verified_insights_json(
        ExportRequest.from_state(
            session=app.session_state["workspace"].session,
            profile=workspace.data_profile,
            state=state,
        )
    )
    downloads = app.get("download_button")
    assert any(item.proto.label == "Download evidence JSON" for item in downloads)
    assert any(item.proto.label.startswith("Download result CSV") for item in downloads)
    next(button for button in app.button if button.label == "Pin to Dashboard").click().run()
    assert not app.exception
    assert len(application.list_dashboard(workspace)) == 1
    # Reload the script with no transient workspace, then reopen the durable session.
    st.cache_resource.clear()
    reopened = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15).run()
    reopened.selectbox(key="session-selector").select(workspace.session.session_id).run()
    reopened.button(key="session-open").click().run()
    assert not reopened.exception
    assert reopened.session_state["agent_state"]["status"] == "completed"
    assert any(button.label == "Unpin" for button in reopened.button)
    assert len(gateway.requests) == 5
    next(button for button in app.button if button.label == "Unpin").click().run()
    assert not app.exception
    assert not application.list_dashboard(workspace)
    # A cross-session payload must never yield a download even when its numbers are valid.
    state["session_id"] = "00000000-0000-0000-0000-000000000001"
    app.session_state["agent_state"] = state
    app.run()
    assert not app.exception
    assert not app.get("download_button")
    assert any("belongs to another session" in error.value for error in app.error)


def test_session_selection_requires_fresh_delete_confirmation(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, first, application, gateway = ui_workspace
    second = application.ingest(application.stage_upload("second.csv", b"value\n1\n2\n"))
    app.run()
    app.checkbox(key="session-delete-confirm").check().run()
    assert not app.button(key="session-delete").disabled
    app.selectbox(key="session-selector").select(second.session.session_id).run()
    assert not app.exception
    assert not app.checkbox(key="session-delete-confirm").value
    assert app.button(key="session-delete").disabled
    app.checkbox(key="session-delete-confirm").check().run()
    app.button(key="session-delete").click().run()
    assert not app.exception
    assert {item.session_id for item in application.list_sessions()} == {first.session.session_id}
    assert app.session_state["workspace"].session.session_id == first.session.session_id
    assert not gateway.requests


def test_unavailable_metric_is_visible_and_confirming_does_not_execute_tools(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _, _ = ui_workspace
    gateway = FakeModelGateway(
        [
            {
                "goal_text": "Total profit",
                "goal_family": "summary",
                "requested_metric_mappings": [
                    {
                        "requested_label": "profit",
                        "status": "unavailable",
                        "reason": "Profit is not available in this dataset.",
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(gateway_module, "GeminiModelGateway", lambda settings: gateway)
    st.cache_resource.clear()
    app.run()
    app.chat_input[0].set_value("What is total profit?").run()
    assert not app.exception
    assert app.session_state["agent_state"]["status"] == "awaiting_semantic_review"
    mappings = next(item.value for item in app.dataframe if "requested_label" in item.value.columns)
    assert mappings.iloc[0]["status"] == "unavailable"
    next(
        button for button in app.button if button.label == "Accept that this metric is unavailable"
    ).click().run()
    assert not app.exception
    assert app.session_state["agent_state"]["status"] == "refused"
    assert not app.session_state["agent_state"]["tool_actions"]
    assert any("Profit is not available" in warning.value for warning in app.warning)
    assert len(gateway.requests) == 1


def test_open_session_clears_previous_messages_and_approval_state(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, first, application, gateway = ui_workspace
    second = application.ingest(application.stage_upload("second.csv", b"value\n1\n2\n"))
    app.session_state["messages"] = [{"role": "user", "content": "Previous private question"}]
    app.session_state["agent_state"] = {
        "status": "awaiting_plan_approval",
        "plan": {"steps": []},
    }
    app.run()
    # An explorer choice names a field that the second dataset does not have.
    app.selectbox(key="explorer-group").set_value("region").run()
    app.selectbox(key="session-selector").select(second.session.session_id).run()
    app.button(key="session-open").click().run()
    assert not app.exception
    assert app.session_state["workspace"].session.session_id == second.session.session_id
    assert app.selectbox(key="explorer-group").value == "(none)"
    assert not app.session_state["messages"]
    assert not any(button.label == "Approve and run" for button in app.button)
    assert application.load_workspace(first.session.session_id)
    assert not gateway.requests


def test_corrected_request_reinterprets_metric_before_approval(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _, _ = ui_workspace
    gateway = FakeModelGateway(
        [
            {
                "goal_text": "Total profit",
                "goal_family": "summary",
                "requested_metric_mappings": [
                    {"requested_label": "profit", "status": "unavailable"}
                ],
            },
            *query_outputs(),
        ]
    )
    monkeypatch.setattr(gateway_module, "GeminiModelGateway", lambda settings: gateway)
    st.cache_resource.clear()
    app.run()
    app.chat_input[0].set_value("Total profit?").run()
    app.text_input(key="semantic-correction").set_value("Total revenue instead").run()
    next(
        button for button in app.button if button.label == "Submit corrected question"
    ).click().run()
    assert not app.exception
    state = app.session_state["agent_state"]
    assert state["status"] == "awaiting_plan_approval"
    assert state["user_request"] == "Total revenue instead"
    assert state["requested_metric_mappings"][0]["requested_label"] == "revenue"
    assert not state["tool_actions"]
    assert len(gateway.requests) == 3


def test_column_clarification_can_be_dismissed_without_guessing_a_column(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, workspace, application, _ = ui_workspace
    gateway = FakeModelGateway(
        [
            {
                "goal_text": "Show values of a column",
                "goal_family": "summary",
                "requested_metric_mappings": [],
                "semantic_annotations": [],
                "clarification_question": "Column 'field' does not exist. Which column?",
            }
        ]
    )
    monkeypatch.setattr(gateway_module, "GeminiModelGateway", lambda settings: gateway)
    st.cache_resource.clear()
    app.run()
    app.chat_input[0].set_value("Which values are in column field?").run()
    assert app.session_state["agent_state"]["status"] == "awaiting_semantic_review"
    assert not any(button.label == "Continue with the original question" for button in app.button)

    next(button for button in app.button if button.label == "Dismiss this question").click().run()

    assert not app.exception
    assert app.session_state["agent_state"]["status"] == "rejected"
    assert any("dismissed" in item.value for item in app.warning)
    assert not any(button.label == "Dismiss this question" for button in app.button)
    assert len(gateway.requests) == 1
    # The dismissal is checkpointed, so reopening the session does not revive the question.
    _, reopened = application.open_session(workspace.session.session_id)
    assert reopened["status"] == "rejected"
    assert reopened["clarification_question"] == ""


def test_deleting_current_session_returns_to_upload_and_clears_state(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
) -> None:
    app, _, application, gateway = ui_workspace
    app.session_state["messages"] = [{"role": "user", "content": "Old request"}]
    app.run()
    app.checkbox(key="session-delete-confirm").check().run()
    app.button(key="session-delete").click().run()
    assert not app.exception
    assert not application.list_sessions()
    assert "workspace" not in app.session_state
    assert "messages" not in app.session_state
    assert not app.chat_input
    assert any("permanently deleted" in item.value for item in app.info)
    assert not gateway.requests


def test_invalid_saved_session_open_shows_error_without_losing_current_workspace(
    ui_workspace: tuple[AppTest, AnalysisWorkspace, LocalAnalysisApplication, FakeModelGateway],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tabular_analytics_agent.application import ApplicationError

    app, workspace, _, gateway = ui_workspace

    def fail_open(self: LocalAnalysisApplication, session_id: object) -> None:
        raise ApplicationError("could not restore Analysis Session checkpoint")

    monkeypatch.setattr(LocalAnalysisApplication, "open_session", fail_open)
    app.run()
    app.button(key="session-open").click().run()
    assert not app.exception
    assert app.session_state["workspace"].session.session_id == workspace.session.session_id
    assert any("could not restore" in item.value for item in app.error)
    assert not gateway.requests
