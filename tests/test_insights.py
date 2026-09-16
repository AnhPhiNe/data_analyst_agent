"""Tests for deterministic Verified Insight publication and invalidation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from tabular_analytics_agent.data import (
    QueryColumn,
    QueryInspection,
    QueryResult,
    TabularDataCore,
    analyze_read_only_sql,
    generated_alias_function,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    DataProfile,
    DatasetIdentity,
    FieldKind,
    FieldProfile,
    InsightAssertion,
    InsightOperator,
    InsightStatus,
    SemanticAnnotation,
    ToolAction,
    UnsupportedClaim,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    VerifiedInsight,
    fingerprint_semantic_annotations,
)
from tabular_analytics_agent.statistics import (
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
    StatisticalTool,
    analyze,
)
from tabular_analytics_agent.verification import (
    available_evidence_values,
    publish_insight,
)
from tabular_analytics_agent.verification.insights import _METRIC_LABELS, _display_metric


def inspection_for(sql: str) -> QueryInspection:
    analysis = analyze_read_only_sql(sql, allowed_table="dataset")
    return QueryInspection(
        normalized_sql=analysis.normalized_sql,
        referenced_columns=analysis.referenced_columns,
        has_wildcard=analysis.has_wildcard,
        group_by_columns=analysis.group_by_columns,
        unaliased_outputs=analysis.unaliased_outputs,
        filters=analysis.filters,
        filter_scopes=analysis.filter_scopes,
        dataset_count_scope=analysis.dataset_count_scope,
        provenance_version=analysis.provenance_version,
        base_relations=analysis.base_relations,
        output_dependencies=analysis.output_dependencies,
    )


def statistical_context(
    tmp_path: Path,
    frame: pd.DataFrame,
    request: StatisticalRequest,
) -> tuple[DataProfile, ToolAction, StatisticalResult]:
    input_path = tmp_path / "statistical-input.csv"
    frame.to_csv(input_path, index=False)
    core = TabularDataCore(tmp_path / "statistical-session")
    handle = core.ingest(input_path)
    profile = core.profile(handle)
    output = StatisticalTool(core).execute(handle=handle, profile=profile, request=request)
    return profile, output.action, output.result


def context() -> tuple[DataProfile, ToolAction, QueryResult]:
    dataset = DatasetIdentity(
        dataset_id=uuid4(),
        original_filename="sales.csv",
        sha256="a" * 64,
        size_bytes=100,
    )
    profile = DataProfile(
        profile_id=uuid4(),
        dataset=dataset,
        row_count=3,
        fields=(
            FieldProfile(
                name="region",
                kind=FieldKind.CATEGORICAL,
                missing_count=0,
                missing_rate=0,
                unique_count=2,
            ),
            FieldProfile(
                name="revenue",
                kind=FieldKind.NUMERIC,
                missing_count=0,
                missing_rate=0,
                unique_count=3,
            ),
        ),
        profile_version="1",
        created_at=datetime.now(UTC),
    )
    passed = VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="query", passed=True, message="Query evidence passed."),),
    )
    sql = "SELECT region, SUM(revenue) AS revenue FROM dataset GROUP BY region"
    analysis = analyze_read_only_sql(sql, allowed_table="dataset")
    inspection = inspection_for(sql)
    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=1,
        inputs={
            "required_fields": ["region", "revenue"],
            "sql": analysis.normalized_sql,
            "filters": ["none"],
        },
        status=ActionStatus.SUCCEEDED,
        output_ref="query-result:one",
        verification_results=(passed,),
    )
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=dataset.dataset_id,
        working_dataset_version=1,
        sql=analysis.normalized_sql,
        columns=(
            QueryColumn(name="region", data_type="VARCHAR"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
        ),
        rows=(("North", 150.0), ("South", 150.0)),
        row_count=2,
        truncated=False,
        duration_ms=1,
        inspection=inspection,
    )
    return (
        profile,
        action.model_copy(update={"output_ref": f"query-result:{result.query_id}"}),
        result,
    )


def test_every_test_statistic_and_effect_size_has_a_claim_label() -> None:
    frame = pd.DataFrame(
        {
            "value": [3.1, 4.2, 2.8, 5.0, 4.4, 3.9, 6.1, 5.7, 6.4, 7.2, 6.8, 7.9],
            "two": ["A"] * 6 + ["B"] * 6,
            "three": ["A", "B", "C"] * 4,
            "x": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
            "y": [2.0, 1, 4, 3, 6, 5, 8, 9, 7, 11, 10, 12],
            "left": ["p", "q"] * 6,
            "right": ["m", "m", "n", "n"] * 3,
            "outcome": ["no", "yes", "no", "no", "yes", "no"]
            + ["yes", "yes", "no", "yes"] * 1
            + ["yes", "yes"],
        }
    )
    operation = StatisticalOperation
    requests = (
        StatisticalRequest(operation=operation.CORRELATION, x_field="x", y_field="y"),
        StatisticalRequest(
            operation=operation.T_TEST,
            value_field="value",
            group_field="two",
            group_order=("A", "B"),
        ),
        StatisticalRequest(
            operation=operation.MANN_WHITNEY,
            value_field="value",
            group_field="two",
            group_order=("A", "B"),
        ),
        StatisticalRequest(operation=operation.CHI_SQUARE, x_field="left", y_field="right"),
        StatisticalRequest(operation=operation.ANOVA, value_field="value", group_field="three"),
        StatisticalRequest(
            operation=operation.KRUSKAL_WALLIS, value_field="value", group_field="three"
        ),
        StatisticalRequest(operation=operation.LINEAR_REGRESSION, x_field="x", y_field="y"),
        StatisticalRequest(
            operation=operation.LOGISTIC_REGRESSION,
            x_field="x",
            y_field="outcome",
            positive_class="yes",
        ),
    )

    for request in requests:
        result = analyze(frame, request)
        effect = result.effect_size.metric if result.effect_size else None
        # A statistic without a label would read as its raw identifier, such as "kruskal h".
        assert result.statistic_name in _METRIC_LABELS, request.operation.value
        assert effect in _METRIC_LABELS, request.operation.value


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (1234567.8, "1234567.8"),
        (3.1000000000000005, "3.1"),
        (247.39000000000007, "247.39"),
        (0.056584184114898774, "0.0565841841148988"),
    ],
)
def test_claim_shows_a_float_with_at_most_15_significant_digits(value: float, shown: str) -> None:
    profile, action, result = context()
    precise = result.model_copy(update={"rows": (("North", value), ("South", 150.0))})

    publication = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].revenue"),
        evidence_metrics=("row[0].revenue",),
        caveats=(),
        profile=profile,
        action=action,
        result=precise,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    # A six-digit format rendered 1.23457e+06, and repr showed binary noise from the float.
    assert publication.claim.endswith(f" is {shown}.")


def test_generated_aggregate_alias_reads_as_its_function() -> None:
    profile, action, result = context()
    sql = "SELECT region, COUNT(*) AS count_1 FROM dataset GROUP BY region"
    counted = result.model_copy(
        update={
            "sql": inspection_for(sql).normalized_sql,
            "columns": (
                QueryColumn(name="region", data_type="VARCHAR"),
                QueryColumn(name="count_1", data_type="BIGINT"),
            ),
            "rows": (("North", 2), ("South", 1)),
            "group_by_columns": ("region",),
            "inspection": inspection_for(sql),
        }
    )

    publication = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].count_1"),
        evidence_metrics=("row[0].count_1",),
        caveats=(),
        profile=profile,
        action=action,
        result=counted,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert publication.claim == "Count for region = North is 2."
    # The SQL policy owns the generated alias form; other names are not aliases.
    assert generated_alias_function("count_1") == "count"
    assert generated_alias_function("total_revenue") is None


@pytest.mark.parametrize(
    ("metric", "inside_sentence"),
    [
        ("anova_f", "ANOVA F statistic"),
        ("welch_t", "Welch t statistic"),
        ("chi_square", "chi-square statistic"),
        ("p_value", "p-value"),
    ],
)
def test_statistic_labels_keep_their_case_inside_a_sentence(
    metric: str, inside_sentence: str
) -> None:
    _, _, result = context()
    assert _display_metric(metric, result) == inside_sentence


def test_query_claim_is_published_with_complete_reproducible_evidence() -> None:
    profile, action, result = context()
    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.EQUALS,
            left_metric="row[0].revenue",
            right_metric="row[1].revenue",
        ),
        evidence_metrics=("row[0].revenue", "row[1].revenue"),
        caveats=("Totals only cover supplied rows.",),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert publication.claim == (
        "Revenue for region = North equals revenue for region = South (150 versus 150)."
    )
    assert publication.status is InsightStatus.VERIFIED
    assert [value.value for value in publication.evidence.values] == [150.0, 150.0]
    assert publication.evidence.dataset_id == profile.dataset.dataset_id
    assert publication.evidence.working_dataset_version == 1
    assert publication.evidence.tool_parameters == (action.inputs,)
    assert publication.evidence.filters == ("none",)
    assert publication.verification.status is VerificationStatus.PASSED


def test_row_claim_uses_numeric_group_by_keys_as_context() -> None:
    profile, action, _ = context()
    year_field = profile.fields[0].model_copy(update={"name": "year", "kind": FieldKind.NUMERIC})
    year_profile = profile.model_copy(update={"fields": (year_field, profile.fields[1])})
    sql = "SELECT year, SUM(revenue) AS revenue FROM dataset GROUP BY year"
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        sql=inspection_for(sql).normalized_sql,
        columns=(
            QueryColumn(name="year", data_type="BIGINT"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
        ),
        rows=((2024, 100.0), (2025, 150.0)),
        row_count=2,
        truncated=False,
        duration_ms=1,
        group_by_columns=("year",),
        inspection=inspection_for(sql),
    )
    year_action = action.model_copy(
        update={
            "inputs": {**action.inputs, "required_fields": ["year", "revenue"]},
            "output_ref": f"query-result:{result.query_id}",
        }
    )

    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.GREATER_THAN,
            left_metric="row[1].revenue",
            right_metric="row[0].revenue",
        ),
        evidence_metrics=("row[0].revenue", "row[1].revenue"),
        caveats=(),
        profile=year_profile,
        action=year_action,
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert publication.claim == (
        "Revenue for year = 2025 is greater than revenue for year = 2024 (150 versus 100)."
    )


def test_single_row_claim_omits_row_position() -> None:
    profile, action, _ = context()
    sql = "SELECT AVG(revenue) AS avg_revenue FROM dataset"
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        sql=inspection_for(sql).normalized_sql,
        columns=(QueryColumn(name="avg_revenue", data_type="DOUBLE"),),
        rows=((110.0,),),
        row_count=1,
        truncated=False,
        duration_ms=1,
        inspection=inspection_for(sql),
    )
    single_row_action = action.model_copy(
        update={
            "inputs": {**action.inputs, "required_fields": ["revenue"]},
            "output_ref": f"query-result:{result.query_id}",
        }
    )

    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS, left_metric="row[0].avg_revenue"
        ),
        evidence_metrics=("row[0].avg_revenue",),
        caveats=(),
        profile=profile,
        action=single_row_action,
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert publication.claim == "Avg revenue is 110."


def test_reporting_a_group_by_label_is_unsupported() -> None:
    profile, action, result = context()
    grouped = result.model_copy(update={"group_by_columns": ("region",)})

    publication = publish_insight(
        assertion=InsightAssertion(operator=InsightOperator.REPORTS, left_metric="row[0].region"),
        evidence_metrics=("row[0].region",),
        caveats=(),
        profile=profile,
        action=action,
        result=grouped,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, UnsupportedClaim)
    assert "GROUP BY label" in publication.reason


def test_sql_filters_stay_in_evidence_instead_of_claim_text() -> None:
    profile, action, _ = context()
    sql = "SELECT COUNT(*) AS row_count FROM dataset WHERE revenue >= 100"
    inspection = inspection_for(sql)
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        sql=inspection.normalized_sql,
        columns=(QueryColumn(name="row_count", data_type="BIGINT"),),
        rows=((2,),),
        row_count=1,
        truncated=False,
        duration_ms=1,
        filters=("revenue >= 100",),
        inspection=inspection,
    )
    filtered_action = action.model_copy(
        update={
            "inputs": {
                **action.inputs,
                "required_fields": ["revenue"],
                "sql": inspection.normalized_sql,
            },
            "output_ref": f"query-result:{result.query_id}",
        }
    )

    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS, left_metric="row[0].row_count"
        ),
        evidence_metrics=("row[0].row_count",),
        caveats=(),
        profile=profile,
        action=filtered_action,
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert publication.claim == "Row count is 2."
    assert publication.evidence.filters == ("revenue >= 100",)


def test_row_count_evidence_exists_only_for_complete_query_results() -> None:
    profile, action, result = context()

    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS, left_metric="result.row_count"
        ),
        evidence_metrics=("result.row_count",),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
    )
    truncated = result.model_copy(update={"truncated": True})

    assert isinstance(publication, VerifiedInsight)
    assert publication.claim == "Result row count is 2."
    assert "result.row_count" not in {
        value.metric for value in available_evidence_values(truncated)
    }


def test_missing_metric_and_stale_dataset_become_unsupported_claims() -> None:
    profile, action, result = context()
    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="row[9].revenue",
        ),
        evidence_metrics=("row[9].revenue",),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=2,
    )

    assert isinstance(publication, UnsupportedClaim)
    assert publication.status is InsightStatus.UNSUPPORTED
    assert publication.evidence is None
    assert "Missing deterministic evidence metrics" in publication.reason
    assert "stale" in publication.reason
    assert publication.verification.status is VerificationStatus.FAILED


def test_false_qualitative_comparison_becomes_unsupported_with_valid_metrics() -> None:
    profile, action, result = context()
    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.GREATER_THAN,
            left_metric="row[1].revenue",
            right_metric="row[0].revenue",
        ),
        evidence_metrics=("row[0].revenue", "row[1].revenue"),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, UnsupportedClaim)
    assert "comparison contradicts" in publication.reason


def test_result_must_match_the_tool_action_output_reference() -> None:
    profile, action, result = context()
    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="row[0].revenue",
        ),
        evidence_metrics=("row[0].revenue",),
        caveats=(),
        profile=profile,
        action=action.model_copy(update={"output_ref": "query-result:other"}),
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, UnsupportedClaim)
    assert "does not identify this deterministic result" in publication.reason


def test_association_assertion_publishes_only_canonical_noncausal_language(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame({"revenue": [1, 2, 3, 4, 5], "score": [2, 4, 6, 8, 10]})
    statistical_profile, action, statistical_result = statistical_context(
        tmp_path,
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION,
            x_field="revenue",
            y_field="score",
        ),
    )
    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.POSITIVE,
            left_metric="pearson_r",
        ),
        evidence_metrics=("pearson_r", "p_value"),
        caveats=(),
        profile=statistical_profile,
        action=action,
        result=statistical_result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert publication.evidence is not None
    assert "positive" in publication.claim
    assert "cause" not in publication.claim
    assert {value.metric for value in available_evidence_values(statistical_result)} >= {
        "pearson_r",
        "p_value",
        "adjusted_alpha",
    }


@pytest.mark.parametrize(
    "operator",
    [InsightOperator.REPORTS, InsightOperator.STATISTICALLY_SIGNIFICANT],
)
def test_correlation_p_value_names_its_test_and_preserves_assertion(
    operator: InsightOperator,
    tmp_path: Path,
) -> None:
    # A monotonic nonlinear relationship: Spearman and Pearson differ.
    frame = pd.DataFrame({"revenue": [1, 2, 3, 4, 5, 6], "score": [1, 4, 9, 16, 25, 36]})
    statistical_profile, action, result = statistical_context(
        tmp_path,
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION, x_field="revenue", y_field="score"
        ),
    )
    assertion = InsightAssertion(operator=operator, left_metric="p_value")
    publication = publish_insight(
        assertion=assertion,
        evidence_metrics=("p_value", "adjusted_alpha", "spearman_rho"),
        caveats=(),
        profile=statistical_profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert "pearson correlation between revenue and score" in publication.claim.lower()
    assert "spearman" not in publication.claim.lower()
    assert "for Pearson correlation between revenue and score" in publication.claim
    assert publication.assertion == assertion
    restored = VerifiedInsight.model_validate_json(publication.model_dump_json())
    assert restored.assertion == assertion
    legacy = publication.model_dump(exclude={"assertion"})
    assert VerifiedInsight.model_validate(legacy).assertion is None
    assert any("separate descriptive estimate" in note for note in publication.evidence.caveats)

    # Legacy/malformed state must not publish an unidentified test as a verified claim.
    unidentified = result.model_copy(update={"statistic_name": None})
    rejected = publish_insight(
        assertion=assertion,
        evidence_metrics=("p_value", "adjusted_alpha"),
        caveats=(),
        profile=statistical_profile,
        action=action,
        result=unidentified,
        current_working_dataset_version=1,
    )
    assert isinstance(rejected, UnsupportedClaim)
    assert "identified test statistic" in rejected.reason


def test_failed_statistical_assumptions_are_preserved_as_evidence_caveats(
    tmp_path: Path,
) -> None:
    frame = pd.DataFrame(
        {
            "category": ["low"] * 4 + ["mid"] * 4 + ["high"] * 4,
            "segment": ["one", "one", "two", "two"] * 3,
        }
    )
    statistical_profile, action, statistical_result = statistical_context(
        tmp_path,
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CHI_SQUARE,
            x_field="category",
            y_field="segment",
        ),
    )

    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="cramers_v",
        ),
        evidence_metrics=("cramers_v",),
        caveats=(),
        profile=statistical_profile,
        action=action,
        result=statistical_result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, VerifiedInsight)
    assert any(
        caveat.startswith("expected_cell_counts:") for caveat in publication.evidence.caveats
    )


def test_significance_assertion_requires_adjusted_alpha_in_selected_evidence() -> None:
    profile, query_action, _ = context()
    frame = pd.DataFrame({"revenue": [1, 2, 3, 4, 5], "score": [2, 4, 6, 8, 10]})
    statistical_result = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CORRELATION,
            x_field="revenue",
            y_field="score",
        ),
    )
    statistical_profile = profile.model_copy(
        update={
            "fields": (
                profile.fields[1],
                profile.fields[1].model_copy(update={"name": "score"}),
            ),
            "row_count": len(frame),
        }
    )
    action = query_action.model_copy(
        update={
            "tool_name": "statistical_analysis",
            "inputs": statistical_result.parameters,
            "output_ref": f"statistical-result:{statistical_result.result_id}",
        }
    )

    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.STATISTICALLY_SIGNIFICANT,
            left_metric="p_value",
        ),
        evidence_metrics=("p_value",),
        caveats=(),
        profile=statistical_profile,
        action=action,
        result=statistical_result,
        current_working_dataset_version=1,
    )

    assert isinstance(publication, UnsupportedClaim)
    assert "adjusted-alpha evidence" in publication.reason


def test_insight_becomes_stale_after_dataset_or_semantic_revision() -> None:
    profile, action, result = context()
    annotations = (
        SemanticAnnotation(
            field_name="revenue",
            meaning="Gross revenue",
            unit="USD",
            confirmed_by_user=True,
        ),
    )
    publication = publish_insight(
        assertion=InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="row[0].revenue",
        ),
        evidence_metrics=("row[0].revenue",),
        caveats=(),
        profile=profile,
        action=action,
        result=result,
        current_working_dataset_version=1,
        semantic_annotations=annotations,
    )
    assert isinstance(publication, VerifiedInsight)
    assert publication.evidence.semantic_annotation_fingerprint == fingerprint_semantic_annotations(
        annotations
    )
