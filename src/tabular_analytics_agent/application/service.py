"""Testable application service used by the Streamlit presentation layer."""

from __future__ import annotations

import math
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
    ToolActionRerun,
)
from tabular_analytics_agent.application.overview import (
    DataOverview,
    ExplorerRequest,
    OverviewChart,
    build_data_overview,
    build_explorer_charts,
    filter_values,
)
from tabular_analytics_agent.data import (
    DataCoreLimits,
    QueryExecutionError,
    QueryResult,
    TabularDataCore,
    UnsafeFileError,
    UnsafeQueryError,
    UnsupportedFileError,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisPlan,
    AnalysisSession,
    AnalyticalArtifact,
    AnalyticalGoal,
    ArtifactType,
    ExecutionBudget,
    SemanticAnnotation,
    SessionStatus,
    ToolAction,
    VerificationStatus,
    canonical_uuid,
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
from tabular_analytics_agent.statistics import (
    StatisticalAnalysisError,
    StatisticalRequest,
    StatisticalResult,
    StatisticalTool,
)
from tabular_analytics_agent.verification.provenance import query_provenance_status
from tabular_analytics_agent.visualization import (
    ArtifactStore,
    ArtifactStoreError,
    ChartRenderResult,
    ChartValidationError,
    make_query_result_reference,
    render_chart,
    retarget_chart_intent,
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
            try:
                delete_tree(
                    session_directory,
                    self._sessions_root,
                    label="failed staged upload cleanup",
                )
            except (OSError, ValueError):
                # Preserve the original upload-validation error if cleanup itself is unavailable.
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
        try:
            handle = core.ingest(upload_path, sheet_name=sheet_name)
            profile = core.profile(
                handle,
                timeout_seconds=self._limits.query_timeout_seconds,
            )
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
        except Exception:
            # Ingest is the only operation that creates this session directory.  A failure before
            # the workspace is durable must not leave a temporary upload/database behind, while a
            # later statistical failure operates on an existing session and never reaches here.
            try:
                if session_directory.exists():
                    delete_tree(
                        session_directory,
                        self._sessions_root,
                        label="failed Analysis Session cleanup",
                    )
            except (OSError, ValueError):
                # Preserve the original ingestion/profile error; startup cleanup is best effort.
                pass
            raise

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
            session_id = canonical_uuid(entry.name)
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

    def run_history(self, workspace: AnalysisWorkspace) -> tuple[str, ...]:
        """Return the graph nodes the latest run passed through, oldest first."""
        with self._open_orchestrator(workspace) as orchestrator:
            try:
                return orchestrator.run_history(str(workspace.session.session_id))
            except Exception as exc:
                raise ApplicationError("could not read the Analysis Session run history") from exc

    def rerun_tool_action(
        self, workspace: AnalysisWorkspace, state: AgentState, action_id: str
    ) -> ToolActionRerun:
        """Re-execute a saved Tool Action from its recorded parameters, without a model call."""
        _require_state_session(state, workspace.session.session_id)
        action = next(
            (
                ToolAction.model_validate(item)
                for item in state.get("tool_actions", [])
                if str(item.get("action_id")) == action_id
            ),
            None,
        )
        if action is None or action.status is not ActionStatus.SUCCEEDED or not action.output_ref:
            raise ApplicationError("only a succeeded Tool Action of this run can be rerun")
        handle = workspace.dataset_handle
        if action.working_dataset_version != handle.working_dataset_version:
            raise ApplicationError("the Tool Action used an earlier Working Dataset version")
        result_id = action.output_ref.partition(":")[2]
        core = self._workspace_core(workspace)
        try:
            if action.tool_name == "read_only_sql":
                stored = QueryResult.model_validate(
                    next(
                        item
                        for item in state.get("query_results", [])
                        if str(item.get("query_id")) == result_id
                    )
                )
                fresh = core.query(handle, str(action.inputs["sql"]))
                reproduced = fresh.columns == stored.columns and fresh.rows == stored.rows
                detail = f"{fresh.row_count} rows"
            else:
                saved = StatisticalResult.model_validate(
                    next(
                        item
                        for item in state.get("statistical_results", [])
                        if str(item.get("result_id")) == result_id
                    )
                )
                output = StatisticalTool(core).execute(
                    handle=handle,
                    profile=workspace.data_profile,
                    request=StatisticalRequest.model_validate(action.inputs["request"]),
                )
                reproduced = _same_statistics(output.result, saved)
                detail = f"{len(output.result.estimates)} estimates"
        except StopIteration as exc:
            raise ApplicationError("the saved result of this Tool Action is missing") from exc
        except (
            KeyError,
            QueryExecutionError,
            StatisticalAnalysisError,
            UnsafeQueryError,
            ValidationError,
        ) as exc:
            raise ApplicationError(f"could not rerun the Tool Action: {exc}") from exc
        return ToolActionRerun(action_id=action_id, reproduced=reproduced, detail=detail)

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
        existing = self._artifact_store.list_artifacts(current_workspace.session.session_id)
        publications: list[AnalyticalArtifact] = []
        with self._open_orchestrator(current_workspace) as orchestrator:
            for payload in state.get("chart_renders", []):
                rendered = ChartRenderResult.model_validate(payload)
                evidence = orchestrator.query_evidence(
                    str(current_workspace.session.session_id),
                    str(rendered.source_result_ref.query_id),
                )
                if evidence is None:
                    raise ApplicationError(
                        "the chart result is no longer available; rerun the Tool Action"
                    )
                result = QueryResult.model_validate(evidence[0])
                action = ToolAction.model_validate(evidence[1])
                _require_current_query_evidence(current_workspace, result, action)
                expected_ref = make_query_result_reference(
                    result, current_workspace.session.semantic_annotations
                )
                if rendered.source_result_ref != expected_ref:
                    raise ApplicationError(
                        "the chart result is stale or mismatched; rerun the Tool Action"
                    )
                # One candidate per verified result; a refined candidate keeps its new chart type.
                artifact = next(
                    (
                        item
                        for item in existing
                        if item.source_result_ref == rendered.source_result_ref
                    ),
                    None,
                )
                if artifact is None:
                    try:
                        artifact = self._artifact_store.create_candidate(
                            current_workspace.session,
                            rendered,
                            insight_ids=_insight_ids_for_source(state, rendered),
                        )
                    except ArtifactStoreError as exc:
                        raise ApplicationError(f"could not publish the chart: {exc}") from exc
                    existing = (*existing, artifact)
                    # Only a new candidate changes the saved session; viewing a result does not.
                    self._save_workspace(current_workspace)
                publications.append(artifact)
        return tuple(publications)

    def list_candidates(self, workspace: AnalysisWorkspace) -> tuple[AnalyticalArtifact, ...]:
        return self._artifact_store.list_candidates(workspace.session)

    def list_dashboard(self, workspace: AnalysisWorkspace) -> tuple[AnalyticalArtifact, ...]:
        return self._artifact_store.list_dashboard(workspace.session)

    def pin(self, workspace: AnalysisWorkspace, artifact_id: UUID) -> AnalyticalArtifact:
        try:
            return self._artifact_store.pin(workspace.session, artifact_id)
        except ArtifactStoreError as exc:
            raise ApplicationError(f"could not pin the chart: {exc}") from exc

    def unpin(self, workspace: AnalysisWorkspace, artifact_id: UUID) -> AnalyticalArtifact:
        try:
            return self._artifact_store.unpin(workspace.session.session_id, artifact_id)
        except ArtifactStoreError as exc:
            raise ApplicationError(f"could not unpin the chart: {exc}") from exc

    def refine_candidate(
        self,
        workspace: AnalysisWorkspace,
        artifact_id: UUID,
        artifact_type: ArtifactType,
    ) -> AnalyticalArtifact:
        """Show a candidate's verified result as another chart type, without a model call."""
        session = workspace.session
        try:
            artifact = self._artifact_store.get(session.session_id, artifact_id)
        except ArtifactStoreError as exc:
            raise ApplicationError(f"could not change the chart: {exc}") from exc
        with self._open_orchestrator(workspace) as orchestrator:
            evidence = orchestrator.query_evidence(
                str(session.session_id), str(artifact.source_result_ref.query_id)
            )
        if evidence is None:
            raise ApplicationError("the verified result behind this chart is no longer available")
        result = QueryResult.model_validate(evidence[0])
        action = ToolAction.model_validate(evidence[1])
        _require_current_query_evidence(workspace, result, action)
        expected_ref = make_query_result_reference(result, session.semantic_annotations)
        if artifact.source_result_ref != expected_ref:
            raise ApplicationError("the chart result is stale or mismatched; rerun the Tool Action")
        intent = retarget_chart_intent(artifact.intent, result, artifact_type).model_copy(
            update={
                "source_result_ref": make_query_result_reference(
                    result, session.semantic_annotations
                )
            }
        )
        try:
            rendered = render_chart(
                intent, result, action, semantic_annotations=session.semantic_annotations
            )
            return self._artifact_store.refine_candidate(session, artifact_id, rendered)
        except ChartValidationError as exc:
            raise ApplicationError(
                f"A {artifact_type.value} chart cannot show this result: {exc}"
            ) from exc
        except ArtifactStoreError as exc:
            raise ApplicationError(f"could not change the chart: {exc}") from exc

    def read_render_spec(self, artifact: AnalyticalArtifact) -> dict[str, Any]:
        return self._artifact_store.read_render_spec(artifact)

    def data_overview(self, workspace: AnalysisWorkspace) -> DataOverview:
        """Build the deterministic Data Overview for a workspace; no model is called."""
        return build_data_overview(
            self._workspace_core(workspace), workspace.dataset_handle, workspace.data_profile
        )

    def explorer_charts(
        self, workspace: AnalysisWorkspace, request: ExplorerRequest
    ) -> tuple[OverviewChart, ...]:
        """Build the user's descriptive explorer charts; no model is called."""
        return build_explorer_charts(
            self._workspace_core(workspace),
            workspace.dataset_handle,
            workspace.data_profile,
            request,
        )

    def filter_values(self, workspace: AnalysisWorkspace, field: str) -> tuple[str, ...]:
        return filter_values(
            self._workspace_core(workspace), workspace.dataset_handle, workspace.data_profile, field
        )

    def _workspace_core(self, workspace: AnalysisWorkspace) -> TabularDataCore:
        return TabularDataCore(self._session_directory(workspace.session.session_id), self._limits)

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


def _require_session_id(value: object) -> UUID:
    """Accept a UUID object or its canonical lowercase text, never a path-like value."""
    session_id = canonical_uuid(value)
    if session_id is None:
        raise ApplicationError("session_id must be a UUID or canonical UUID string")
    return session_id


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


def _require_current_query_evidence(
    workspace: AnalysisWorkspace,
    result: QueryResult,
    action: ToolAction,
) -> None:
    """Guard chart publication and refinement with the current, inspected query evidence."""
    expected_reference = f"query-result:{result.query_id}"
    action_is_verified = bool(action.verification_results) and all(
        item.status is VerificationStatus.PASSED for item in action.verification_results
    )
    provenance_valid, provenance_message = query_provenance_status(result)
    valid = (
        action.tool_name == "read_only_sql"
        and action.status is ActionStatus.SUCCEEDED
        and action.output_ref == expected_reference
        and action.working_dataset_version == workspace.dataset_handle.working_dataset_version
        and result.dataset_id == workspace.dataset_handle.dataset.dataset_id
        and result.working_dataset_version == workspace.dataset_handle.working_dataset_version
        and not result.truncated
        and action_is_verified
        and isinstance(action.inputs.get("sql"), str)
        and action.inputs["sql"] == result.sql
        and provenance_valid
    )
    if valid:
        return
    reason = (
        provenance_message if not provenance_valid else "the Tool Action is stale or unverified"
    )
    raise ApplicationError(f"{reason}; rerun the Tool Action before creating a chart")


def _same_statistics(fresh: StatisticalResult, saved: StatisticalResult) -> bool:
    """Compare a rerun statistical result with the saved one, allowing floating-point noise."""

    # A checkpoint created while the product still used reservoir sampling is legacy evidence.
    # It may remain readable, but a full-data rerun must not claim to reproduce that old scope.
    scope_fields = (
        "dataset_row_count",
        "population_row_count",
        "rows_loaded",
        "sampled",
        "sampling_method",
        "sampling_seed",
        "partial",
        "truncated",
    )
    if any(getattr(fresh, field) != getattr(saved, field) for field in scope_fields):
        return False
    if fresh.sampled is not False:
        return False

    def numbers(result: StatisticalResult) -> list[float | None]:
        return [result.statistic, result.p_value, *(item.value for item in result.estimates)]

    same_metrics = [item.metric for item in fresh.estimates] == [
        item.metric for item in saved.estimates
    ]
    return same_metrics and all(
        (left is None and right is None)
        or (
            left is not None
            and right is not None
            and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
        )
        for left, right in zip(numbers(fresh), numbers(saved), strict=True)
    )


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
