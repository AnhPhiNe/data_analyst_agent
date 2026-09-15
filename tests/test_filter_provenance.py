"""Regression tests for SQL filter scopes across results, claims, and exports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tabular_analytics_agent.application.exports import (
    ExportRequest,
    export_verified_insights_json,
)
from tabular_analytics_agent.data import (
    DatasetHandle,
    QueryInspection,
    QueryResult,
    TabularDataCore,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisSession,
    DataProfile,
    EvidenceTrail,
    FilterScope,
    InsightAssertion,
    InsightOperator,
    SessionStatus,
    ToolAction,
    VerifiedInsight,
)
from tabular_analytics_agent.verification import publish_insight, verify_query_evidence

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_sales.csv"
FILTERED_CTE_SQL = (
    "WITH filtered AS ("
    "SELECT region, SUM(revenue) AS total FROM dataset "
    "WHERE region = 'North' GROUP BY region HAVING SUM(revenue) > 0"
    ") SELECT region, total FROM filtered "
    "WHERE total > 0 AND EXISTS (SELECT 1 FROM dataset WHERE revenue >= 100)"
)


def build_query_context(
    tmp_path: Path,
) -> tuple[
    DatasetHandle,
    DataProfile,
    QueryInspection,
    QueryResult,
    ToolAction,
    VerifiedInsight,
]:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(FIXTURE)
    profile = core.profile(handle)
    inspection = core.inspect_query(handle, FILTERED_CTE_SQL)
    result = core.query(handle, FILTERED_CTE_SQL)
    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=handle.working_dataset_version,
        inputs={"sql": result.sql, "required_fields": ["region", "revenue"]},
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{result.query_id}",
        verification_results=(
            verify_query_evidence(
                profile=profile,
                result=result,
                source_fields=("region", "revenue"),
                current_working_dataset_version=handle.working_dataset_version,
            ),
        ),
    )
    insight = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="row[0].total",
        ),
        evidence_metrics=("row[0].total",),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=handle.working_dataset_version,
    )
    assert isinstance(insight, VerifiedInsight)
    return handle, profile, inspection, result, action, insight


def test_filter_scopes_keep_outer_filters_and_nested_predicates_separate(
    tmp_path: Path,
) -> None:
    _, _, inspection, result, _, _ = build_query_context(tmp_path)

    assert inspection.filters == (
        "total > 0 AND EXISTS(SELECT 1 FROM dataset WHERE revenue >= 100)",
    )
    assert result.filters == inspection.filters
    assert result.filter_scopes == (
        FilterScope(
            scope="outer",
            clause="where",
            expression="total > 0 AND EXISTS(SELECT 1 FROM dataset WHERE revenue >= 100)",
        ),
        FilterScope(scope="cte:filtered", clause="where", expression="region = 'North'"),
        FilterScope(scope="cte:filtered", clause="having", expression="SUM(revenue) > 0"),
        FilterScope(scope="subquery:1", clause="where", expression="revenue >= 100"),
    )


def test_direct_where_and_having_filters_keep_legacy_outer_behavior(tmp_path: Path) -> None:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(FIXTURE)
    inspection = core.inspect_query(
        handle,
        "SELECT region, SUM(revenue) AS total FROM dataset "
        "WHERE region = 'North' GROUP BY region HAVING SUM(revenue) > 0",
    )

    assert inspection.filters == ("region = 'North'", "SUM(revenue) > 0")
    assert inspection.filter_scopes == (
        FilterScope(scope="outer", clause="where", expression="region = 'North'"),
        FilterScope(scope="outer", clause="having", expression="SUM(revenue) > 0"),
    )


def test_duplicate_nested_cte_aliases_receive_unique_scope_names(tmp_path: Path) -> None:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(FIXTURE)
    inspection = core.inspect_query(
        handle,
        "WITH filtered AS (SELECT region FROM dataset WHERE region = 'North'), "
        "outer_cte AS (WITH filtered AS ("
        "SELECT region FROM dataset WHERE region = 'South') "
        "SELECT region FROM filtered WHERE region = 'South') "
        "SELECT region FROM outer_cte",
    )

    assert inspection.filters == ()
    assert {
        (scope.scope, scope.clause, scope.expression) for scope in inspection.filter_scopes
    } == {
        ("cte:filtered", "where", "region = 'North'"),
        ("cte:filtered:2", "where", "region = 'South'"),
        ("cte:outer_cte", "where", "region = 'South'"),
    }


def test_legacy_result_inspection_and_evidence_payloads_default_to_no_scoped_filters(
    tmp_path: Path,
) -> None:
    _, _, inspection, result, _, insight = build_query_context(tmp_path)

    legacy_inspection = inspection.model_dump(mode="python")
    legacy_inspection.pop("filter_scopes")
    legacy_inspection.pop("dataset_count_scope")
    assert QueryInspection.model_validate(legacy_inspection).filter_scopes == ()

    legacy_result = result.model_dump(mode="python")
    legacy_result.pop("filter_scopes")
    legacy_result.pop("dataset_count_scope")
    assert QueryResult.model_validate(legacy_result).filter_scopes == ()

    legacy_evidence = insight.evidence.model_dump(mode="python")
    legacy_evidence.pop("filter_scopes")
    legacy_evidence.pop("dataset_count_scope")
    assert EvidenceTrail.model_validate(legacy_evidence).filter_scopes == ()


def test_filter_scopes_reach_verified_claim_evidence_and_json_export(tmp_path: Path) -> None:
    handle, profile, _, result, action, insight = build_query_context(tmp_path)
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
        tool_actions=(action,),
        query_results=(result,),
    )

    assert result.filter_scopes == insight.evidence.filter_scopes
    assert "query context: cte:filtered where region = 'North'" in insight.claim
    assert "cte:filtered having SUM(revenue) > 0" in insight.claim
    assert "subquery:1 where revenue >= 100" in insight.claim
    # The nested predicates are query context; the legacy outer condition remains distinct.
    assert insight.evidence.filters == result.filters

    payload = json.loads(export_verified_insights_json(request))
    exported_result = payload["results"][0]
    exported_evidence = payload["verified_insights"][0]["evidence"]
    assert exported_result["filter_scopes"] == [
        scope.model_dump(mode="json") for scope in result.filter_scopes
    ]
    assert exported_evidence["filter_scopes"] == exported_result["filter_scopes"]
