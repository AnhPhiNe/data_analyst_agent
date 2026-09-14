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
from tabular_analytics_agent.application.models import (
    AnalysisWorkspace,
    SessionSummary,
    StagedUpload,
)
from tabular_analytics_agent.application.overview import DataOverview, build_data_overview
from tabular_analytics_agent.data import (
    DataCoreLimits,
    TabularDataCore,
    UnsafeFileError,
    UnsupportedFileError,
)
from tabular_analytics_agent.domain import (
    AnalysisPlan,
    AnalysisSession,
    AnalyticalArtifact,
    AnalyticalGoal,
    ExecutionBudget,
    SemanticAnnotation,
    SessionStatus,
)
from tabular_analytics_agent.filesystem import (
    delete_tree,
    is_filesystem_link,
    resolve_within,
    validate_tree,
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
from tabular_analytics_agent.visualization import (
    ArtifactStore,
    ArtifactStoreError,
    ChartRenderResult,
)

_SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".xlsx"}
_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


class LocalAnalysisApplication:
    """Coordinate local data, agent checkpoints, and dashboard artifacts."""

    def __init__(
        self,
        data_root: Path,
        model_gateway: ModelGateway,
        *,
        limits: DataCoreLimits | None = None,
        execution_budget: ExecutionBudget | None = None,
        send_sample_values: bool = True,
    ) -> None:
        self._data_root = data_root.resolve()
        self._model_gateway = model_gateway
        self._limits = limits or DataCoreLimits()
        self._execution_budget = execution_budget or ExecutionBudget()
        self._send_sample_values = send_sample_values
        self._sessions_root = _resolve_storage_path(
            self._data_root,
            self._data_root / "sessions",
            label="Analysis Session storage root",
        )
        try:
            self._sessions_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ApplicationError("could not initialize Analysis Session storage") from exc
        artifact_database = _resolve_storage_path(
            self._data_root,
            self._data_root / "artifacts.sqlite",
            label="artifact metadata storage",
        )
        render_spec_root = _resolve_storage_path(
            self._data_root,
            self._data_root / "render-specs",
            label="artifact render-spec storage",
        )
        self._artifact_store = ArtifactStore(
            artifact_database,
            render_spec_root,
        )

    @property
    def sends_sample_values(self) -> bool:
        """Whether frequent field values may appear in model prompts."""
        return self._send_sample_values

    def stage_upload(self, original_filename: str, content: bytes) -> StagedUpload:
        """Persist an untrusted browser upload under a new session-owned safe path."""
        session_id = uuid4()
        safe_name = _safe_upload_name(original_filename)
        session_directory = self._session_directory(session_id)
        core = TabularDataCore(session_directory, self._limits)
        if not content:
            raise UnsafeFileError("Upload cannot be empty")
        if len(content) > core.limits.max_file_bytes:
            raise UnsafeFileError(
                f"Upload exceeds the {core.limits.max_file_bytes}-byte size limit"
            )
        upload_path = self._session_path(session_id, "incoming", safe_name)
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        if upload_path.exists() or is_filesystem_link(upload_path):
            raise ApplicationError("generated Analysis Session upload path already exists")
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
        session_directory = self._session_directory(staged.session_id)
        incoming_directory = self._session_path(staged.session_id, "incoming")
        upload_path = _resolve_storage_path(
            self._sessions_root,
            staged.upload_path,
            label="staged upload path",
            require_exists=True,
        )
        if upload_path.parent != incoming_directory or not upload_path.is_file():
            raise ApplicationError("staged upload path is not owned by its Analysis Session")
        core = TabularDataCore(session_directory, self._limits)
        handle = core.ingest(upload_path, sheet_name=sheet_name)
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

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        """Return valid persisted sessions in deterministic recent-first order."""
        try:
            entries = tuple(self._sessions_root.iterdir())
        except OSError as exc:
            raise ApplicationError("could not list Analysis Sessions") from exc

        summaries: list[SessionSummary] = []
        for entry in entries:
            try:
                is_link = is_filesystem_link(entry)
            except OSError as exc:
                raise ApplicationError("could not inspect Analysis Session storage") from exc
            if is_link or not entry.is_dir():
                continue
            session_id = _canonical_session_id(entry.name)
            if session_id is None:
                continue
            try:
                workspace = self.load_workspace(session_id)
            except ApplicationError:
                continue
            summaries.append(
                SessionSummary(
                    session=workspace.session,
                    original_filename=workspace.dataset_handle.dataset.original_filename,
                )
            )

        return tuple(
            sorted(
                summaries,
                key=lambda summary: (
                    -summary.updated_at.timestamp(),
                    str(summary.session_id),
                ),
            )
        )

    def open_session(self, session_id: UUID) -> tuple[AnalysisWorkspace, AgentState]:
        """Restore one workspace, its latest checkpoint, and dashboard metadata."""
        session_id = _require_session_id(session_id)
        workspace = self.load_workspace(session_id)
        checkpoint_path = self._session_path(session_id, "checkpoints.sqlite")
        requires_checkpoint = (
            workspace.session.status is not SessionStatus.RUNNING
            or workspace.session.active_goal is not None
        )
        if not checkpoint_path.is_file():
            if requires_checkpoint:
                raise ApplicationError(
                    "could not restore Analysis Session checkpoint: checkpoint is missing"
                )
            return workspace, {}
        try:
            state = self.get_state(workspace)
            if requires_checkpoint and not state:
                raise ApplicationError(
                    "could not restore Analysis Session checkpoint: checkpoint is empty"
                )
            artifacts = self._artifact_store.list_dashboard(workspace.session)
            for artifact in artifacts:
                self._artifact_store.read_render_spec(artifact)
        except ApplicationError:
            raise
        except ArtifactStoreError as exc:
            raise ApplicationError("could not restore Analysis Session dashboard metadata") from exc
        except Exception as exc:
            raise ApplicationError("could not restore Analysis Session checkpoint") from exc

        checkpoint_session_id = state.get("session_id")
        if checkpoint_session_id is not None and checkpoint_session_id != str(session_id):
            raise ApplicationError(
                "could not restore Analysis Session checkpoint: session identity does not match"
            )
        return workspace, state

    def delete_session(self, session_id: UUID) -> None:
        """Delete exactly one validated session namespace and its artifacts."""
        session_id = _require_session_id(session_id)
        self.load_workspace(session_id)
        session_directory = self._session_directory(session_id)
        try:
            validate_tree(
                session_directory,
                self._sessions_root,
                label="Analysis Session storage",
            )
        except (ValueError, OSError) as exc:
            raise ApplicationError("Analysis Session storage is unsafe to delete") from exc
        try:
            self._artifact_store.delete_session(session_id)
        except ArtifactStoreError as exc:
            raise ApplicationError("could not delete Analysis Session artifacts") from exc
        try:
            delete_tree(
                session_directory,
                self._sessions_root,
                label="Analysis Session storage",
            )
        except (ValueError, OSError) as exc:
            raise ApplicationError("could not safely delete Analysis Session storage") from exc

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
        _require_state_session(state, workspace.session.session_id)
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
        session_id = _require_session_id(session_id)
        session_directory = self._session_directory(session_id)
        if not session_directory.is_dir():
            raise ApplicationError(
                "could not restore Analysis Session workspace: session storage is missing"
            )
        path = self._workspace_path(session_id)
        try:
            workspace = AnalysisWorkspace.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise ApplicationError(
                "could not restore Analysis Session workspace: metadata is missing or invalid"
            ) from exc
        try:
            self._validate_workspace(workspace, session_id)
        except ApplicationError as exc:
            raise ApplicationError(f"could not restore Analysis Session workspace: {exc}") from exc
        return workspace

    def get_state(self, workspace: AnalysisWorkspace) -> AgentState:
        """Read the latest checkpoint state for the current Analysis Session."""
        with self._open_orchestrator(workspace) as orchestrator:
            try:
                state = orchestrator.get_state(str(workspace.session.session_id))
            except Exception as exc:
                raise ApplicationError("could not restore Analysis Session checkpoint") from exc
        checkpoint_session_id = state.get("session_id")
        if checkpoint_session_id is not None and checkpoint_session_id != str(
            workspace.session.session_id
        ):
            raise ApplicationError(
                "could not restore Analysis Session checkpoint: session identity does not match"
            )
        return state

    def publish_candidates(
        self,
        workspace: AnalysisWorkspace,
        state: AgentState,
    ) -> tuple[AnalyticalArtifact, ...]:
        """Idempotently publish completed verified chart renders as candidates."""
        _require_state_session(state, workspace.session.session_id)
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

    def data_overview(self, workspace: AnalysisWorkspace) -> DataOverview:
        """Build the deterministic Data Overview for a workspace; no model is called."""
        core = TabularDataCore(self._session_directory(workspace.session.session_id), self._limits)
        return build_data_overview(core, workspace.dataset_handle, workspace.data_profile)

    @contextmanager
    def _open_orchestrator(self, workspace: AnalysisWorkspace) -> Iterator[AgentOrchestrator]:
        # Workspaces are validated where they enter from disk (load_workspace); paths used
        # here are derived from the session id and stay confined by _session_path.
        session_directory = self._session_directory(workspace.session.session_id)
        checkpoint_path = self._session_path(
            workspace.session.session_id,
            "checkpoints.sqlite",
        )
        with open_sqlite_checkpointer(checkpoint_path) as saver:
            yield AgentOrchestrator(
                self._model_gateway,
                TabularDataCore(session_directory, self._limits),
                checkpointer=saver,
                execution_budget=self._execution_budget,
                send_sample_values=self._send_sample_values,
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
        return self._session_path(session_id, "workspace.json")

    def _session_directory(self, session_id: UUID) -> Path:
        session_id = _require_session_id(session_id)
        return _resolve_storage_path(
            self._sessions_root,
            self._sessions_root / str(session_id),
            label="Analysis Session storage",
        )

    def _session_path(self, session_id: UUID, *parts: str) -> Path:
        session_id = _require_session_id(session_id)
        if not parts or any(part in {"", ".", ".."} or Path(part).name != part for part in parts):
            raise ApplicationError("Analysis Session path contains an invalid component")
        session_directory = self._session_directory(session_id)
        return _resolve_storage_path(
            session_directory,
            session_directory.joinpath(*parts),
            label="Analysis Session path",
        )

    def _validate_workspace(
        self,
        workspace: AnalysisWorkspace,
        session_id: UUID,
    ) -> None:
        if workspace.session.session_id != session_id:
            raise ApplicationError("workspace session identity does not match its path")
        session = workspace.session
        handle = workspace.dataset_handle
        profile = workspace.data_profile
        if session.source_dataset_id != handle.dataset.dataset_id:
            raise ApplicationError("workspace Source Dataset identity does not match its session")
        if session.working_dataset_version != handle.working_dataset_version:
            raise ApplicationError("workspace Working Dataset version does not match its session")
        if session.data_profile_id != profile.profile_id:
            raise ApplicationError("workspace Data Profile identity does not match its session")
        if profile.dataset != handle.dataset:
            raise ApplicationError("workspace Data Profile identity does not match its dataset")
        if session.graph_checkpoint_id not in (None, str(session_id)):
            raise ApplicationError("workspace checkpoint identity does not match its session")

        session_directory = self._session_directory(session_id)
        if not session_directory.is_dir():
            raise ApplicationError("session storage is missing")
        source_path = _resolve_storage_path(
            self._sessions_root,
            handle.source_path,
            label="Source Dataset path",
            require_exists=True,
        )
        source_suffix = source_path.suffix.casefold()
        if source_suffix not in _SUPPORTED_UPLOAD_SUFFIXES:
            raise ApplicationError("Source Dataset path has an unsupported extension")
        expected_source = _resolve_storage_path(
            self._sessions_root,
            session_directory / "source" / f"{handle.dataset.dataset_id}{source_suffix}",
            label="Source Dataset path",
        )
        if source_path != expected_source:
            raise ApplicationError("Source Dataset path does not match its identity")

        working_path = _resolve_storage_path(
            self._sessions_root,
            handle.working_database_path,
            label="Working Dataset path",
            require_exists=True,
        )
        expected_working = _resolve_storage_path(
            self._sessions_root,
            session_directory / "working" / str(handle.working_dataset_version) / "dataset.duckdb",
            label="Working Dataset path",
        )
        if working_path != expected_working:
            raise ApplicationError("Working Dataset path does not match its version")
        checkpoint_path = self._session_path(session_id, "checkpoints.sqlite")
        if checkpoint_path.exists() and not checkpoint_path.is_file():
            raise ApplicationError("checkpoint path is not a file")


def _canonical_session_id(name: str) -> UUID | None:
    try:
        session_id = UUID(name)
    except (ValueError, AttributeError):
        return None
    return session_id if str(session_id) == name else None


def _require_session_id(value: object) -> UUID:
    """Accept a UUID object or its canonical lowercase text, never a path-like value."""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        session_id = _canonical_session_id(value)
        if session_id is not None:
            return session_id
    raise ApplicationError("session_id must be a UUID or canonical UUID string")


def _require_state_session(state: AgentState, session_id: UUID) -> None:
    """Reject checkpoint data unless it names the workspace it would update."""
    if state.get("session_id") != str(session_id):
        raise ApplicationError("agent state session identity does not match its workspace")


def _resolve_storage_path(
    root: Path,
    candidate: Path,
    *,
    label: str,
    require_exists: bool = False,
) -> Path:
    """Translate shared filesystem validation errors into the application boundary."""
    try:
        return resolve_within(root, candidate, label=label, require_exists=require_exists)
    except (ValueError, OSError) as exc:
        raise ApplicationError(f"unsafe {label}: {exc}") from exc


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
    if value in {AgentRunStatus.COMPLETED, AgentRunStatus.REFUSED}:
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
