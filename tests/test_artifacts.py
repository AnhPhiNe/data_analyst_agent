"""Tests for durable candidate and pinned dashboard artifact lifecycle."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tabular_analytics_agent.data import QueryColumn, QueryResult
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisSession,
    ArtifactStatus,
    ArtifactType,
    ChartIntent,
    SemanticAnnotation,
    SessionStatus,
    ToolAction,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)
from tabular_analytics_agent.visualization import (
    ArtifactNotFoundError,
    ArtifactStore,
    ArtifactStoreError,
    ArtifactTransitionError,
    ChartRenderResult,
    make_query_result_reference,
    render_chart,
)


def make_rendered_chart(
    *,
    title: str = "Revenue by month",
    dataset_id: UUID | None = None,
    working_dataset_version: int = 1,
) -> ChartRenderResult:
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=dataset_id or uuid4(),
        working_dataset_version=working_dataset_version,
        sql="SELECT month, SUM(revenue) AS revenue FROM dataset GROUP BY month",
        columns=(
            QueryColumn(name="month", data_type="DATE"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
        ),
        rows=(("2026-01-01", 100.0), ("2026-02-01", 150.0)),
        row_count=2,
        truncated=False,
        duration_ms=2,
    )
    source_verification = VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="query", passed=True, message="Query passed."),),
    )
    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=working_dataset_version,
        inputs={"sql": result.sql, "required_fields": ["month", "revenue"]},
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{result.query_id}",
        duration_ms=2,
        verification_results=(source_verification,),
    )
    intent = ChartIntent(
        artifact_type=ArtifactType.BAR,
        analytical_purpose="Compare monthly revenue",
        source_result_ref=make_query_result_reference(result),
        x_field="month",
        y_fields=("revenue",),
        title=title,
        labels={"month": "Month", "revenue": "Revenue"},
    )
    return render_chart(intent, result, action)


def make_store(root: Path) -> ArtifactStore:
    return ArtifactStore(root / "metadata.sqlite", root / "render-specs")


def make_session(
    rendered: ChartRenderResult,
    *,
    session_id: UUID | None = None,
) -> AnalysisSession:
    now = datetime.now(UTC)
    return AnalysisSession(
        session_id=session_id or uuid4(),
        status=SessionStatus.RUNNING,
        source_dataset_id=rendered.source_result_ref.dataset_id,
        working_dataset_version=rendered.source_result_ref.working_dataset_version,
        created_at=now,
        updated_at=now,
    )


def test_create_candidate_is_durable_and_keeps_exact_source_link(tmp_path: Path) -> None:
    rendered = make_rendered_chart()
    session = make_session(rendered)
    store = make_store(tmp_path)

    artifact = store.create_candidate(session, rendered, insight_ids=(uuid4(),))

    assert artifact.status is ArtifactStatus.CANDIDATE
    assert artifact.source_result_ref == rendered.source_result_ref
    assert artifact.intent.source_result_ref == rendered.source_result_ref
    assert artifact.version == 1
    assert store.list_dashboard(session) == ()
    assert store.list_candidates(session) == (artifact,)

    restarted = make_store(tmp_path)
    assert restarted.get(session.session_id, artifact.artifact_id) == artifact
    payload = restarted.read_render_spec(artifact)
    assert payload["schema_version"] == 1
    assert payload["artifact_id"] == str(artifact.artifact_id)
    assert payload["source_result_ref"]["query_id"] == str(rendered.source_result_ref.query_id)
    assert payload["plotly_spec"] == rendered.plotly_spec
    assert not tuple((tmp_path / "render-specs").rglob("*.tmp"))


def test_pin_and_unpin_control_dashboard_membership(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    session = make_session(rendered)
    candidate = store.create_candidate(session, rendered)

    pinned = store.pin(session, candidate.artifact_id)

    assert pinned.status is ArtifactStatus.PINNED
    assert pinned.version == candidate.version
    assert store.list_candidates(session) == ()
    assert store.list_dashboard(session) == (pinned,)
    with pytest.raises(ArtifactTransitionError, match="must be candidate"):
        store.pin(session, candidate.artifact_id)

    unpinned = store.unpin(session.session_id, candidate.artifact_id)
    assert unpinned.status is ArtifactStatus.CANDIDATE
    assert store.list_candidates(session) == (unpinned,)
    assert store.list_dashboard(session) == ()


def test_refine_candidate_creates_a_new_immutable_render_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    dataset_id = uuid4()
    original_render = make_rendered_chart(title="Original", dataset_id=dataset_id)
    session = make_session(original_render)
    original = store.create_candidate(session, original_render)
    original_payload = store.read_render_spec(original)

    revised_render = make_rendered_chart(title="Revised", dataset_id=dataset_id)
    revised = store.refine_candidate(session, original.artifact_id, revised_render)

    assert revised.artifact_id == original.artifact_id
    assert revised.created_at == original.created_at
    assert revised.updated_at >= original.updated_at
    assert revised.version == 2
    assert revised.render_spec_ref != original.render_spec_ref
    assert revised.source_result_ref == revised_render.source_result_ref
    assert store.read_render_spec(original) == original_payload
    assert store.read_render_spec(revised)["plotly_spec"]["layout"]["title"]["text"] == "Revised"
    assert store.list_candidates(session) == (revised,)


def test_pinned_artifact_cannot_be_refined_until_unpinned(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    dataset_id = uuid4()
    rendered = make_rendered_chart(dataset_id=dataset_id)
    session = make_session(rendered)
    artifact = store.create_candidate(session, rendered)
    store.pin(session, artifact.artifact_id)

    with pytest.raises(ArtifactTransitionError, match="only candidate"):
        store.refine_candidate(
            session,
            artifact.artifact_id,
            make_rendered_chart(dataset_id=dataset_id),
        )


def test_artifacts_are_isolated_by_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    owner_session = make_session(rendered)
    other_session = make_session(rendered)
    artifact = store.create_candidate(owner_session, rendered)

    with pytest.raises(ArtifactNotFoundError, match="this session"):
        store.get(other_session.session_id, artifact.artifact_id)
    with pytest.raises(ArtifactNotFoundError, match="this session"):
        store.pin(other_session, artifact.artifact_id)
    assert store.list_artifacts(other_session.session_id) == ()


def test_publication_and_dashboard_require_current_session_source(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    session = make_session(rendered)
    stale_session = session.model_copy(update={"working_dataset_version": 2})
    wrong_dataset_session = session.model_copy(update={"source_dataset_id": uuid4()})
    reinterpreted_session = session.model_copy(
        update={
            "semantic_annotations": (
                SemanticAnnotation(
                    field_name="revenue",
                    meaning="Net revenue after refunds",
                    unit="USD",
                    role="measure",
                    confirmed_by_user=True,
                ),
            )
        }
    )

    for invalid_session in (stale_session, wrong_dataset_session, reinterpreted_session):
        with pytest.raises(ArtifactTransitionError, match="stale or does not belong"):
            store.create_candidate(invalid_session, rendered)

    candidate = store.create_candidate(session, rendered)
    with pytest.raises(ArtifactTransitionError, match="stale or does not belong"):
        store.pin(stale_session, candidate.artifact_id)
    with pytest.raises(ArtifactTransitionError, match="stale or does not belong"):
        store.pin(reinterpreted_session, candidate.artifact_id)

    pinned = store.pin(session, candidate.artifact_id)
    assert store.list_dashboard(session) == (pinned,)
    assert store.list_dashboard(stale_session) == ()
    assert store.list_dashboard(reinterpreted_session) == ()


def test_unverified_render_is_never_published(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    session = make_session(rendered)
    failed = VerificationResult(
        status=VerificationStatus.FAILED,
        checks=(VerificationCheck(name="chart", passed=False, message="Chart failed."),),
    )
    unverified = rendered.model_copy(update={"verification": failed})

    with pytest.raises(ArtifactTransitionError, match="only verified"):
        store.create_candidate(session, unverified)

    assert store.list_artifacts(session.session_id) == ()
    assert not tuple((tmp_path / "render-specs").rglob("*.json"))


def test_missing_artifact_reports_domain_error(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ArtifactNotFoundError):
        store.get(UUID(int=1), UUID(int=2))


def test_corrupt_or_escaping_render_spec_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    artifact = store.create_candidate(make_session(rendered), rendered)
    path = tmp_path / "render-specs" / Path(artifact.render_spec_ref)

    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ArtifactStoreError, match="could not read"):
        store.read_render_spec(artifact)

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ArtifactStoreError, match="JSON object"):
        store.read_render_spec(artifact)

    swapped = {
        "schema_version": 1,
        "artifact_id": str(artifact.artifact_id),
        "artifact_version": artifact.version + 1,
        "source_result_ref": artifact.source_result_ref.model_dump(mode="json"),
        "plotly_spec": {},
        "verification": artifact.verification.model_dump(mode="json"),
    }
    path.write_text(json.dumps(swapped), encoding="utf-8")
    with pytest.raises(ArtifactStoreError, match="does not match its metadata identity"):
        store.read_render_spec(artifact)

    escaping = artifact.model_copy(update={"render_spec_ref": "../outside.json"})
    with pytest.raises(ArtifactStoreError, match="escapes artifact storage"):
        store.read_render_spec(escaping)


@pytest.mark.parametrize("scope", ["sibling-artifact", "sibling-session"])
def test_stored_render_spec_ref_cannot_read_a_sibling(
    tmp_path: Path,
    scope: str,
) -> None:
    store = make_store(tmp_path)
    dataset_id = uuid4()
    first_render = make_rendered_chart(title="First", dataset_id=dataset_id)
    sibling_render = make_rendered_chart(title="Sibling", dataset_id=dataset_id)
    first_session = make_session(first_render)
    sibling_session = first_session if scope == "sibling-artifact" else make_session(sibling_render)
    first = store.create_candidate(first_session, first_render)
    sibling = store.create_candidate(sibling_session, sibling_render)

    with sqlite3.connect(tmp_path / "metadata.sqlite") as connection:
        payload = connection.execute(
            "SELECT payload_json FROM analytical_artifacts WHERE artifact_id = ?",
            (str(first.artifact_id),),
        ).fetchone()
        assert payload is not None
        corrupted = json.loads(str(payload[0]))
        corrupted["render_spec_ref"] = sibling.render_spec_ref
        connection.execute(
            "UPDATE analytical_artifacts SET payload_json = ? WHERE artifact_id = ?",
            (json.dumps(corrupted), str(first.artifact_id)),
        )

    stored = store.get(first_session.session_id, first.artifact_id)
    with pytest.raises(ArtifactStoreError, match="does not belong"):
        store.read_render_spec(stored)
    assert store.read_render_spec(sibling)["artifact_id"] == str(sibling.artifact_id)


def test_delete_session_rejects_paths_before_metadata_or_tree_changes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    session = make_session(rendered)
    artifact = store.create_candidate(session, rendered)
    render_spec = tmp_path / "render-specs" / Path(artifact.render_spec_ref)
    sentinel = tmp_path / "render-specs" / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    invalid_values: tuple[object, ...] = (
        ".",
        str(tmp_path / "render-specs"),
        tmp_path / "render-specs",
        str(session.session_id).upper(),
    )
    for value in invalid_values:
        with pytest.raises(ArtifactStoreError, match="session_id"):
            store.delete_session(value)  # type: ignore[arg-type]

    assert store.get(session.session_id, artifact.artifact_id) == artifact
    assert render_spec.is_file()
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("scope", ["sibling-artifact", "sibling-session"])
def test_metadata_payload_cannot_impersonate_a_sibling(tmp_path: Path, scope: str) -> None:
    store = make_store(tmp_path)
    dataset_id = uuid4()
    first_render = make_rendered_chart(title="First", dataset_id=dataset_id)
    sibling_render = make_rendered_chart(title="Sibling", dataset_id=dataset_id)
    session = make_session(first_render)
    sibling_session = session if scope == "sibling-artifact" else make_session(sibling_render)
    first = store.create_candidate(session, first_render)
    sibling = store.create_candidate(sibling_session, sibling_render)
    with sqlite3.connect(tmp_path / "metadata.sqlite") as connection:
        connection.execute(
            "UPDATE analytical_artifacts SET payload_json = ? WHERE artifact_id = ?",
            (sibling.model_dump_json(), str(first.artifact_id)),
        )

    with pytest.raises(ArtifactStoreError, match="session identity"):
        store.get(session.session_id, first.artifact_id)
    with pytest.raises(ArtifactStoreError, match="session identity"):
        store.list_artifacts(session.session_id)
    with pytest.raises(ArtifactStoreError, match="session identity"):
        store.list_candidates(session)
    assert store.get(sibling_session.session_id, sibling.artifact_id) == sibling
    assert store.read_render_spec(sibling)["artifact_id"] == str(sibling.artifact_id)


def test_failed_atomic_spec_write_never_creates_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    rendered = make_rendered_chart()
    session = make_session(rendered)

    def fail_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(ArtifactStoreError, match="atomically publish"):
        store.create_candidate(session, rendered)

    assert store.list_artifacts(session.session_id) == ()
