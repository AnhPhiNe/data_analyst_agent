"""Focused tests for in-memory CSV and Verified Insight metadata exports."""

from __future__ import annotations

import codecs
import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.application import LocalAnalysisApplication
from tabular_analytics_agent.application.exports import (
    ExportRequest,
    ExportValidationError,
    evidence_query_results,
    export_query_result_csv,
    export_verified_insights_json,
)
from tabular_analytics_agent.data import QueryColumn, QueryResult, TabularDataCore
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisSession,
    DataProfile,
    InsightAssertion,
    InsightOperator,
    SemanticAnnotation,
    SessionStatus,
    ToolAction,
    VerifiedInsight,
    fingerprint_semantic_annotations,
)
from tabular_analytics_agent.model_gateway import FakeModelGateway
from tabular_analytics_agent.orchestration import ApprovalDecision
from tabular_analytics_agent.statistics import (
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
    StatisticalTool,
)
from tabular_analytics_agent.verification import (
    publish_insight,
    verify_query_evidence,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_sales.csv"


def context(
    tmp_path: Path,
) -> tuple[AnalysisSession, DataProfile, ToolAction, QueryResult, VerifiedInsight]:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(FIXTURE)
    profile = core.profile(handle)
    annotations = (
        SemanticAnnotation(
            field_name="revenue",
            meaning="Gross revenue",
            unit="USD",
            confirmed_by_user=True,
        ),
    )
    now = datetime.now(UTC)
    session = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.COMPLETED,
        source_dataset_id=handle.dataset.dataset_id,
        working_dataset_version=1,
        semantic_annotations=annotations,
        created_at=now,
        updated_at=now,
    )
    sql = (
        "SELECT region, SUM(revenue) AS revenue FROM dataset GROUP BY region ORDER BY revenue DESC"
    )
    result = core.query(handle, sql)
    verification = verify_query_evidence(
        profile=profile,
        result=result,
        source_fields=("region", "revenue"),
        current_working_dataset_version=handle.working_dataset_version,
    )
    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=1,
        inputs={
            "plan_step_id": "regional-totals",
            "tool_name": "read_only_sql",
            "purpose": "Tổng doanh thu theo khu vực",
            "sql": sql,
            "required_fields": ["region", "revenue"],
        },
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{result.query_id}",
        verification_results=(verification,),
    )
    insight = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.GREATER_THAN,
            left_metric="row[0].revenue",
            right_metric="row[1].revenue",
        ),
        evidence_metrics=("row[0].revenue", "row[1].revenue"),
        caveats=("Chỉ bao gồm các dòng đã tải lên.",),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
        semantic_annotations=annotations,
    )
    assert isinstance(insight, VerifiedInsight)
    return session, profile, action, result, insight


def statistical_context(
    tmp_path: Path,
) -> tuple[AnalysisSession, DataProfile, ToolAction, StatisticalResult, VerifiedInsight]:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(FIXTURE)
    profile = core.profile(handle)
    now = datetime.now(UTC)
    session = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.COMPLETED,
        source_dataset_id=handle.dataset.dataset_id,
        working_dataset_version=handle.working_dataset_version,
        created_at=now,
        updated_at=now,
    )
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("revenue",),
        random_seed=17,
    )
    output = StatisticalTool(core).execute(handle=handle, profile=profile, request=request)
    insight = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="revenue.mean"),
        evidence_metrics=("revenue.mean",),
        caveats=(),
        profile=profile,
        action=output.action,
        result=output.result,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(insight, VerifiedInsight)
    return session, profile, output.action, output.result, insight


def json_state(
    session: AnalysisSession,
    action: ToolAction,
    insight: VerifiedInsight,
    result: QueryResult,
) -> dict[str, object]:
    return {
        "session_id": str(session.session_id),
        "verified_insights": [insight.model_dump(mode="json")],
        "tool_actions": [action.model_dump(mode="json")],
        "query_results": [result.model_dump(mode="json")],
        "model_traces": [{"prompt": "must not be exported"}],
    }


def test_csv_preserves_unicode_quotes_none_and_newlines(tmp_path: Path) -> None:
    _, _, _, result, _ = context(tmp_path)
    result = result.model_copy(
        update={
            "columns": (
                QueryColumn(name="label", data_type="VARCHAR"),
                QueryColumn(name="value", data_type="DOUBLE"),
            ),
            "rows": (("Hà Nội,\ntrung tâm", None),),
            "row_count": 1,
        }
    )

    payload = export_query_result_csv(result)
    assert payload.startswith(codecs.BOM_UTF8)
    decoded = payload.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(decoded, newline="")))

    assert rows == [["label", "value"], ["Hà Nội,\ntrung tâm", ""]]


def test_csv_neutralizes_formula_strings_by_default_but_can_preserve_raw_text(
    tmp_path: Path,
) -> None:
    _, _, _, result, _ = context(tmp_path)
    result = result.model_copy(
        update={
            "columns": (QueryColumn(name="=header", data_type="VARCHAR"),),
            "rows": (("=1+1",), (" +SUM(A1)",), ("-not-a-number",), ("@cmd",), (-5,)),
            "row_count": 5,
        }
    )

    def read(payload: bytes) -> list[list[str]]:
        return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))

    safe = read(export_query_result_csv(result))
    raw = read(export_query_result_csv(result, neutralize_formula_injection=False))

    assert safe[0] == ["'=header"]
    assert raw[0] == ["=header"]
    assert [row[0] for row in safe[1:]] == ["'=1+1", "' +SUM(A1)", "'-not-a-number", "'@cmd", "-5"]
    assert [row[0] for row in raw[1:]] == ["=1+1", " +SUM(A1)", "-not-a-number", "@cmd", "-5"]


def test_json_exports_current_verified_scope_without_rows_or_traces(tmp_path: Path) -> None:
    session, profile, action, result, insight = context(tmp_path)
    request = ExportRequest.from_state(
        session=session,
        profile=profile,
        state=json_state(session, action, insight, result),
    )

    payload = json.loads(export_verified_insights_json(request))
    exported_action = payload["tool_actions"][0]
    exported_result = payload["results"][0]

    assert payload["schema_version"] == "1"
    assert payload["export_type"] == "verified_insights"
    assert payload["session"] == {
        "session_id": str(session.session_id),
        "source_dataset_id": str(profile.dataset.dataset_id),
        "working_dataset_version": 1,
        "semantic_annotation_fingerprint": fingerprint_semantic_annotations(
            session.semantic_annotations
        ),
    }
    assert (
        exported_action["inputs"]
        == payload["verified_insights"][0]["evidence"]["tool_parameters"][0]
    )
    assert exported_result["result_ref"] == f"query-result:{result.query_id}"
    assert exported_result["result_type"] == "query"
    assert exported_result["sql"] == result.sql
    assert exported_result["row_count"] == result.row_count
    assert "rows" not in exported_result
    assert "original_filename" not in payload["dataset"]
    assert "must not be exported" not in json.dumps(payload)
    assert evidence_query_results(request) == (result,)


def test_export_selects_only_results_referenced_by_verified_insights(tmp_path: Path) -> None:
    session, profile, action, result, insight = context(tmp_path)
    unrelated = result.model_copy(update={"query_id": uuid4()})
    request = ExportRequest(
        session=session,
        profile=profile,
        verified_insights=(insight,),
        tool_actions=(action,),
        query_results=(unrelated, result),
    )
    assert evidence_query_results(request) == (result,)

    no_insights = request.model_copy(update={"verified_insights": ()})
    payload = json.loads(export_verified_insights_json(no_insights))
    assert payload["verified_insights"] == []
    assert payload["tool_actions"] == []
    assert payload["results"] == []


def test_from_state_rejects_missing_or_invalid_records(tmp_path: Path) -> None:
    session, profile, _, _, _ = context(tmp_path)
    base_state = {"session_id": str(session.session_id)}

    with pytest.raises(ExportValidationError, match="include session_id"):
        ExportRequest.from_state(session=session, profile=profile, state={})
    for state in (
        {"session_id": "not-a-uuid"},
        {**base_state, "verified_insights": "not-a-list"},
        {**base_state, "verified_insights": [{"claim": "incomplete"}]},
    ):
        with pytest.raises(ExportValidationError, match="not valid for export"):
            ExportRequest.from_state(session=session, profile=profile, state=state)

    empty = ExportRequest.from_state(
        session=session,
        profile=profile,
        state={**base_state, "tool_actions": None},
    )
    assert evidence_query_results(empty) == ()


def test_export_rejects_records_bound_to_another_session_or_dataset(tmp_path: Path) -> None:
    session, profile, action, result, insight = context(tmp_path)
    state = json_state(session, action, insight, result)

    with pytest.raises(ExportValidationError, match="does not match the workspace session"):
        ExportRequest.from_state(
            session=session,
            profile=profile,
            state={**state, "session_id": str(uuid4())},
        )
    with pytest.raises(ValueError, match="dataset identities do not match"):
        ExportRequest(
            session=session.model_copy(update={"source_dataset_id": uuid4()}),
            profile=profile,
        )


def test_export_rejects_stale_or_missing_insight_evidence(tmp_path: Path) -> None:
    session, profile, action, result, insight = context(tmp_path)
    current = ExportRequest(
        session=session,
        profile=profile,
        verified_insights=(insight,),
        tool_actions=(action,),
        query_results=(result,),
    )

    stale_cases = {
        "stale semantic annotations": current.model_copy(
            update={"session": session.model_copy(update={"semantic_annotations": ()})}
        ),
        "evidence is stale": current.model_copy(
            update={"session": session.model_copy(update={"working_dataset_version": 2})}
        ),
        "belongs to another dataset": current.model_copy(
            update={
                "verified_insights": (
                    insight.model_copy(
                        update={
                            "evidence": insight.evidence.model_copy(update={"dataset_id": uuid4()})
                        }
                    ),
                )
            }
        ),
        "unavailable evidence": current.model_copy(update={"tool_actions": ()}),
        f"result query-result:{result.query_id} is stale": current.model_copy(
            update={"query_results": (result.model_copy(update={"working_dataset_version": 2}),)}
        ),
    }
    for message, request in stale_cases.items():
        with pytest.raises(ExportValidationError, match=message):
            export_verified_insights_json(request)


def test_json_exports_statistical_parameters_and_estimates(tmp_path: Path) -> None:
    session, profile, action, result, insight = statistical_context(tmp_path)
    request = ExportRequest.from_state(
        session=session,
        profile=profile,
        state={
            "session_id": str(session.session_id),
            "verified_insights": [insight.model_dump(mode="json")],
            "tool_actions": [action.model_dump(mode="json")],
            "statistical_results": [result.model_dump(mode="json")],
        },
    )

    payload = json.loads(export_verified_insights_json(request))
    exported_action = payload["tool_actions"][0]
    exported_result = payload["results"][0]

    assert exported_result["result_ref"] == f"statistical-result:{result.result_id}"
    assert exported_result["result_type"] == "statistical"
    assert exported_action["inputs"] == action.inputs
    assert exported_result["parameters"] == exported_action["inputs"]["request"]
    assert exported_result["estimates"] == [
        estimate.model_dump(mode="json") for estimate in result.estimates
    ]
    assert evidence_query_results(request) == ()


def test_local_application_run_exports_its_verified_query_evidence(tmp_path: Path) -> None:
    gateway = FakeModelGateway(
        [
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
    )
    application = LocalAnalysisApplication(tmp_path / "app-data", gateway)
    workspace = application.ingest(
        application.stage_upload("sales.csv", b"region,revenue\nNorth,100\nSouth,200\n")
    )

    application.start(workspace, "What is total revenue?")
    state = application.resume(workspace, ApprovalDecision(approved=True))
    request = ExportRequest.from_state(
        session=workspace.session,
        profile=workspace.data_profile,
        state=state,
    )
    payload = json.loads(export_verified_insights_json(request))

    assert payload["tool_actions"][0]["inputs"]["sql"] == payload["results"][0]["sql"]
    [csv_result] = evidence_query_results(request)
    rows = list(csv.reader(io.StringIO(export_query_result_csv(csv_result).decode("utf-8-sig"))))
    assert rows == [["total"], ["300"]]
