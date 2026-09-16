"""Independent acceptance checks for the MVP full-data statistical contract.

These tests deliberately keep the presentation row cap small.  That makes it possible to prove
that the statistical calculation uses the complete approved input while a query preview stays
bounded for the UI/model boundary.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import tabular_analytics_agent.statistics.service as statistics_service
from tabular_analytics_agent.application import LocalAnalysisApplication
from tabular_analytics_agent.application.exports import (
    ExportRequest,
    ExportValidationError,
    export_verified_insights_json,
)
from tabular_analytics_agent.data import (
    DataCoreLimits,
    DatasetHandle,
    TabularDataCore,
    UnsafeQueryError,
    analyze_read_only_sql,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisSession,
    DataProfile,
    InsightAssertion,
    InsightOperator,
    SessionStatus,
    ToolAction,
    UnsupportedClaim,
    VerifiedInsight,
)
from tabular_analytics_agent.model_gateway import FakeModelGateway
from tabular_analytics_agent.orchestration import AgentRunStatus, AgentState
from tabular_analytics_agent.orchestration.evidence_catalog import insight_evidence_catalog
from tabular_analytics_agent.statistics import (
    InsufficientSampleError,
    StatisticalAnalysisError,
    StatisticalOperation,
    StatisticalParameterError,
    StatisticalRequest,
    StatisticalTimeoutError,
    StatisticalTool,
)
from tabular_analytics_agent.statistics.service import (
    StatisticalToolOutput,
    _raise_worker_error,
    _run_statistical_worker,
)
from tabular_analytics_agent.statistics.worker import statistical_worker_entry
from tabular_analytics_agent.verification import (
    publish_insight,
    query_claim_status,
    query_output_dependencies,
    query_provenance_status,
    result_provenance_metadata,
    statistical_action_status,
    statistical_full_data_status,
    verify_query_evidence,
)
from tests.worker_support import (
    error_statistical_worker,
    invalid_result_statistical_worker,
    slow_statistical_worker,
)


def write_csv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8", newline="")
    return path


def full_data_context(
    tmp_path: Path,
    content: str,
    *,
    max_query_rows: int = 10,
) -> tuple[
    TabularDataCore,
    DatasetHandle,
    DataProfile,
    StatisticalRequest,
    StatisticalToolOutput,
]:
    core = TabularDataCore(
        tmp_path / "session",
        limits=DataCoreLimits(max_query_rows=max_query_rows, query_timeout_seconds=30),
    )
    handle = core.ingest(write_csv(tmp_path / "input.csv", content))
    profile = core.profile(handle)
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("value",),
        random_seed=7,
    )
    output = StatisticalTool(core).execute(handle=handle, profile=profile, request=request)
    return core, handle, profile, request, output


class RecordingSender:
    """Small pipe double for exercising the worker entry point in the parent process."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.closed = False

    def send(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)

    def close(self) -> None:
        self.closed = True


def test_statistical_result_validator_rejects_inconsistent_full_data_metadata(
    tmp_path: Path,
) -> None:
    _core, _handle, _profile, _request, output = full_data_context(tmp_path, "value\n1\n2\n3\n4\n")
    base = output.result.model_dump(mode="json")
    invalid_cases = (
        ({"dataset_row_count": None}, "dataset and population"),
        ({"population_row_count": None}, "dataset and population"),
        ({"rows_loaded": None}, "rows_loaded"),
        ({"rows_loaded": 3}, "rows_loaded"),
        ({"partial": True}, "partial"),
        ({"truncated": True}, "partial"),
        ({"sampling_method": "reservoir"}, "sampling method"),
        ({"sampling_seed": 7}, "sampling method"),
        ({"sample_size": 5}, "sample_size"),
        ({"missing_row_count": 5}, "missing_row_count"),
    )
    for update, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            type(output.result).model_validate({**base, **update})


def test_provenance_guards_reject_invalid_scope_and_action_metadata(tmp_path: Path) -> None:
    _core, _handle, _profile, _request, output = full_data_context(tmp_path, "value\n1\n2\n3\n4\n")
    result = output.result
    invalid_results = (
        (result.model_copy(update={"sampled": None}), "legacy"),
        (result.model_copy(update={"sampled": True}), "sampled"),
        (result.model_copy(update={"sampling_method": "reservoir"}), "sampling method"),
        (result.model_copy(update={"sampling_seed": 7}), "sampling method"),
        (result.model_copy(update={"partial": True}), "partial"),
        (result.model_copy(update={"truncated": True}), "partial"),
        (result.model_copy(update={"rows_loaded": 3}), "every row"),
        (
            result.model_copy(update={"population_row_count": 5, "rows_loaded": 5}),
            "exceed",
        ),
        (result.model_copy(update={"sample_size": 5}), "sample_size"),
        (result.model_copy(update={"missing_row_count": 5}), "missing_row_count"),
    )
    for invalid, message in invalid_results:
        valid, detail = statistical_full_data_status(invalid)
        assert not valid
        assert message in detail.lower()

    valid, detail = statistical_action_status(output.action, result)
    assert valid, detail
    missing_action = output.action.model_copy(
        update={
            "inputs": {
                key: value for key, value in output.action.inputs.items() if key != "sampled"
            }
        }
    )
    valid, detail = statistical_action_status(missing_action, result)
    assert not valid
    assert "sampled" in detail
    mismatched_action = output.action.model_copy(
        update={"inputs": {**output.action.inputs, "rows_loaded": 3}}
    )
    valid, detail = statistical_action_status(mismatched_action, result)
    assert not valid
    assert "rows_loaded" in detail


def test_query_provenance_guards_cover_legacy_mismatch_and_claim_failures(tmp_path: Path) -> None:
    _core, _handle, _profile, _request, output = full_data_context(tmp_path, "value\n1\n2\n3\n4\n")
    result = output.input_query
    valid, detail = query_provenance_status(result)
    assert valid, detail
    assert query_output_dependencies(result, "row[0].VALUE") == ("value",)
    assert query_output_dependencies(result, "value") is None
    assert query_claim_status(result, ("row[0].value",), profile_fields={"value"})[0]
    assert result_provenance_metadata(result)["provenance_version"] == "v1"

    legacy = result.model_copy(update={"inspection": None})
    assert not query_provenance_status(legacy)[0]
    assert query_output_dependencies(legacy, "row[0].value") is None
    mismatched = result.model_copy(update={"sql": "SELECT value FROM dataset"})
    assert not query_provenance_status(mismatched)[0]
    assert result.inspection is not None
    assert not query_provenance_status(
        result.model_copy(
            update={
                "inspection": result.inspection.model_copy(update={"base_relations": ()}),
            }
        )
    )[0]
    assert result_provenance_metadata(legacy)["provenance_version"] is None

    truncated = result.model_copy(update={"truncated": True})
    valid, detail = query_claim_status(truncated, ("result.row_count",), profile_fields={"value"})
    assert not valid
    assert "truncated" in detail
    valid, detail = query_claim_status(result, ("row[0].missing",), profile_fields={"value"})
    assert not valid
    assert "not a deterministic" in detail
    valid, detail = query_claim_status(result, ("row[0].value",), profile_fields={"other"})
    assert not valid
    assert "unverified source" in detail


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT ( FROM dataset", "could not be parsed"),
        ("SELECT COLUMNS(*) FROM dataset", "Dynamic COLUMNS"),
        ("SELECT read_csv_auto('file.csv') FROM dataset", "External-access"),
        ("SELECT random() FROM dataset", "Volatile"),
        ("SELECT 1 AS value", "must reference the session dataset"),
        (
            "WITH dataset AS (SELECT 1 AS value) SELECT value FROM dataset",
            "cannot be shadowed",
        ),
        ("SELECT 1 AS value FROM other", "outside this session"),
    ],
)
def test_sql_policy_rejects_parse_external_and_non_dataset_scopes(sql: str, message: str) -> None:
    """Unsafe SQL must fail before execution, preserving reproducible evidence boundaries."""
    with pytest.raises(UnsafeQueryError, match=message):
        analyze_read_only_sql(sql, allowed_table="dataset")


def test_statistical_output_and_worker_entry_reject_bad_child_results(tmp_path: Path) -> None:
    _core, _handle, _profile, request, output = full_data_context(tmp_path, "value\n1\n2\n3\n4\n")
    with pytest.raises(ValueError, match="versions"):
        StatisticalToolOutput.model_validate(
            {
                **output.model_dump(mode="python"),
                "input_query": output.input_query.model_copy(update={"working_dataset_version": 2}),
            }
        )
    with pytest.raises(ValueError, match="full-input count"):
        StatisticalToolOutput.model_validate(
            {**output.model_dump(mode="python"), "full_input_row_count": 3}
        )
    with pytest.raises(ValueError, match="does not support sampled"):
        StatisticalToolOutput.model_validate({**output.model_dump(mode="python"), "sampled": True})

    sender = RecordingSender()
    statistical_worker_entry(
        rows=((1.0,), (2.0,), (3.0,), (4.0,)),
        columns=("value",),
        request_payload=request.model_dump(mode="json"),
        sender=sender,  # type: ignore[arg-type]
    )
    assert sender.closed
    assert sender.payloads[0]["ok"] is True, sender.payloads[0]
    invalid_sender = RecordingSender()
    statistical_worker_entry(
        rows=((1.0,), (2.0,)),
        columns=("value",),
        request_payload={"operation": "unknown"},
        sender=invalid_sender,  # type: ignore[arg-type]
    )
    assert invalid_sender.closed
    assert invalid_sender.payloads[0]["ok"] is False
    assert "error_type" in invalid_sender.payloads[0]


def test_spawned_worker_error_and_invalid_result_are_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_input: dict[str, Any] = {
        "rows": ((1.0,), (2.0,)),
        "columns": ("value",),
        "request_payload": {"operation": "descriptive", "value_fields": ["value"]},
    }
    monkeypatch.setattr(statistics_service, "statistical_worker_entry", error_statistical_worker)
    with pytest.raises(InsufficientSampleError, match="no usable values"):
        _run_statistical_worker(**worker_input, deadline=time.perf_counter() + 5.0)

    monkeypatch.setattr(
        statistics_service,
        "statistical_worker_entry",
        invalid_result_statistical_worker,
    )
    with pytest.raises(StatisticalAnalysisError, match="invalid result"):
        _run_statistical_worker(**worker_input, deadline=time.perf_counter() + 5.0)


def test_worker_error_mapping_and_expired_deadline_are_typed() -> None:
    for error_type, error_class in (
        ("InsufficientSampleError", InsufficientSampleError),
        ("StatisticalParameterError", StatisticalParameterError),
        ("StatisticalTimeoutError", StatisticalTimeoutError),
    ):
        with pytest.raises(error_class):
            _raise_worker_error({"error_type": error_type, "error": "typed child failure"})
    with pytest.raises(StatisticalAnalysisError, match="unknown child failure"):
        _raise_worker_error({"error_type": "OtherError", "error": "unknown child failure"})
    with pytest.raises(StatisticalTimeoutError, match="deadline"):
        _run_statistical_worker(
            rows=((1.0,), (2.0,)),
            columns=("value",),
            request_payload={"operation": "descriptive", "value_fields": ["value"]},
            deadline=time.perf_counter() - 1,
        )


def test_10001_rows_are_all_analyzed_while_preview_stays_bounded(tmp_path: Path) -> None:
    content = "value\n" + ("0\n" * 10_000) + "10001\n"
    _core, handle, profile, request, output = full_data_context(tmp_path, content)

    mean = next(
        estimate.value for estimate in output.result.estimates if estimate.metric == "value.mean"
    )
    assert mean == pytest.approx(1.0, abs=1e-12)
    assert output.result.sample_size == 10_001
    assert output.result.missing_row_count == 0
    assert output.result.dataset_row_count == 10_001
    assert output.result.population_row_count == 10_001
    assert output.result.rows_loaded == 10_001
    assert output.result.sampled is False
    assert output.result.sampling_method is None
    assert output.result.sampling_seed is None
    assert output.result.partial is False
    assert output.result.truncated is False

    # The presentation adapter is intentionally capped, but its SQL has no sampling/limit clause
    # and the statistical result retains the full execution count.
    assert output.input_query.row_count == 10
    assert output.input_query.truncated is True
    assert "SAMPLE" not in output.input_query.sql.upper()
    assert "LIMIT" not in output.input_query.sql.upper()
    assert output.action.inputs["dataset_row_count"] == 10_001
    assert output.action.inputs["population_row_count"] == 10_001
    assert output.action.inputs["rows_loaded"] == 10_001
    assert output.action.inputs["sampled"] is False
    assert output.action.inputs["sampling_method"] is None
    assert output.action.inputs["sampling_seed"] is None
    assert output.action.inputs["partial"] is False
    assert output.action.inputs["truncated"] is False
    assert "sampling" not in output.action.inputs
    assert "maximum_rows" not in json.dumps(output.action.inputs)

    # Keep these variables in the setup contract: the result must belong to this profiled handle.
    assert output.input_query.dataset_id == profile.dataset.dataset_id == handle.dataset.dataset_id
    assert output.result.source_fields == request.source_fields


def test_missing_values_are_counted_from_execution_and_filter_scope_is_full_data(
    tmp_path: Path,
) -> None:
    content = "value,region\n1,North\n,North\n3,South\n5,South\n"
    core = TabularDataCore(
        tmp_path / "session",
        limits=DataCoreLimits(max_query_rows=1, query_timeout_seconds=30),
    )
    handle = core.ingest(write_csv(tmp_path / "input.csv", content))
    profile = core.profile(handle)

    # This exercises the existing SQL analysis scope.  No statistical filter API is introduced.
    filtered = core.read_full(
        handle,
        "SELECT value FROM dataset WHERE region = 'South'",
    )
    assert filtered.dataset_row_count == 4
    assert filtered.population_row_count == 2
    assert filtered.row_count == 2
    assert filtered.rows == ((3,), (5,))

    output = StatisticalTool(core).execute(
        handle=handle,
        profile=profile,
        request=StatisticalRequest(
            operation=StatisticalOperation.DESCRIPTIVE,
            value_fields=("value",),
        ),
    )
    mean = next(
        estimate.value for estimate in output.result.estimates if estimate.metric == "value.mean"
    )
    assert mean == pytest.approx(3.0)
    assert output.result.dataset_row_count == 4
    assert output.result.population_row_count == 4
    assert output.result.rows_loaded == 4
    assert output.result.sample_size == 3
    assert output.result.missing_row_count == 1
    assert output.result.usable_counts_by_field == {"value": 3}
    assert output.result.excluded_counts_by_field == {"value": 1}


def test_full_data_metadata_reaches_evidence_catalog_trail_and_json_export(tmp_path: Path) -> None:
    content = "value\n1\n2\n3\n4\n"
    _core, handle, profile, _, output = full_data_context(tmp_path, content, max_query_rows=2)
    insight = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="value.mean"),
        evidence_metrics=("value.mean",),
        caveats=(),
        profile=profile,
        action=output.action,
        result=output.result,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(insight, VerifiedInsight)
    evidence = insight.evidence
    assert evidence.provenance_version == "v1"
    assert evidence.dataset_row_count == 4
    assert evidence.population_row_count == 4
    assert evidence.rows_loaded == 4
    assert evidence.sample_size == 4
    assert evidence.missing_row_count == 0
    assert evidence.sampled is False
    assert evidence.sampling_method is None
    assert evidence.sampling_seed is None
    assert evidence.partial is False
    assert evidence.truncated is False

    state: AgentState = {
        "tool_actions": [output.action.model_dump(mode="json")],
        "statistical_results": [output.result.model_dump(mode="json")],
    }
    catalog = insight_evidence_catalog(state, profile)
    assert len(catalog) == 1
    assert catalog[0]["provenance"] == {
        "provenance_version": "v1",
        "dataset_row_count": 4,
        "population_row_count": 4,
        "rows_loaded": 4,
        "sample_size": 4,
        "missing_row_count": 0,
        "sampled": False,
        "sampling_method": None,
        "sampling_seed": None,
        "partial": False,
        "truncated": False,
    }

    now = datetime.now(UTC)
    session = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.COMPLETED,
        source_dataset_id=handle.dataset.dataset_id,
        working_dataset_version=handle.working_dataset_version,
        created_at=now,
        updated_at=now,
    )
    request = ExportRequest(
        session=session,
        profile=profile,
        verified_insights=(insight,),
        tool_actions=(output.action,),
        statistical_results=(output.result,),
    )
    payload = json.loads(export_verified_insights_json(request))
    exported_evidence = payload["verified_insights"][0]["evidence"]
    exported_result = payload["results"][0]
    for name, expected in {
        "dataset_row_count": 4,
        "population_row_count": 4,
        "rows_loaded": 4,
        "sample_size": 4,
        "missing_row_count": 0,
        "sampled": False,
        "sampling_method": None,
        "sampling_seed": None,
        "partial": False,
        "truncated": False,
    }.items():
        assert exported_evidence[name] == expected
        assert exported_result[name] == expected
    assert exported_action_metadata(payload)["inputs"]["sampled"] is False
    assert exported_action_metadata(payload)["inputs"]["sampling_method"] is None
    assert "maximum_rows" not in json.dumps(exported_action_metadata(payload)["inputs"])


def exported_action_metadata(payload: dict[str, object]) -> dict[str, Any]:
    actions = payload["tool_actions"]
    assert isinstance(actions, list) and len(actions) == 1
    action = actions[0]
    assert isinstance(action, dict)
    return action


def test_legacy_sampled_evidence_cannot_be_published_or_exported(tmp_path: Path) -> None:
    _core, handle, profile, request, output = full_data_context(tmp_path, "value\n1\n2\n3\n4\n")
    legacy_result = output.result.model_copy(
        update={
            "sampled": True,
            "sampling_method": "reservoir",
            "sampling_seed": 7,
        }
    )
    legacy_action = output.action.model_copy(
        update={
            "inputs": {
                "request": request.reproducible_parameters(),
                "sampling": {
                    "method": "reservoir",
                    "maximum_rows": 10_000,
                    "random_seed": 7,
                    "sampled": True,
                },
            },
            "output_ref": f"statistical-result:{legacy_result.result_id}",
        }
    )
    publication = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="value.mean"),
        evidence_metrics=("value.mean",),
        caveats=(),
        profile=profile,
        action=legacy_action,
        result=legacy_result,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(publication, UnsupportedClaim)
    assert "sampled" in publication.reason.lower() or "full-data" in publication.reason.lower()

    # A hand-constructed legacy VerifiedInsight remains readable in memory, but the export gate
    # must refuse it until the statistical Tool Action is rerun under the current contract.
    current_publication = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="value.mean"),
        evidence_metrics=("value.mean",),
        caveats=(),
        profile=profile,
        action=output.action,
        result=output.result,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(current_publication, VerifiedInsight)
    legacy_evidence = current_publication.evidence.model_copy(
        update={
            "sampled": True,
            "sampling_method": "reservoir",
            "sampling_seed": 7,
        }
    )
    legacy_verified = VerifiedInsight.model_construct(
        insight_id=uuid4(),
        claim="legacy claim",
        evidence=legacy_evidence,
        verification=output.action.verification_results[0],
    )
    now = datetime.now(UTC)
    session = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.COMPLETED,
        source_dataset_id=handle.dataset.dataset_id,
        working_dataset_version=handle.working_dataset_version,
        created_at=now,
        updated_at=now,
    )
    request_export = ExportRequest(
        session=session,
        profile=profile,
        verified_insights=(legacy_verified,),
        tool_actions=(legacy_action,),
        statistical_results=(legacy_result,),
    )
    with pytest.raises(ExportValidationError, match=r"sampled|full-data"):
        export_verified_insights_json(request_export)


def test_literal_metric_cannot_be_published_but_literal_label_can_accompany_aggregate(
    tmp_path: Path,
) -> None:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(write_csv(tmp_path / "input.csv", "revenue\n100\n200\n"))
    profile = core.profile(handle)

    literal = core.query(handle, "SELECT 1688 AS value FROM dataset")
    literal_action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=handle.working_dataset_version,
        inputs={"sql": literal.sql, "required_fields": ["revenue"]},
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{literal.query_id}",
        verification_results=(
            verify_query_evidence(
                profile=profile,
                result=literal,
                source_fields=("revenue",),
                current_working_dataset_version=handle.working_dataset_version,
            ),
        ),
    )
    publication = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].value"),
        evidence_metrics=("row[0].value",),
        caveats=(),
        profile=profile,
        action=literal_action,
        result=literal,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(publication, UnsupportedClaim)
    assert "literal" in publication.reason.lower() or "dependency" in publication.reason.lower()

    aggregate = core.query(
        handle,
        "SELECT 'Revenue' AS metric, SUM(revenue) AS value FROM dataset",
    )
    aggregate_action = literal_action.model_copy(
        update={
            "action_id": uuid4(),
            "inputs": {"sql": aggregate.sql, "required_fields": ["revenue"]},
            "output_ref": f"query-result:{aggregate.query_id}",
            "verification_results": (
                verify_query_evidence(
                    profile=profile,
                    result=aggregate,
                    source_fields=("revenue",),
                    current_working_dataset_version=handle.working_dataset_version,
                ),
            ),
        }
    )
    valid = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].value"),
        evidence_metrics=("row[0].value",),
        caveats=(),
        profile=profile,
        action=aggregate_action,
        result=aggregate,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(valid, VerifiedInsight)


def test_local_application_rejects_fabricated_literal_metric_end_to_end(tmp_path: Path) -> None:
    outputs: list[dict[str, object]] = [
        {
            "goal_text": "Report the value",
            "goal_family": "summary",
            "requested_metric_mappings": [],
            "semantic_annotations": [],
            "clarification_question": None,
        },
        {
            "steps": [
                {
                    "step_id": "fabricated-value",
                    "description": "Read the requested value",
                    "expected_tool": "read_only_sql",
                    "required_fields": ["revenue"],
                    "intended_output": "A reported value",
                    "caveats": [],
                    "requires_approval": True,
                }
            ]
        },
        {"sql": "SELECT 1688 AS value FROM dataset"},
        {
            "insights": [
                {
                    "plan_step_id": "fabricated-value",
                    "assertion": {"operator": "reports", "left_metric": "row[0].value"},
                    "evidence_metrics": ["row[0].value"],
                    "caveats": [],
                }
            ]
        },
    ]
    application = LocalAnalysisApplication(tmp_path / "app-data", FakeModelGateway(outputs))
    workspace = application.ingest(application.stage_upload("sales.csv", b"revenue\n10\n20\n"))

    paused = application.start(workspace, "Report the value")
    assert paused["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL
    completed = application.resume(workspace, True)

    assert completed["status"] == AgentRunStatus.COMPLETED
    assert completed["verified_insights"] == []
    assert len(completed["unsupported_claims"]) == 1
    reason = completed["unsupported_claims"][0]["reason"]
    assert isinstance(reason, str)
    assert "literal" in reason.lower() or "dependency" in reason.lower()
    assert application.publish_candidates(workspace, completed) == ()


def test_statistical_worker_timeout_terminates_spawned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "worker-started.pid"
    monkeypatch.setenv("TABULAR_TEST_WORKER_MARKER", str(marker))
    monkeypatch.setattr(statistics_service, "statistical_worker_entry", slow_statistical_worker)
    rows = ((0.0,),)
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("value",),
    )
    with pytest.raises(StatisticalTimeoutError, match=r"worker terminated"):
        _run_statistical_worker(
            rows=rows,
            columns=("value",),
            request_payload=request.model_dump(mode="json"),
            deadline=time.perf_counter() + 2.0,
        )
    assert marker.is_file()
    worker_pid = int(marker.read_text(encoding="ascii"))
    assert not any(child.pid == worker_pid and child.is_alive() for child in mp.active_children())
