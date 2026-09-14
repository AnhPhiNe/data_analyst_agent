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
    LocalAnalysisApplication,
    StagedUpload,
)
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
    return LocalAnalysisApplication(Path(data_root), GeminiModelGateway(settings))


def current_workspace() -> AnalysisWorkspace | None:
    value = st.session_state.get("workspace")
    return value if isinstance(value, AnalysisWorkspace) else None


def current_state() -> AgentState | None:
    value = st.session_state.get("agent_state")
    return cast(AgentState, value) if isinstance(value, dict) else None


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
        reason = st.text_input(
            "Correction if the proposal is wrong",
            placeholder="Example: revenue means net revenue after returns",
        )
        approve, reject = st.columns(2)
        if approve.button("Confirm semantics", type="primary", width="stretch"):
            resume_agent(application, workspace, {"approved": True})
        if reject.button("Reject and clarify", width="stretch", disabled=not reason.strip()):
            resume_agent(
                application,
                workspace,
                {"approved": False, "reason": reason.strip()},
            )

    if status == AgentRunStatus.AWAITING_PLAN_APPROVAL:
        render_plan(state)
        revision = st.text_input(
            "Optional revision request",
            placeholder="Example: compare medians instead of means",
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
    st.success("Analysis completed with deterministic verification.")
    if state.get("plan"):
        with st.expander("Executed analysis plan", expanded=False):
            render_plan(state, title="Analysis plan")
    insights = state.get("verified_insights", [])
    if insights:
        st.subheader("Verified insights")
        for item in insights:
            with st.container(border=True):
                st.write(item.get("claim", "Verified insight"))
                caveats = item.get("evidence", {}).get("caveats", [])
                if caveats:
                    st.caption("Caveats: " + "; ".join(caveats))
    else:
        st.info("The run completed but did not produce a supported verified claim.")

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
    for message in st.session_state.get("messages", []):
        with st.chat_message(message["role"]):
            st.write(message["content"])

    if state:
        render_approval(application, workspace, state)
        if state.get("status") == AgentRunStatus.COMPLETED:
            render_completed_analysis(application, workspace, state)
        elif state.get("status") == AgentRunStatus.REJECTED:
            st.warning(state.get("error") or "The analysis plan was rejected.")
        elif state.get("status") == AgentRunStatus.FAILED:
            st.error(
                "The agent could not complete this request safely. Try rephrasing the question "
                "or naming the columns to analyze."
            )
            with st.expander("Technical details"):
                st.code(state.get("error") or "No error detail was recorded.", language=None)

    waiting = bool(
        state
        and state.get("status")
        in {
            AgentRunStatus.AWAITING_SEMANTIC_REVIEW,
            AgentRunStatus.AWAITING_PLAN_APPROVAL,
        }
    )
    prompt = st.chat_input(
        "Ask a question about this dataset",
        disabled=waiting,
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
    st.subheader("Tool and verification trail")
    for action in state.get("tool_actions", []):
        label = f"{action.get('tool_name', 'tool')} · {action.get('status', 'unknown')}"
        with st.expander(label):
            st.json(
                {
                    "action_id": action.get("action_id"),
                    "working_dataset_version": action.get("working_dataset_version"),
                    "output_ref": action.get("output_ref"),
                    "duration_ms": action.get("duration_ms"),
                    "verification_results": action.get("verification_results", []),
                }
            )
    st.subheader("Model call metadata")
    traces = state.get("model_traces", [])
    if traces:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "task": trace.get("task"),
                        "model": trace.get("model_id"),
                        "latency_ms": trace.get("latency_ms"),
                        "tokens": trace.get("usage", {}).get("total_tokens"),
                        "repairs": trace.get("validation_repair_count"),
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
    uploaded = st.file_uploader("Upload a dataset", type=["csv", "xlsx"])
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
        sheet_name = st.selectbox("Worksheet", inspection.sheets)
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

    workspace = current_workspace()
    with st.sidebar:
        st.markdown("### Tabular Analytics Agent")
        st.caption(f"Model: {model_id}")
        if workspace:
            st.caption(f"Session: {str(workspace.session.session_id)[:8]}")
            st.caption(f"Dataset: {workspace.dataset_handle.dataset.original_filename}")
            if st.button("Start another dataset", width="stretch"):
                for key in ("workspace", "agent_state", "messages", "staged_upload"):
                    st.session_state.pop(key, None)
                st.rerun()

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
