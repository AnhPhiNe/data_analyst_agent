"""Testable application service used by the Streamlit presentation layer."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from tabular_analytics_agent.application.errors import ApplicationError
from tabular_analytics_agent.application.models import AnalysisWorkspace, StagedUpload
from tabular_analytics_agent.data import TabularDataCore, UnsafeFileError, UnsupportedFileError
from tabular_analytics_agent.domain import (
    AnalysisPlan,
    AnalysisSession,
    AnalyticalArtifact,
    AnalyticalGoal,
    SemanticAnnotation,
    SessionStatus,
)
from tabular_analytics_agent.model_gateway import ModelGateway
from tabular_analytics_agent.orchestration import (
    AgentOrchestrator,
    AgentRunRequest,
    AgentRunStatus,
    AgentState,
    ApprovalDecision,
    open_sqlite_checkpointer,
)
from tabular_analytics_agent.visualization import ArtifactStore, ChartRenderResult

_SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".xlsx"}
_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


class LocalAnalysisApplication:
    """Coordinate local data, agent checkpoints, and dashboard artifacts."""

    def __init__(self, data_root: Path, model_gateway: ModelGateway) -> None:
        self._data_root = data_root.resolve()
        self._model_gateway = model_gateway
        self._sessions_root = self._data_root / "sessions"
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        self._artifact_store = ArtifactStore(
            self._data_root / "artifacts.sqlite",
            self._data_root / "render-specs",
        )

    def stage_upload(self, original_filename: str, content: bytes) -> StagedUpload:
        """Persist an untrusted browser upload under a new session-owned safe path."""
        session_id = uuid4()
        safe_name = _safe_upload_name(original_filename)
        session_directory = self._session_directory(session_id)
        core = TabularDataCore(session_directory)
        if not content:
            raise UnsafeFileError("Upload cannot be empty")
        if len(content) > core.limits.max_file_bytes:
            raise UnsafeFileError(
                f"Upload exceeds the {core.limits.max_file_bytes}-byte size limit"
            )
        upload_path = session_directory / "incoming" / safe_name
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            upload_path.write_bytes(content)
            inspection = core.inspect(upload_path)
        except Exception:
            upload_path.unlink(missing_ok=True)
            raise
        return StagedUpload(
            session_id=session_id,
            upload_path=upload_path,
            inspection=inspection,
        )

    def ingest(self, staged: StagedUpload, *, sheet_name: str | None = None) -> AnalysisWorkspace:
        """Create a profiled Working Dataset and durable Analysis Session context."""
        core = TabularDataCore(self._session_directory(staged.session_id))
        handle = core.ingest(staged.upload_path, sheet_name=sheet_name)
        profile = core.profile(handle)
        now = datetime.now(UTC)
        session = AnalysisSession(
            session_id=staged.session_id,
            status=SessionStatus.RUNNING,
            source_dataset_id=handle.dataset.dataset_id,
            working_dataset_version=handle.working_dataset_version,
            data_profile_id=profile.profile_id,
            graph_checkpoint_id=str(staged.session_id),
            created_at=now,
            updated_at=now,
        )
        workspace = AnalysisWorkspace(
            session=session,
            dataset_handle=handle,
            data_profile=profile,
        )
        self._save_workspace(workspace)
        return workspace

    def start(self, workspace: AnalysisWorkspace, user_request: str) -> AgentState:
        """Start one checkpointed analysis turn for the current dataset."""
        request = AgentRunRequest(
            session_id=str(workspace.session.session_id),
            user_request=user_request,
            dataset_handle=workspace.dataset_handle,
            data_profile=workspace.data_profile,
        )
        with self._open_orchestrator(workspace) as orchestrator:
            state = orchestrator.start(request)
        self._save_workspace(self.sync_workspace(workspace, state))
        return state

    def resume(
        self,
        workspace: AnalysisWorkspace,
        decision: ApprovalDecision | bool | dict[str, object],
    ) -> AgentState:
        """Resume semantic or plan approval from the durable graph checkpoint."""
        payload: bool | dict[str, object]
        if isinstance(decision, ApprovalDecision):
            payload = decision.model_dump(mode="json", exclude_none=True)
        else:
            payload = decision
        with self._open_orchestrator(workspace) as orchestrator:
            state = orchestrator.resume(str(workspace.session.session_id), payload)
        self._save_workspace(self.sync_workspace(workspace, state))
        return state

    def sync_workspace(
        self,
        workspace: AnalysisWorkspace,
        state: AgentState,
    ) -> AnalysisWorkspace:
        """Project checkpointed goal and approval state into durable session metadata."""
        annotations = tuple(
            SemanticAnnotation.model_validate(value)
            for value in state.get("semantic_annotations", [])
        )
        goal_payload = state.get("goal")
        plan_payload = state.get("plan")
        goal = AnalyticalGoal.model_validate(goal_payload) if goal_payload else None
        plan = AnalysisPlan.model_validate(plan_payload) if plan_payload else None
        updated_session = AnalysisSession.model_validate(
            {
                **workspace.session.model_dump(),
                "status": _session_status(state.get("status")),
                "semantic_annotations": annotations,
                "active_goal": goal,
                "active_plan_id": plan.plan_id if plan else None,
                "updated_at": datetime.now(UTC),
            }
        )
        return workspace.model_copy(update={"session": updated_session})

    def load_workspace(self, session_id: UUID) -> AnalysisWorkspace:
        """Restore validated session context after an application rerun."""
        path = self._workspace_path(session_id)
        try:
            return AnalysisWorkspace.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise ApplicationError("could not restore Analysis Session workspace") from exc

    def get_state(self, workspace: AnalysisWorkspace) -> AgentState:
        """Read the latest checkpoint state for the current Analysis Session."""
        with self._open_orchestrator(workspace) as orchestrator:
            return orchestrator.get_state(str(workspace.session.session_id))

    def publish_candidates(
        self,
        workspace: AnalysisWorkspace,
        state: AgentState,
    ) -> tuple[AnalyticalArtifact, ...]:
        """Idempotently publish completed verified chart renders as candidates."""
        if state.get("status") != AgentRunStatus.COMPLETED:
            raise ApplicationError("artifacts can be published only from a completed agent run")
        current_workspace = self.sync_workspace(workspace, state)
        self._save_workspace(current_workspace)
        existing = self._artifact_store.list_artifacts(current_workspace.session.session_id)
        publications: list[AnalyticalArtifact] = []
        for payload in state.get("chart_renders", []):
            rendered = ChartRenderResult.model_validate(payload)
            insight_ids = _insight_ids_for_source(state, rendered)
            match = next(
                (
                    artifact
                    for artifact in existing
                    if artifact.intent == rendered.intent
                    and artifact.source_result_ref == rendered.source_result_ref
                ),
                None,
            )
            artifact = match or self._artifact_store.create_candidate(
                current_workspace.session,
                rendered,
                insight_ids=insight_ids,
            )
            publications.append(artifact)
            if match is None:
                existing = (*existing, artifact)
        return tuple(publications)

    def list_candidates(self, workspace: AnalysisWorkspace) -> tuple[AnalyticalArtifact, ...]:
        return self._artifact_store.list_candidates(workspace.session)

    def list_dashboard(self, workspace: AnalysisWorkspace) -> tuple[AnalyticalArtifact, ...]:
        return self._artifact_store.list_dashboard(workspace.session)

    def pin(self, workspace: AnalysisWorkspace, artifact_id: UUID) -> AnalyticalArtifact:
        return self._artifact_store.pin(workspace.session, artifact_id)

    def unpin(self, workspace: AnalysisWorkspace, artifact_id: UUID) -> AnalyticalArtifact:
        return self._artifact_store.unpin(workspace.session.session_id, artifact_id)

    def read_render_spec(self, artifact: AnalyticalArtifact) -> dict[str, Any]:
        return self._artifact_store.read_render_spec(artifact)

    @contextmanager
    def _open_orchestrator(self, workspace: AnalysisWorkspace) -> Iterator[AgentOrchestrator]:
        session_directory = self._session_directory(workspace.session.session_id)
        with open_sqlite_checkpointer(session_directory / "checkpoints.sqlite") as saver:
            yield AgentOrchestrator(
                self._model_gateway,
                TabularDataCore(session_directory),
                checkpointer=saver,
            )

    def _save_workspace(self, workspace: AnalysisWorkspace) -> None:
        path = self._workspace_path(workspace.session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".workspace-{uuid4().hex[:8]}.tmp")
        try:
            temporary.write_text(workspace.model_dump_json(), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ApplicationError(
                "could not atomically persist Analysis Session workspace"
            ) from exc

    def _workspace_path(self, session_id: UUID) -> Path:
        return self._session_directory(session_id) / "workspace.json"

    def _session_directory(self, session_id: UUID) -> Path:
        return self._sessions_root / str(session_id)


def _safe_upload_name(original_filename: str) -> str:
    normalized = original_filename.replace("\\", "/")
    basename = PurePosixPath(normalized).name
    suffix = Path(basename).suffix.casefold()
    if suffix not in _SUPPORTED_UPLOAD_SUFFIXES:
        raise UnsupportedFileError("Only CSV and XLSX uploads are supported")
    stem = Path(basename).stem
    safe_stem = _SAFE_FILENAME_CHARS.sub("_", stem).strip(" .")[:80]
    if not safe_stem:
        safe_stem = "upload"
    return f"{safe_stem}{suffix}"


def _session_status(value: str | None) -> SessionStatus:
    if value in {
        AgentRunStatus.AWAITING_SEMANTIC_REVIEW,
        AgentRunStatus.AWAITING_PLAN_APPROVAL,
    }:
        return SessionStatus.WAITING
    if value == AgentRunStatus.COMPLETED:
        return SessionStatus.COMPLETED
    if value in {AgentRunStatus.FAILED, AgentRunStatus.REJECTED}:
        return SessionStatus.FAILED
    return SessionStatus.RUNNING


def _insight_ids_for_source(
    state: AgentState,
    rendered: ChartRenderResult,
) -> tuple[UUID, ...]:
    action_ids = {
        str(action["action_id"])
        for action in state.get("tool_actions", [])
        if action.get("output_ref") == rendered.source_result_ref.canonical_ref
    }
    return tuple(
        UUID(str(insight["insight_id"]))
        for insight in state.get("verified_insights", [])
        if action_ids.intersection(
            str(action_id) for action_id in insight.get("evidence", {}).get("tool_action_ids", [])
        )
    )
