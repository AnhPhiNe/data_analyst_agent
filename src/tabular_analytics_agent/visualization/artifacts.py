"""Durable candidate and pinned artifact lifecycle for local dashboards."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from tabular_analytics_agent.domain import (
    AnalysisSession,
    AnalyticalArtifact,
    ArtifactStatus,
    QueryResultReference,
    VerificationStatus,
    canonical_uuid,
    semantic_fingerprint_matches,
)
from tabular_analytics_agent.filesystem import (
    delete_tree,
    is_filesystem_link,
    resolve_within,
    validate_tree,
)
from tabular_analytics_agent.visualization.errors import (
    ArtifactNotFoundError,
    ArtifactStoreError,
    ArtifactTransitionError,
)
from tabular_analytics_agent.visualization.models import ChartRenderResult

_SCHEMA_VERSION = 1


class ArtifactStore:
    """Store artifact metadata in SQLite and immutable Plotly specs as JSON."""

    def __init__(self, database_path: Path, render_spec_root: Path) -> None:
        try:
            if is_filesystem_link(database_path):
                raise ArtifactStoreError(
                    "artifact metadata database must not be a symlink or junction"
                )
            if is_filesystem_link(render_spec_root):
                raise ArtifactStoreError(
                    "artifact render-spec root must not be a symlink or junction"
                )
            self._database_path = database_path.resolve()
            self._render_spec_root = render_spec_root.resolve()
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._render_spec_root.mkdir(parents=True, exist_ok=True)
        except ArtifactStoreError:
            raise
        except (OSError, RuntimeError) as exc:
            raise ArtifactStoreError("could not initialize artifact storage paths") from exc
        self._initialize()

    def create_candidate(
        self,
        session: AnalysisSession,
        rendered: ChartRenderResult,
        *,
        insight_ids: tuple[UUID, ...] = (),
    ) -> AnalyticalArtifact:
        """Publish a verified render as a new candidate artifact."""
        self._require_verified(rendered)
        self._require_current_source(session, rendered.source_result_ref)
        session_id = session.session_id
        now = datetime.now(UTC)
        artifact_id = uuid4()
        render_spec_ref = self._render_spec_ref(session_id, artifact_id, version=1)
        artifact = AnalyticalArtifact(
            artifact_id=artifact_id,
            session_id=session_id,
            status=ArtifactStatus.CANDIDATE,
            intent=rendered.intent,
            source_result_ref=rendered.source_result_ref,
            insight_ids=insight_ids,
            render_spec_ref=render_spec_ref,
            verification=rendered.verification,
            created_at=now,
            updated_at=now,
        )
        spec_path = self._write_render_spec(artifact, rendered.plotly_spec)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO analytical_artifacts (
                        artifact_id, session_id, status, version, source_query_id,
                        dataset_id, working_dataset_version, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._row_values(artifact),
                )
        except sqlite3.Error as exc:
            spec_path.unlink(missing_ok=True)
            raise ArtifactStoreError("could not persist candidate artifact metadata") from exc
        return artifact

    def refine_candidate(
        self,
        session: AnalysisSession,
        artifact_id: UUID,
        rendered: ChartRenderResult,
        *,
        insight_ids: tuple[UUID, ...] | None = None,
    ) -> AnalyticalArtifact:
        """Create the next immutable render version for an existing candidate."""
        self._require_verified(rendered)
        self._require_current_source(session, rendered.source_result_ref)
        session_id = session.session_id
        current = self.get(session_id, artifact_id)
        if current.status is not ArtifactStatus.CANDIDATE:
            raise ArtifactTransitionError("only candidate artifacts can be refined")

        revised = AnalyticalArtifact(
            artifact_id=current.artifact_id,
            session_id=current.session_id,
            status=ArtifactStatus.CANDIDATE,
            intent=rendered.intent,
            source_result_ref=rendered.source_result_ref,
            insight_ids=current.insight_ids if insight_ids is None else insight_ids,
            render_spec_ref=self._render_spec_ref(
                session_id, artifact_id, version=current.version + 1
            ),
            verification=rendered.verification,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
            version=current.version + 1,
        )
        spec_path = self._write_render_spec(revised, rendered.plotly_spec)
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE analytical_artifacts
                    SET status = ?, version = ?, source_query_id = ?, dataset_id = ?,
                        working_dataset_version = ?, payload_json = ?
                    WHERE artifact_id = ? AND session_id = ? AND status = ? AND version = ?
                    """,
                    (
                        revised.status.value,
                        revised.version,
                        str(revised.source_result_ref.query_id),
                        str(revised.source_result_ref.dataset_id),
                        revised.source_result_ref.working_dataset_version,
                        revised.model_dump_json(),
                        str(artifact_id),
                        str(session_id),
                        ArtifactStatus.CANDIDATE.value,
                        current.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ArtifactTransitionError(
                        "candidate changed while the revised artifact was being published"
                    )
        except (sqlite3.Error, ArtifactTransitionError) as exc:
            spec_path.unlink(missing_ok=True)
            if isinstance(exc, ArtifactTransitionError):
                raise
            raise ArtifactStoreError("could not persist refined artifact metadata") from exc
        return revised

    def pin(self, session: AnalysisSession, artifact_id: UUID) -> AnalyticalArtifact:
        """Move a candidate artifact onto the session dashboard."""
        current = self.get(session.session_id, artifact_id)
        self._require_current_source(session, current.source_result_ref)
        return self._transition(
            session.session_id,
            artifact_id,
            expected=ArtifactStatus.CANDIDATE,
            target=ArtifactStatus.PINNED,
        )

    def unpin(self, session_id: UUID, artifact_id: UUID) -> AnalyticalArtifact:
        """Move a pinned artifact back to the candidate collection."""
        return self._transition(
            session_id,
            artifact_id,
            expected=ArtifactStatus.PINNED,
            target=ArtifactStatus.CANDIDATE,
        )

    def get(self, session_id: UUID, artifact_id: UUID) -> AnalyticalArtifact:
        """Load one artifact while enforcing session ownership."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT artifact_id, session_id, payload_json FROM analytical_artifacts
                    WHERE artifact_id = ? AND session_id = ?
                    """,
                    (str(artifact_id), str(session_id)),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ArtifactStoreError("could not read artifact metadata") from exc
        if row is None:
            raise ArtifactNotFoundError("artifact was not found in this session")
        return self._parse_artifact(row)

    def list_artifacts(
        self,
        session_id: UUID,
        *,
        status: ArtifactStatus | None = None,
    ) -> tuple[AnalyticalArtifact, ...]:
        """List a session's artifacts, optionally filtered by lifecycle status."""
        parameters: tuple[str, ...]
        if status is None:
            sql = """
                SELECT artifact_id, session_id, payload_json FROM analytical_artifacts
                WHERE session_id = ? ORDER BY rowid
            """
            parameters = (str(session_id),)
        else:
            sql = """
                SELECT artifact_id, session_id, payload_json FROM analytical_artifacts
                WHERE session_id = ? AND status = ? ORDER BY rowid
            """
            parameters = (str(session_id), status.value)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as exc:
            raise ArtifactStoreError("could not list artifact metadata") from exc
        return tuple(self._parse_artifact(row) for row in rows)

    def delete_session(self, session_id: UUID) -> None:
        """Remove one session's metadata and render-spec namespace only."""
        session_id = _require_session_id(session_id)
        session_spec_root = self._session_render_spec_root(session_id)
        try:
            if session_spec_root.exists():
                validate_tree(
                    session_spec_root,
                    self._render_spec_root,
                    label="artifact render-spec namespace",
                )
        except (ValueError, OSError) as exc:
            raise ArtifactStoreError("artifact render-spec namespace is unsafe to delete") from exc

        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT artifact_id, payload_json FROM analytical_artifacts
                    WHERE session_id = ?
                    """,
                    (str(session_id),),
                ).fetchall()
                for row in rows:
                    try:
                        artifact = AnalyticalArtifact.model_validate_json(str(row["payload_json"]))
                        row_artifact_id = UUID(str(row["artifact_id"]))
                    except (ValidationError, ValueError, TypeError) as exc:
                        raise ArtifactStoreError(
                            "artifact metadata is invalid for the requested session"
                        ) from exc
                    if artifact.artifact_id != row_artifact_id or artifact.session_id != session_id:
                        raise ArtifactStoreError(
                            "artifact metadata does not match its session identity"
                        )
                    self._resolve_render_spec(
                        artifact.render_spec_ref,
                        session_id=session_id,
                        artifact_id=artifact.artifact_id,
                    )
                connection.execute(
                    "DELETE FROM analytical_artifacts WHERE session_id = ?",
                    (str(session_id),),
                )
                connection.commit()
        except ArtifactStoreError:
            raise
        except sqlite3.Error as exc:
            raise ArtifactStoreError("could not delete artifact metadata") from exc

        try:
            delete_tree(
                session_spec_root,
                self._render_spec_root,
                label="artifact render-spec namespace",
            )
        except (ValueError, OSError) as exc:
            raise ArtifactStoreError(
                "could not delete artifact render-spec namespace after metadata removal"
            ) from exc

    def list_candidates(self, session: AnalysisSession) -> tuple[AnalyticalArtifact, ...]:
        """Return current artifacts awaiting user selection."""
        return self._list_current(session, ArtifactStatus.CANDIDATE)

    def list_dashboard(self, session: AnalysisSession) -> tuple[AnalyticalArtifact, ...]:
        """Return explicitly pinned artifacts that are current for the session."""
        return self._list_current(session, ArtifactStatus.PINNED)

    def read_render_spec(self, artifact: AnalyticalArtifact) -> dict[str, Any]:
        """Load the immutable render payload referenced by an artifact record."""
        path = self._resolve_render_spec(
            artifact.render_spec_ref,
            session_id=artifact.session_id,
            artifact_id=artifact.artifact_id,
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError("could not read artifact render specification") from exc
        if not isinstance(payload, dict):
            raise ArtifactStoreError("artifact render specification must be a JSON object")
        expected_identity = {
            "artifact_id": str(artifact.artifact_id),
            "artifact_version": artifact.version,
            "source_result_ref": artifact.source_result_ref.model_dump(mode="json"),
            "verification": artifact.verification.model_dump(mode="json"),
        }
        if any(payload.get(key) != value for key, value in expected_identity.items()):
            raise ArtifactStoreError(
                "artifact render specification does not match its metadata identity"
            )
        return payload

    def _transition(
        self,
        session_id: UUID,
        artifact_id: UUID,
        *,
        expected: ArtifactStatus,
        target: ArtifactStatus,
    ) -> AnalyticalArtifact:
        current = self.get(session_id, artifact_id)
        if current.status is not expected:
            raise ArtifactTransitionError(
                f"artifact must be {expected.value} before it can become {target.value}"
            )
        updated = AnalyticalArtifact.model_validate(
            {
                **current.model_dump(),
                "status": target,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE analytical_artifacts SET status = ?, payload_json = ?
                    WHERE artifact_id = ? AND session_id = ? AND status = ? AND version = ?
                    """,
                    (
                        target.value,
                        updated.model_dump_json(),
                        str(artifact_id),
                        str(session_id),
                        expected.value,
                        current.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ArtifactTransitionError(
                        "artifact changed while its lifecycle status was being updated"
                    )
        except sqlite3.Error as exc:
            raise ArtifactStoreError("could not update artifact lifecycle status") from exc
        return updated

    def _list_current(
        self,
        session: AnalysisSession,
        status: ArtifactStatus,
    ) -> tuple[AnalyticalArtifact, ...]:
        if session.source_dataset_id is None or session.working_dataset_version == 0:
            return ()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, session_id, payload_json FROM analytical_artifacts
                WHERE session_id = ? AND status = ? AND dataset_id = ?
                    AND working_dataset_version = ?
                ORDER BY rowid
                """,
                (
                    str(session.session_id),
                    status.value,
                    str(session.source_dataset_id),
                    session.working_dataset_version,
                ),
            ).fetchall()
        artifacts = (self._parse_artifact(row) for row in rows)
        return tuple(
            artifact
            for artifact in artifacts
            if semantic_fingerprint_matches(
                artifact.source_result_ref.semantic_annotation_fingerprint,
                session.semantic_annotations,
            )
        )

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS analytical_artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('candidate', 'pinned')),
                        version INTEGER NOT NULL CHECK (version > 0),
                        source_query_id TEXT NOT NULL,
                        dataset_id TEXT NOT NULL,
                        working_dataset_version INTEGER NOT NULL CHECK (
                            working_dataset_version > 0
                        ),
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_artifacts_session_status
                    ON analytical_artifacts (session_id, status);
                    """
                )
        except sqlite3.Error as exc:
            raise ArtifactStoreError("could not initialize artifact metadata storage") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _parse_artifact(row: sqlite3.Row) -> AnalyticalArtifact:
        try:
            artifact = AnalyticalArtifact.model_validate_json(str(row["payload_json"]))
        except (ValidationError, ValueError, TypeError) as exc:
            raise ArtifactStoreError("artifact metadata is invalid") from exc
        if (
            str(artifact.artifact_id) != row["artifact_id"]
            or str(artifact.session_id) != row["session_id"]
        ):
            raise ArtifactStoreError("artifact metadata does not match its session identity")
        return artifact

    def _write_render_spec(
        self,
        artifact: AnalyticalArtifact,
        plotly_spec: dict[str, Any],
    ) -> Path:
        path = self._resolve_render_spec(
            artifact.render_spec_ref,
            session_id=artifact.session_id,
            artifact_id=artifact.artifact_id,
        )
        temporary = path.with_name(f".tmp-{uuid4().hex[:8]}")
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "artifact_id": str(artifact.artifact_id),
            "artifact_version": artifact.version,
            "source_result_ref": artifact.source_result_ref.model_dump(mode="json"),
            "plotly_spec": plotly_spec,
            "verification": artifact.verification.model_dump(mode="json"),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ArtifactStoreError("could not atomically publish render specification") from exc
        return path

    def _resolve_render_spec(
        self,
        reference: str,
        *,
        session_id: UUID | None = None,
        artifact_id: UUID | None = None,
    ) -> Path:
        if not isinstance(reference, str) or not reference or "\\" in reference:
            raise ArtifactStoreError("render specification reference escapes artifact storage")
        pure_reference = PurePosixPath(reference)
        parts = pure_reference.parts
        if (
            pure_reference.is_absolute()
            or not parts
            or any(part in {"", ".", ".."} for part in parts)
            or pure_reference.as_posix() != reference
        ):
            raise ArtifactStoreError("render specification reference escapes artifact storage")
        if session_id is not None and (not parts or parts[0] != str(session_id)):
            raise ArtifactStoreError(
                "render specification reference does not belong to its session"
            )
        if artifact_id is not None and (len(parts) < 2 or parts[1] != str(artifact_id)):
            raise ArtifactStoreError(
                "render specification reference does not belong to its artifact"
            )
        candidate = self._render_spec_root.joinpath(*parts)
        return self._resolve_render_path(candidate)

    def _session_render_spec_root(self, session_id: UUID) -> Path:
        session_id = _require_session_id(session_id)
        return self._resolve_render_path(self._render_spec_root / str(session_id))

    def _resolve_render_path(self, candidate: Path) -> Path:
        """Translate the shared path guard's errors at the artifact-store boundary."""
        try:
            return resolve_within(
                self._render_spec_root,
                candidate,
                label="artifact render specification",
            )
        except ValueError as exc:
            if "escapes" in str(exc):
                raise ArtifactStoreError(
                    "render specification reference escapes artifact storage"
                ) from exc
            raise ArtifactStoreError("artifact render-spec path is unsafe") from exc
        except OSError as exc:
            raise ArtifactStoreError("could not resolve artifact render-spec path") from exc

    @staticmethod
    def _render_spec_ref(session_id: UUID, artifact_id: UUID, *, version: int) -> str:
        publication_id = uuid4()
        return f"{session_id}/{artifact_id}/v{version}-{publication_id.hex[:12]}.json"

    @staticmethod
    def _require_verified(rendered: ChartRenderResult) -> None:
        if rendered.verification.status is not VerificationStatus.PASSED:
            raise ArtifactTransitionError("only verified chart renders can become artifacts")

    @staticmethod
    def _require_current_source(
        session: AnalysisSession,
        source: QueryResultReference,
    ) -> None:
        if (
            session.source_dataset_id != source.dataset_id
            or session.working_dataset_version != source.working_dataset_version
            or not semantic_fingerprint_matches(
                source.semantic_annotation_fingerprint, session.semantic_annotations
            )
        ):
            raise ArtifactTransitionError(
                "artifact source is stale or does not belong to the current Analysis Session"
            )

    @staticmethod
    def _row_values(artifact: AnalyticalArtifact) -> tuple[str | int, ...]:
        return (
            str(artifact.artifact_id),
            str(artifact.session_id),
            artifact.status.value,
            artifact.version,
            str(artifact.source_result_ref.query_id),
            str(artifact.source_result_ref.dataset_id),
            artifact.source_result_ref.working_dataset_version,
            artifact.model_dump_json(),
        )


def _require_session_id(value: object) -> UUID:
    """Accept only a UUID object or its canonical lowercase string form."""
    session_id = canonical_uuid(value)
    if session_id is None:
        raise ArtifactStoreError("session_id must be a UUID or canonical UUID string")
    return session_id
