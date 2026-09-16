"""Focused regressions for SQL provenance and publication lineage guards."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tabular_analytics_agent.application.exports import (
    ExportRequest,
    ExportValidationError,
    evidence_results,
)
from tabular_analytics_agent.data import (
    QueryColumn,
    QueryInspection,
    QueryResult,
    UnsafeQueryError,
    analyze_read_only_sql,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisSession,
    DataProfile,
    DatasetIdentity,
    FieldKind,
    FieldProfile,
    InsightAssertion,
    InsightOperator,
    SessionStatus,
    ToolAction,
    VerifiedInsight,
)
from tabular_analytics_agent.statistics import (
    StatisticalEstimate,
    StatisticalOperation,
    StatisticalResult,
)
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


def _profile() -> DataProfile:
    dataset = DatasetIdentity(
        dataset_id=uuid4(),
        original_filename="sales.csv",
        sha256="a" * 64,
        size_bytes=100,
    )
    return DataProfile(
        profile_id=uuid4(),
        dataset=dataset,
        row_count=2,
        fields=(
            FieldProfile(
                name="region",
                kind=FieldKind.CATEGORICAL,
                missing_count=0,
                missing_rate=0.0,
                unique_count=2,
            ),
            FieldProfile(
                name="revenue",
                kind=FieldKind.NUMERIC,
                missing_count=0,
                missing_rate=0.0,
                unique_count=2,
            ),
        ),
        profile_version="1",
        created_at=datetime.now(UTC),
    )


def _query_context(
    profile: DataProfile,
    *,
    sql: str,
    rows: tuple[tuple[str | int | float | bool | None, ...], ...],
    columns: tuple[QueryColumn, ...],
    source_fields: tuple[str, ...] = ("revenue",),
) -> tuple[QueryResult, ToolAction]:
    analysis = analyze_read_only_sql(sql, allowed_table="dataset")
    inspection_data = {
        "normalized_sql": analysis.normalized_sql,
        "referenced_columns": analysis.referenced_columns,
        "has_wildcard": analysis.has_wildcard,
        "group_by_columns": analysis.group_by_columns,
        "unaliased_outputs": analysis.unaliased_outputs,
        "filters": analysis.filters,
        "filter_scopes": analysis.filter_scopes,
        "dataset_count_scope": analysis.dataset_count_scope,
        "provenance_version": analysis.provenance_version,
        "base_relations": analysis.base_relations,
        "output_dependencies": analysis.output_dependencies,
    }
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        sql=analysis.normalized_sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=False,
        duration_ms=1,
        inspection=QueryInspection.model_validate(inspection_data),
    )
    verification = verify_query_evidence(
        profile=profile,
        result=result,
        source_fields=source_fields,
        current_working_dataset_version=1,
    )
    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=1,
        inputs={"sql": result.sql, "required_fields": list(source_fields)},
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{result.query_id}",
        verification_results=(verification,),
    )
    return result, action


def test_policy_resolves_cte_dependencies_and_distinguishes_count_and_literal() -> None:
    analysis = analyze_read_only_sql(
        "WITH totals AS (SELECT SUM(revenue) AS total FROM dataset) "
        "SELECT 1688 AS label, total FROM totals",
        allowed_table="dataset",
    )

    assert analysis.provenance_version == "v1"
    assert analysis.base_relations == ("dataset",)
    assert analysis.output_dependencies == (("label", ()), ("total", ("revenue",)))

    casefolded_cte = analyze_read_only_sql(
        "WITH Totals AS (SELECT SUM(revenue) AS Total FROM dataset) SELECT TOTAL FROM TOTALS",
        allowed_table="dataset",
    )
    assert casefolded_cte.output_dependencies[0][1] == ("revenue",)

    count = analyze_read_only_sql(
        "SELECT COUNT(*) AS record_count FROM dataset",
        allowed_table="dataset",
    )
    assert count.output_dependencies == (("record_count", ("__row_count__",)),)

    computed = analyze_read_only_sql(
        "SELECT COUNT(*) + SUM(revenue) AS combined FROM dataset",
        allowed_table="dataset",
    )
    assert computed.output_dependencies == (("combined", ("__row_count__", "revenue")),)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH dataset AS (SELECT 1688 AS value) SELECT value FROM dataset",
        "SELECT 1 AS value",
        "SELECT random() AS value FROM dataset",
        "SELECT CURRENT_TIMESTAMP AS value FROM dataset",
        "SELECT UUID() AS value FROM dataset",
    ],
)
def test_policy_rejects_shadowed_missing_or_volatile_sources(sql: str) -> None:
    with pytest.raises(UnsafeQueryError):
        analyze_read_only_sql(sql, allowed_table="dataset")


def test_query_verification_rejects_legacy_or_mismatched_inspection() -> None:
    profile = _profile()
    result, _ = _query_context(
        profile,
        sql="SELECT SUM(revenue) AS total FROM dataset",
        rows=((250.0,),),
        columns=(QueryColumn(name="total", data_type="DOUBLE"),),
    )

    legacy = result.model_copy(update={"inspection": None})
    assert query_provenance_status(legacy)[0] is False
    verification = verify_query_evidence(
        profile=profile,
        result=legacy,
        source_fields=("revenue",),
        current_working_dataset_version=1,
    )
    assert verification.status is not None
    assert not next(check for check in verification.checks if check.name == "sql_provenance").passed

    mismatch = result.model_copy(update={"sql": "SELECT 1 AS total FROM dataset"})
    assert query_provenance_status(mismatch)[0] is False


def test_publication_rejects_literal_metric_but_allows_literal_label() -> None:
    profile = _profile()
    literal_result, literal_action = _query_context(
        profile,
        sql="SELECT 1688 AS value, SUM(revenue) AS total FROM dataset",
        rows=((1688, 250.0),),
        columns=(
            QueryColumn(name="value", data_type="INTEGER"),
            QueryColumn(name="total", data_type="DOUBLE"),
        ),
    )
    rejected = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].value"),
        evidence_metrics=("row[0].value",),
        caveats=(),
        profile=profile,
        action=literal_action,
        result=literal_result,
        current_working_dataset_version=1,
    )
    assert not isinstance(rejected, VerifiedInsight)
    assert "literal" in rejected.reason.lower()

    label_result, label_action = _query_context(
        profile,
        sql="SELECT 'North' AS label, SUM(revenue) AS total FROM dataset",
        rows=(("North", 250.0),),
        columns=(
            QueryColumn(name="label", data_type="VARCHAR"),
            QueryColumn(name="total", data_type="DOUBLE"),
        ),
    )
    published = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].total"),
        evidence_metrics=("row[0].total",),
        caveats=(),
        profile=profile,
        action=label_action,
        result=label_result,
        current_working_dataset_version=1,
    )
    assert isinstance(published, VerifiedInsight)


def test_export_rejects_verified_insight_backed_by_legacy_query_result() -> None:
    profile = _profile()
    result, action = _query_context(
        profile,
        sql="SELECT SUM(revenue) AS total FROM dataset",
        rows=((250.0,),),
        columns=(QueryColumn(name="total", data_type="DOUBLE"),),
    )
    published = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].total"),
        evidence_metrics=("row[0].total",),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
    )
    assert isinstance(published, VerifiedInsight)
    legacy_result = result.model_copy(update={"inspection": None})
    now = datetime.now(UTC)
    session = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.COMPLETED,
        source_dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        created_at=now,
        updated_at=now,
    )
    request = ExportRequest(
        session=session,
        profile=profile,
        verified_insights=(published,),
        tool_actions=(action,),
        query_results=(legacy_result,),
    )
    with pytest.raises(ExportValidationError, match=r"legacy|provenance"):
        evidence_results(request)


def _statistical_result(**updates: object) -> StatisticalResult:
    result = StatisticalResult(
        result_id=uuid4(),
        operation=StatisticalOperation.DESCRIPTIVE,
        source_fields=("revenue",),
        sample_size=2,
        missing_row_count=0,
        missing_data_handling="complete cases",
        dataset_row_count=2,
        population_row_count=2,
        rows_loaded=2,
        sampled=False,
        sampling_method=None,
        sampling_seed=None,
        partial=False,
        truncated=False,
        estimates=(StatisticalEstimate(metric="revenue.mean", value=125.0),),
        parameters={"value_fields": ["revenue"]},
    )
    return result.model_copy(update=updates)


def _statistical_action(result: StatisticalResult, **inputs: object) -> ToolAction:
    return ToolAction(
        action_id=uuid4(),
        tool_name="statistical_analysis",
        schema_version="1",
        working_dataset_version=1,
        inputs={
            "dataset_row_count": result.dataset_row_count,
            "population_row_count": result.population_row_count,
            "rows_loaded": result.rows_loaded,
            "sampled": result.sampled,
            "sampling_method": result.sampling_method,
            "sampling_seed": result.sampling_seed,
            "partial": result.partial,
            "truncated": result.truncated,
            **inputs,
        },
    )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"sampled": None}, "legacy"),
        ({"sampled": True}, "sampled"),
        ({"sampling_method": "reservoir"}, "sampling"),
        ({"partial": True}, "partial or truncated"),
        ({"rows_loaded": 1}, "every row"),
        ({"population_row_count": 3}, "every row"),
        ({"sample_size": 3}, "sample_size"),
        ({"missing_row_count": 3}, "missing_row_count"),
    ],
)
def test_statistical_full_data_guard_rejects_incomplete_or_inconsistent_scope(
    updates: dict[str, object], message: str
) -> None:
    passed, detail = statistical_full_data_status(_statistical_result(**updates))
    assert not passed
    assert message.casefold() in detail.casefold()


def test_statistical_action_scope_must_be_present_and_match_execution() -> None:
    result = _statistical_result()
    valid = _statistical_action(result)
    assert statistical_action_status(valid, result)[0]

    missing = valid.model_copy(update={"inputs": {}})
    assert not statistical_action_status(missing, result)[0]

    mismatch = valid.model_copy(update={"inputs": {**valid.inputs, "rows_loaded": 1}})
    assert not statistical_action_status(mismatch, result)[0]

    legacy_sampling = valid.model_copy(
        update={
            "inputs": {
                **valid.inputs,
                "sampling": {"method": "reservoir", "maximum_rows": 10_000},
            }
        }
    )
    assert not statistical_action_status(legacy_sampling, result)[0]


def test_query_claim_and_catalog_metadata_cover_casefolded_and_unavailable_outputs() -> None:
    profile = _profile()
    result, _ = _query_context(
        profile,
        sql="SELECT SUM(revenue) AS total FROM dataset",
        rows=((250.0,),),
        columns=(QueryColumn(name="total", data_type="DOUBLE"),),
    )
    assert query_output_dependencies(result, "row[0].TOTAL") == ("revenue",)
    assert query_output_dependencies(result, "result.row_count") is None
    assert query_output_dependencies(result, "row[1].missing") is None
    assert query_claim_status(result, ("result.row_count",), profile_fields={"revenue"})[0]

    truncated = result.model_copy(update={"truncated": True})
    assert not query_claim_status(truncated, ("result.row_count",), profile_fields={"revenue"})[0]

    assert result.inspection is not None
    unknown_inspection = result.inspection.model_copy(
        update={"output_dependencies": (("total", ("unknown_field",)),)}
    )
    unknown = result.model_copy(update={"inspection": unknown_inspection})
    assert not query_claim_status(unknown, ("row[0].total",), profile_fields={"revenue"})[0]

    no_base = result.inspection.model_copy(update={"base_relations": ()})
    assert not query_provenance_status(result.model_copy(update={"inspection": no_base}))[0]

    query_metadata = result_provenance_metadata(result)
    assert query_metadata["provenance_version"] == "v1"
    assert query_metadata["base_relations"] == ["dataset"]
    stat_metadata = result_provenance_metadata(_statistical_result())
    assert stat_metadata["sampled"] is False
    assert stat_metadata["sampling_method"] is None
