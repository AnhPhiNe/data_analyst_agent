"""Safe whole-dataset COUNT(*) binding and evidence regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.application.exports import ExportRequest, export_verified_insights_json
from tabular_analytics_agent.data import (
    DatasetHandle,
    QueryColumn,
    TabularDataCore,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisPlan,
    AnalysisSession,
    AnalyticalGoal,
    DataProfile,
    GoalFamily,
    InsightAssertion,
    InsightOperator,
    PlanStatus,
    PlanStep,
    SessionStatus,
    ToolAction,
    VerifiedInsight,
)
from tabular_analytics_agent.model_gateway import SQLToolRequestDraft
from tabular_analytics_agent.orchestration.binding import bind_tool_request_to_step
from tabular_analytics_agent.orchestration.field_ids import PlanRepairError
from tabular_analytics_agent.verification import publish_insight, verify_query_evidence


def make_context(
    tmp_path: Path,
    *,
    csv_content: str = "region,revenue\nNorth,100\nSouth,150\n",
) -> tuple[TabularDataCore, DatasetHandle, DataProfile, PlanStep, AnalysisPlan]:
    upload = tmp_path / "sales.csv"
    upload.write_text(csv_content, encoding="utf-8", newline="")
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(upload)
    profile = core.profile(handle)
    step = PlanStep(
        step_id="whole-dataset-count",
        description="Count all dataset rows",
        expected_tool="read_only_sql",
        required_fields=(),
        intended_output="One whole-dataset row count",
    )
    plan = AnalysisPlan(
        plan_id=uuid4(),
        goal=AnalyticalGoal(text="Count rows", family=GoalFamily.SUMMARY),
        steps=(step,),
        status=PlanStatus.APPROVED,
    )
    return core, handle, profile, step, plan


def test_empty_field_plan_binds_count_and_exports_verified_row_count(
    tmp_path: Path,
) -> None:
    core, handle, profile, step, plan = make_context(tmp_path)
    bound = bind_tool_request_to_step(
        SQLToolRequestDraft(sql="SELECT COUNT(*) AS record_count FROM dataset AS rows_table"),
        step=step,
        plan=plan,
        profile=profile,
        handle=handle,
        data_core=core,
    )

    assert bound.required_fields == ()
    result = core.query(handle, bound.sql or "")
    verification = verify_query_evidence(
        profile=profile,
        result=result,
        source_fields=bound.required_fields,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert result.dataset_count_scope == "whole_dataset"
    assert result.rows == ((profile.row_count,),)
    assert result.row_count == 1
    assert verification.status.value == "passed"

    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=handle.working_dataset_version,
        inputs={
            "plan_step_id": step.step_id,
            "purpose": bound.purpose,
            "sql": result.sql,
            "required_fields": [],
        },
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{result.query_id}",
        verification_results=(verification,),
    )
    insight = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="row[0].record_count",
        ),
        evidence_metrics=("row[0].record_count",),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(insight, VerifiedInsight)
    assert insight.evidence.source_fields == ()
    assert insight.evidence.dataset_count_scope == "whole_dataset"
    assert "is 2." in insight.claim

    now = datetime.now(UTC)
    session = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.COMPLETED,
        source_dataset_id=handle.dataset.dataset_id,
        working_dataset_version=handle.working_dataset_version,
        created_at=now,
        updated_at=now,
    )
    export = ExportRequest(
        session=session,
        profile=profile,
        verified_insights=(insight,),
        tool_actions=(action,),
        query_results=(result,),
    )
    payload = json.loads(export_verified_insights_json(export))
    assert payload["results"][0]["dataset_count_scope"] == "whole_dataset"
    assert payload["verified_insights"][0]["evidence"]["dataset_count_scope"] == "whole_dataset"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM dataset",
        "SELECT COUNT(1) AS record_count FROM dataset",
        "SELECT COUNT(revenue) AS record_count FROM dataset",
        "SELECT COUNT(*) AS record_count FROM dataset WHERE region = 'North'",
        "SELECT region, COUNT(*) AS record_count FROM dataset GROUP BY region",
        "WITH counted AS (SELECT COUNT(*) AS record_count FROM dataset) "
        "SELECT COUNT(*) AS total_count FROM counted",
        "SELECT COUNT(*) AS record_count, MAX(revenue) AS max_revenue FROM dataset",
        "SELECT COUNT(*) AS record_count FROM dataset AS a "
        "JOIN dataset AS b ON a.region = b.region",
    ],
)
def test_empty_field_plan_rejects_sql_without_exact_whole_dataset_count(
    tmp_path: Path,
    sql: str,
) -> None:
    core, handle, profile, step, plan = make_context(tmp_path)

    # A step that approves no fields is a plan defect, so binding asks for a replan.
    with pytest.raises(PlanRepairError, match="lists no required_fields"):
        bind_tool_request_to_step(
            SQLToolRequestDraft(sql=sql),
            step=step,
            plan=plan,
            profile=profile,
            handle=handle,
            data_core=core,
        )


def test_count_verification_rechecks_marker_sql_shape_and_profile_count(tmp_path: Path) -> None:
    core, handle, profile, _, _ = make_context(tmp_path)
    result = core.query(handle, "SELECT COUNT(*) AS record_count FROM dataset")

    invalid_results = (
        (result.model_copy(update={"dataset_count_scope": None}), "schema_grounding"),
        (
            result.model_copy(
                update={"sql": "SELECT COUNT(*) AS record_count FROM dataset WHERE 1 = 1"}
            ),
            "schema_grounding",
        ),
        (result.model_copy(update={"rows": ((profile.row_count + 1,),)}), "schema_grounding"),
        (result.model_copy(update={"rows": ((True,),)}), "schema_grounding"),
        (result.model_copy(update={"truncated": True}), "schema_grounding"),
        (
            result.model_copy(
                update={
                    "columns": (
                        QueryColumn(name="record_count", data_type="BIGINT"),
                        QueryColumn(name="extra", data_type="BIGINT"),
                    ),
                    "rows": ((profile.row_count, 0),),
                }
            ),
            "schema_grounding",
        ),
        (result.model_copy(update={"dataset_id": uuid4()}), "dataset_identity"),
        (
            result.model_copy(
                update={"working_dataset_version": handle.working_dataset_version + 1}
            ),
            "working_dataset_version",
        ),
    )
    for invalid_result, failed_gate in invalid_results:
        verification = verify_query_evidence(
            profile=profile,
            result=invalid_result,
            source_fields=(),
            current_working_dataset_version=handle.working_dataset_version,
        )
        assert verification.status.value == "failed"
        assert not next(check for check in verification.checks if check.name == failed_gate).passed


def test_count_of_empty_dataset_is_verified_as_zero(tmp_path: Path) -> None:
    core, handle, profile, _, _ = make_context(tmp_path, csv_content="region,revenue\n")
    result = core.query(handle, "SELECT COUNT(*) AS record_count FROM dataset")

    verification = verify_query_evidence(
        profile=profile,
        result=result,
        source_fields=(),
        current_working_dataset_version=handle.working_dataset_version,
    )

    assert profile.row_count == 0
    assert result.rows == ((0,),)
    assert verification.status.value == "passed"
