"""Streamlit entry point for the local Tabular Analytics Agent demo."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from tabular_analytics_agent.application import (
    AnalysisWorkspace,
    ApplicationError,
    LocalAnalysisApplication,
    SessionSummary,
    StagedUpload,
    limits_from_environment,
    send_sample_values_from_environment,
)
from tabular_analytics_agent.application.exports import (
    ExportRequest,
    evidence_query_results,
    export_query_result_csv,
    export_verified_insights_json,
)
from tabular_analytics_agent.application.suggestions import suggest_goals
from tabular_analytics_agent.model_gateway import (
    DEFAULT_GEMINI_MODEL,
    GeminiModelGateway,
    GeminiSettings,
    ModelConfigurationError,
)
from tabular_analytics_agent.orchestration import AgentRunStatus, AgentState

load_dotenv()

st.set_page_config(
    page_title="Tabular Analytics Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1500px;}
      [data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.22); border-radius: 12px;
        padding: .8rem 1rem; background: rgba(128,128,128,.04);}
      .taa-kicker {letter-spacing: .12em; text-transform: uppercase; font-size: .76rem;
        color: #5b8def; font-weight: 700;}
      .taa-muted {color: rgba(128,128,128,.95); font-size: .92rem;}
      div[data-testid="stAlert"] {border-radius: 12px;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def build_application(data_root: str, model_id: str) -> LocalAnalysisApplication:
    settings = GeminiSettings.from_environment(
        {
            **os.environ,
            "TABULAR_AGENT_MODEL": model_id,
        }
    )
    limits, execution_budget = limits_from_environment()
    return LocalAnalysisApplication(
        Path(data_root),
        GeminiModelGateway(settings),
        limits=limits,
        execution_budget=execution_budget,
        send_sample_values=send_sample_values_from_environment(),
    )


def current_workspace() -> AnalysisWorkspace | None:
    value = st.session_state.get("workspace")
    return value if isinstance(value, AnalysisWorkspace) else None


def current_state() -> AgentState | None:
    value = st.session_state.get("agent_state")
    return cast(AgentState, value) if isinstance(value, dict) else None


_SESSION_WIDGET_KEYS = {
    "session-selector",
    "session-open",
    "session-new",
    "session-delete-confirm",
    "session-delete",
    "dataset-upload",
    "dataset-sheet",
    "analysis-question",
    "semantic-correction",
    "plan-revision",
}


def reset_session_ui_state() -> None:
    """Clear workspace, approval, message, and widget state before a session switch."""
    for key in tuple(st.session_state):
        if (
            key in _SESSION_WIDGET_KEYS
            or key
            in {
                "workspace",
                "agent_state",
                "messages",
                "staged_upload",
            }
            or str(key).startswith("artifact-")
        ):
            st.session_state.pop(key, None)


def _reset_session_controls() -> None:
    for key in (
        "session-selector",
        "session-open",
        "session-delete-confirm",
        "session-delete",
    ):
        st.session_state.pop(key, None)


def _session_option_label(summary: SessionSummary) -> str:
    timestamp = summary.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        f"{summary.original_filename} · {summary.status.value} · "
        f"{timestamp} · {str(summary.session_id)[:8]}"
    )


def clear_delete_confirmation() -> None:
    st.session_state.pop("session-delete-confirm", None)


def render_session_manager(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace | None,
) -> None:
    """Render the sidebar controls for durable session listing and lifecycle actions."""
    st.markdown("### Sessions")
    try:
        sessions = application.list_sessions()
    except ApplicationError as exc:
        st.error(str(exc))
        sessions = ()

    selected_session_id = None
    if sessions:
        session_ids = tuple(summary.session_id for summary in sessions)
        summaries_by_id = {summary.session_id: summary for summary in sessions}
        current_id = workspace.session.session_id if workspace else None
        index = session_ids.index(current_id) if current_id in session_ids else 0
        selected_session_id = st.selectbox(
            "Session",
            session_ids,
            index=index,
            format_func=lambda value: _session_option_label(summaries_by_id[value]),
            key="session-selector",
            on_change=clear_delete_confirmation,
        )
        if st.button("Open session", key="session-open", width="stretch"):
            try:
                opened_workspace, state = application.open_session(selected_session_id)
            except ApplicationError as exc:
                st.error(str(exc))
            else:
                reset_session_ui_state()
                st.session_state.workspace = opened_workspace
                st.session_state.agent_state = state or None
                st.session_state.messages = []
                st.rerun()
        confirmed = st.checkbox(
            f"Permanently delete {summaries_by_id[selected_session_id].original_filename} "
            f"({str(selected_session_id)[:8]})",
            key="session-delete-confirm",
        )
        if st.button(
            "Delete session",
            key="session-delete",
            disabled=not confirmed,
            width="stretch",
        ):
            try:
                application.delete_session(selected_session_id)
            except ApplicationError as exc:
                st.error(str(exc))
            else:
                if workspace and workspace.session.session_id == selected_session_id:
                    reset_session_ui_state()
                else:
                    _reset_session_controls()
                deleted = summaries_by_id[selected_session_id]
                st.session_state.session_notice = (
                    f"Session {deleted.original_filename} ({str(selected_session_id)[:8]}) and "
                    "its stored data were permanently deleted. Sessions with the same file name "
                    "are separate uploads and remain until deleted."
                )
                st.rerun()
    else:
        st.caption("No persisted sessions yet.")

    if st.button("New session", key="session-new", width="stretch"):
        reset_session_ui_state()
        st.rerun()


def set_agent_state(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace,
    state: AgentState,
) -> None:
    st.session_state.agent_state = state
    st.session_state.workspace = application.sync_workspace(workspace, state)


def profile_table(workspace: AnalysisWorkspace) -> pd.DataFrame:
    rows = []
    for field in workspace.data_profile.fields:
        summary = field.numeric_summary
        rows.append(
            {
                "field": field.name,
                "kind": field.kind.value,
                "missing": field.missing_count,
                "unique": field.unique_count,
                "mean": summary.mean if summary else None,
                "std": summary.standard_deviation if summary else None,
                "min": summary.minimum if summary else None,
                "q1": summary.first_quartile if summary else None,
                "median": summary.median if summary else None,
                "q3": summary.third_quartile if summary else None,
                "max": summary.maximum if summary else None,
                "warnings": "; ".join(field.warnings),
            }
        )
    return pd.DataFrame(rows)


def render_dataset_summary(workspace: AnalysisWorkspace) -> None:
    profile = workspace.data_profile
    missing_cells = sum(field.missing_count for field in profile.fields)
    columns = st.columns(4)
    columns[0].metric("Rows", f"{profile.row_count:,}")
    columns[1].metric("Columns", len(profile.fields))
    columns[2].metric("Missing cells", f"{missing_cells:,}")
    columns[3].metric("Duplicate rows", f"{profile.duplicate_row_count:,}")

    with st.expander("Dataset profile", expanded=False):
        st.dataframe(profile_table(workspace), hide_index=True, width="stretch")
        if profile.pii_candidates:
            st.warning(
                "Possible PII fields are excluded from model evidence: "
                + ", ".join(profile.pii_candidates)
            )


def render_plan(state: AgentState, title: str = "Proposed analysis plan") -> None:
    plan = state.get("plan", {})
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    st.subheader(title)
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        with st.container(border=True):
            st.markdown(f"**{index}. {step.get('description', 'Analysis step')}**")
            st.caption(
                f"Tool: {step.get('expected_tool', 'unknown')} · "
                f"Fields: {', '.join(step.get('required_fields', [])) or 'none'}"
            )
            if step.get("intended_output"):
                st.write(step["intended_output"])
            caveats = step.get("caveats", [])
            if caveats:
                st.caption("Caveats: " + "; ".join(caveats))


def render_metric_mappings(state: AgentState) -> None:
    mappings = state.get("requested_metric_mappings", [])
    if mappings:
        st.caption("Requested metrics and proposed data mapping")
        st.dataframe(pd.DataFrame(mappings), hide_index=True, width="stretch")
        st.caption(
            "Review the meaning of each mapping; source-field checks do not prove "
            "semantic equivalence."
        )


def resume_agent(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace,
    decision: bool | dict[str, object],
) -> None:
    with st.spinner("Agent is working through verified tools…"):
        state = application.resume(workspace, decision)
    set_agent_state(application, workspace, state)
    st.rerun()


def render_approval(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace,
    state: AgentState,
) -> None:
    status = state.get("status")
    if status == AgentRunStatus.AWAITING_SEMANTIC_REVIEW:
        st.warning(state.get("clarification_question", "Review the proposed field meanings."))
        proposals = state.get("proposed_annotations", [])
        if proposals:
            st.dataframe(pd.DataFrame(proposals), hide_index=True, width="stretch")
        unavailable = (
            any(
                item.get("status") == "unavailable"
                for item in state.get("requested_metric_mappings", [])
            )
            and not proposals
        )
        # Without proposed annotations the agent is asking for a corrected question.
        asks_for_question = not proposals
        if asks_for_question:
            choice = (
                "accept that this metric cannot be answered from this dataset"
                if unavailable
                else "dismiss this question"
            )
            st.markdown(
                "**Choose one:** rewrite the question using the columns below and submit it, "
                f"or {choice}."
            )
            st.caption(
                "Available columns: "
                + ", ".join(field.name for field in workspace.data_profile.fields)
            )
        reason = st.text_input(
            "Corrected question" if asks_for_question else "Correction if the proposal is wrong",
            placeholder=(
                "Example: average salary per department"
                if asks_for_question
                else "Example: revenue means net revenue after returns"
            ),
            key="semantic-correction",
        )
        approve, reject = st.columns(2)
        if asks_for_question and not unavailable:
            # Continuing would make the agent guess a column the user never named.
            if approve.button("Dismiss this question", width="stretch"):
                st.session_state.agent_state = None
                st.rerun()
        else:
            confirmation = (
                "Accept that this metric is unavailable" if unavailable else "Confirm semantics"
            )
            if approve.button(confirmation, type="primary", width="stretch"):
                resume_agent(application, workspace, {"approved": True})
        submit_label = "Submit corrected question" if asks_for_question else "Reject and clarify"
        if reject.button(submit_label, width="stretch", disabled=not reason.strip()):
            resume_agent(
                application,
                workspace,
                {"approved": False, "corrected_request": reason.strip()},
            )

    if status == AgentRunStatus.AWAITING_PLAN_APPROVAL:
        render_plan(state)
        revision = st.text_input(
            "Optional revision request",
            placeholder="Example: compare medians instead of means",
            key="plan-revision",
        )
        approve, revise = st.columns(2)
        if approve.button("Approve and run", type="primary", width="stretch"):
            resume_agent(application, workspace, True)
        if revise.button("Request revision", width="stretch", disabled=not revision.strip()):
            resume_agent(
                application,
                workspace,
                {"approved": False, "revision_request": revision.strip()},
            )


def render_result_table(payload: dict[str, Any]) -> None:
    columns = [item["name"] for item in payload.get("columns", [])]
    rows = payload.get("rows", [])
    if columns and rows:
        st.dataframe(pd.DataFrame(rows, columns=columns), hide_index=True, width="stretch")
    if payload.get("truncated"):
        st.warning("The displayed query result reached its configured row limit.")


def render_completed_analysis(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace,
    state: AgentState,
) -> None:
    if state.get("answered_from_profile"):
        st.info("Answered directly from the Data Profile; no analysis tools were needed.")
        st.dataframe(profile_table(workspace), hide_index=True, width="stretch")
        return
    insights = state.get("verified_insights", [])
    if insights:
        st.success("Analysis completed with deterministic verification.")
    elif not state.get("unsupported_claims") and (state.get("query_result") or {}).get("rows"):
        # A row listing is answered by its verified table; no separate claim is required.
        st.info("The verified result table below answers this request.")
    else:
        st.warning(
            "The analysis ran, but no claim passed deterministic verification. "
            "Review the details below or rephrase the question."
        )
    if state.get("plan"):
        with st.expander("Executed analysis plan", expanded=False):
            render_plan(state, title="Analysis plan")
    if insights:
        caveat_lists = [list(item.get("evidence", {}).get("caveats", [])) for item in insights]
        # Result caveats repeat on every insight drawn from the same result; show them once.
        shared: list[str] = []
        if len(caveat_lists) > 1:
            shared = [
                caveat
                for caveat in caveat_lists[0]
                if all(caveat in other for other in caveat_lists[1:])
            ]
        st.subheader("Verified insights")
        for item, caveats in zip(insights, caveat_lists, strict=True):
            with st.container(border=True):
                st.write(item.get("claim", "Verified insight"))
                own = [caveat for caveat in caveats if caveat not in shared]
                if own:
                    st.caption("Caveats: " + "; ".join(own))
        if shared:
            st.caption("Caveats for all insights: " + "; ".join(shared))

    unsupported = state.get("unsupported_claims", [])
    for item in unsupported:
        st.warning(f"Unsupported claim: {item.get('reason', 'insufficient evidence')}")

    query_result = state.get("query_result")
    if isinstance(query_result, dict) and query_result:
        with st.expander("Verified result table", expanded=True):
            render_result_table(query_result)

    artifact_error = state.get("artifact_error")
    if artifact_error:
        st.info(f"No chart candidate was published: {artifact_error}")
    else:
        application.publish_candidates(workspace, state)
    render_exports(workspace, state)


def render_exports(workspace: AnalysisWorkspace, state: AgentState) -> None:
    if not state.get("verified_insights"):
        return
    try:
        request = ExportRequest.from_state(
            session=workspace.session,
            profile=workspace.data_profile,
            state=state,
        )
        # Validate the complete evidence binding before offering any download.
        metadata = export_verified_insights_json(request)
        csv_results = evidence_query_results(request)
    except ValueError:
        st.warning(
            "Export is unavailable: the saved results do not match the current verified evidence."
        )
        return
    with st.expander("Download verified results"):
        st.download_button(
            "Download evidence JSON",
            metadata,
            file_name=f"analysis-{workspace.session.session_id}.json",
            mime="application/json",
            key=f"export-json-{workspace.session.session_id}",
        )
        for result in csv_results:
            st.download_button(
                f"Download result CSV ({str(result.query_id)[:8]})",
                export_query_result_csv(result),
                file_name=f"result-{result.query_id}.csv",
                mime="text/csv",
                key=f"export-csv-{result.query_id}",
            )
            if result.truncated:
                st.warning(
                    "This CSV contains only the bounded rows returned by the query, "
                    "not the full dataset."
                )
        st.caption(
            "CSV text cells and headers that could execute spreadsheet formulas are prefixed "
            "with an apostrophe. JSON excludes model prompts and traces."
        )


def render_artifact(
    application: LocalAnalysisApplication,
    artifact: Any,
    *,
    action_label: str,
    action: Any,
) -> None:
    with st.container(border=True):
        st.subheader(artifact.intent.title)
        st.caption(artifact.intent.analytical_purpose)
        payload = application.read_render_spec(artifact)
        st.plotly_chart(
            go.Figure(payload["plotly_spec"]),
            width="stretch",
            config={"displaylogo": False},
        )
        st.caption(
            f"Verified query {str(artifact.source_result_ref.query_id)[:8]} · "
            f"dataset v{artifact.source_result_ref.working_dataset_version} · "
            f"artifact v{artifact.version}"
        )
        if st.button(action_label, key=f"artifact-{action_label}-{artifact.artifact_id}"):
            action(artifact.artifact_id)
            st.rerun()


def render_analyze_tab(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace,
) -> None:
    render_dataset_summary(workspace)
    state = current_state()
    if state and state.get("session_id") not in (None, str(workspace.session.session_id)):
        st.error(
            "The current result belongs to another session. "
            "Open the session again to restore it safely."
        )
        return
    for message in st.session_state.get("messages", []):
        with st.chat_message(message["role"]):
            st.write(message["content"])

    if state:
        render_metric_mappings(state)
        render_approval(application, workspace, state)
        if state.get("status") == AgentRunStatus.COMPLETED:
            render_completed_analysis(application, workspace, state)
        elif state.get("status") == AgentRunStatus.REFUSED:
            st.warning(
                state.get("refusal_reason")
                or "This request cannot be supported with the current data."
            )
            st.caption(
                "Ask a new question, for example with other columns or groups, or add more data."
            )
        elif state.get("status") == AgentRunStatus.REJECTED:
            st.warning(state.get("error") or "The analysis plan was rejected.")
        elif state.get("status") == AgentRunStatus.FAILED:
            st.error(
                "The agent could not complete this request safely. Try rephrasing the question "
                "or naming the columns to analyze."
            )
            with st.expander("Technical details"):
                st.code(state.get("error") or "No error detail was recorded.", language=None)

    suggested_goal = None
    if not state and not st.session_state.get("messages"):
        st.caption("Suggested goals from the Data Profile (or ask your own question below)")
        for index, goal in enumerate(suggest_goals(workspace.data_profile)):
            if st.button(goal, key=f"suggested-goal-{index}"):
                suggested_goal = goal

    waiting = bool(
        state
        and state.get("status")
        in {
            AgentRunStatus.AWAITING_SEMANTIC_REVIEW,
            AgentRunStatus.AWAITING_PLAN_APPROVAL,
        }
    )
    prompt = (
        st.chat_input(
            "Ask a question about this dataset",
            disabled=waiting,
            key="analysis-question",
        )
        or suggested_goal
    )
    if prompt:
        st.session_state.setdefault("messages", []).append({"role": "user", "content": prompt})
        with st.spinner("Agent is interpreting your goal…"):
            next_state = application.start(workspace, prompt)
        set_agent_state(application, workspace, next_state)
        st.rerun()

    candidates = application.list_candidates(workspace)
    if candidates:
        st.divider()
        st.subheader("Candidate artifacts")
        st.caption("Only you decide which verified artifacts appear on the Dashboard.")
        for artifact in candidates:
            render_artifact(
                application,
                artifact,
                action_label="Pin to Dashboard",
                action=lambda artifact_id: application.pin(workspace, artifact_id),
            )


def render_dashboard_tab(
    application: LocalAnalysisApplication,
    workspace: AnalysisWorkspace,
) -> None:
    artifacts = application.list_dashboard(workspace)
    if not artifacts:
        st.info("No pinned artifacts yet. Pin a candidate from the Analyze tab.")
        return
    for artifact in artifacts:
        render_artifact(
            application,
            artifact,
            action_label="Unpin",
            action=lambda artifact_id: application.unpin(workspace, artifact_id),
        )


def render_audit_tab(state: AgentState | None) -> None:
    if not state:
        st.info("Run an analysis to inspect its verification trail.")
        return
    st.subheader("Tool actions")
    actions = state.get("tool_actions", [])
    if not actions:
        st.caption("No Tool Action ran for this request.")
    for index, action in enumerate(actions, start=1):
        inputs = action.get("inputs", {})
        status = action.get("status", "unknown")
        label = f"{index}. {action.get('tool_name', 'tool')} · {status}"
        with st.expander(label, expanded=status != "succeeded"):
            fields = ", ".join(str(field) for field in inputs.get("required_fields", []))
            st.caption(
                f"Plan step {inputs.get('plan_step_id', '-')} · approved fields: "
                f"{fields or 'none'} · retries: {action.get('retry_count', 0)} · "
                f"duration: {action.get('duration_ms') or '-'} ms"
            )
            if inputs.get("sql"):
                st.code(inputs["sql"], language="sql")
            if inputs.get("request"):
                st.json(inputs["request"], expanded=False)
            if action.get("error"):
                st.error(action["error"])
            checks = [
                check
                for verification in action.get("verification_results", [])
                for check in verification.get("checks", [])
            ]
            if checks:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "check": check.get("name"),
                                "passed": "yes" if check.get("passed") else "NO",
                                "message": check.get("message"),
                            }
                            for check in checks
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )

    st.subheader("Insight evidence")
    insights = state.get("verified_insights", [])
    for item in insights:
        evidence = item.get("evidence", {})
        with st.expander(str(item.get("claim", "Verified insight"))):
            st.caption(
                f"Fields: {', '.join(evidence.get('source_fields', []))} · filters: "
                f"{'; '.join(evidence.get('filters', [])) or 'none'} · source rows: "
                f"{evidence.get('source_row_count')} · result rows: "
                f"{evidence.get('result_row_count')}"
            )
            values = evidence.get("values", [])
            if values:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "metric": value.get("metric"),
                                "value": str(value.get("value")),
                                "unit": value.get("unit") or "",
                            }
                            for value in values
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
            st.caption(f"Missing data: {evidence.get('missing_data_handling', '-')}")
    for item in state.get("unsupported_claims", []):
        st.warning(
            f"Rejected claim: {item.get('claim', '')} ({item.get('reason', 'no reason recorded')})"
        )
    if not insights and not state.get("unsupported_claims"):
        st.caption("No insight was drafted for this request.")

    st.subheader("Model calls")
    traces = state.get("model_traces", [])
    if traces:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "task": trace.get("task"),
                        "prompt": trace.get("prompt_template_version"),
                        "model": trace.get("model_id"),
                        "latency_ms": trace.get("latency_ms"),
                        "tokens": trace.get("usage", {}).get("total_tokens"),
                        "repairs": trace.get("validation_repair_count"),
                        "error": trace.get("error") or "",
                    }
                    for trace in traces
                ]
            ),
            hide_index=True,
            width="stretch",
        )


def render_upload(application: LocalAnalysisApplication) -> None:
    st.markdown(
        '<p class="taa-kicker">Local-first · verified · reproducible</p>', unsafe_allow_html=True
    )
    st.title("Turn tabular data into evidence-backed decisions")
    st.write(
        "Upload a CSV or XLSX file, approve the analysis plan, and curate verified charts into "
        "your dashboard. Dataset values stay in deterministic local tools."
    )
    uploaded = st.file_uploader(
        "Upload a dataset",
        type=["csv", "xlsx"],
        key="dataset-upload",
    )
    if uploaded and st.button("Inspect file", type="primary"):
        with st.spinner("Inspecting file structure and safety limits…"):
            st.session_state.staged_upload = application.stage_upload(
                uploaded.name, uploaded.getvalue()
            )
        st.rerun()

    staged = st.session_state.get("staged_upload")
    if not isinstance(staged, StagedUpload):
        return
    inspection = staged.inspection
    st.success(
        f"{inspection.original_filename} · {inspection.file_format.upper()} · "
        f"{inspection.size_bytes:,} bytes"
    )
    for warning in inspection.warnings:
        st.warning(warning)
    sheet_name = None
    if inspection.sheets:
        sheet_name = st.selectbox("Worksheet", inspection.sheets, key="dataset-sheet")
    if st.button("Ingest and profile", type="primary"):
        with st.spinner("Creating an immutable source and profiling the working dataset…"):
            st.session_state.workspace = application.ingest(staged, sheet_name=sheet_name)
            st.session_state.agent_state = None
            st.session_state.messages = []
            del st.session_state.staged_upload
        st.rerun()


def main() -> None:
    data_root = os.getenv("TABULAR_AGENT_DATA_DIR", ".data")
    model_id = os.getenv("TABULAR_AGENT_MODEL", DEFAULT_GEMINI_MODEL)
    try:
        application = build_application(data_root, model_id)
    except ModelConfigurationError as exc:
        st.error(str(exc))
        st.caption("Add GOOGLE_API_KEY to .env, then restart Streamlit.")
        st.stop()
    except ApplicationError as exc:
        st.error(str(exc))
        st.caption("Fix the setting in .env or the data directory, then restart Streamlit.")
        st.stop()

    if notice := st.session_state.pop("session_notice", None):
        st.info(notice)
    workspace = current_workspace()
    with st.sidebar:
        st.markdown("### Tabular Analytics Agent")
        st.caption(f"Model: {model_id}")
        if not application.sends_sample_values:
            st.caption("Sample values are not sent to the model.")
        render_session_manager(application, workspace)
        if workspace:
            st.caption(f"Session: {str(workspace.session.session_id)[:8]}")
            st.caption(f"Dataset: {workspace.dataset_handle.dataset.original_filename}")

    if workspace is None:
        render_upload(application)
        return

    st.markdown('<p class="taa-kicker">Analysis workspace</p>', unsafe_allow_html=True)
    st.title(workspace.dataset_handle.dataset.original_filename)
    analyze, dashboard, audit = st.tabs(["Analyze", "Dashboard", "Audit"])
    with analyze:
        render_analyze_tab(application, workspace)
    with dashboard:
        render_dashboard_tab(application, workspace)
    with audit:
        render_audit_tab(current_state())


if __name__ == "__main__":
    main()
