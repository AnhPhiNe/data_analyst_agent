"""Tests for deterministic Verified Insight publication and invalidation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd

from tabular_analytics_agent.data import QueryColumn, QueryResult
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
    StaleInsight,
    ToolAction,
    UnsupportedClaim,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    VerifiedInsight,
)
from tabular_analytics_agent.statistics import (
    StatisticalOperation,
    StatisticalRequest,
    analyze,
)
from tabular_analytics_agent.verification import (
    available_evidence_values,
    invalidate_stale_insight,
    publish_insight,
    semantic_annotation_fingerprint,
)


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
    action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=1,
        inputs={
            "required_fields": ["region", "revenue"],
            "sql": "SELECT region, SUM(revenue) AS revenue FROM dataset GROUP BY region",
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
        sql=str(action.inputs["sql"]),
        columns=(
            QueryColumn(name="region", data_type="VARCHAR"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
        ),
        rows=(("North", 150.0), ("South", 150.0)),
        row_count=2,
        truncated=False,
        duration_ms=1,
    )
    return (
        profile,
        action.model_copy(update={"output_ref": f"query-result:{result.query_id}"}),
        result,
    )


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
    result = QueryResult(
        query_id=uuid4(),
        dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        sql="SELECT year, SUM(revenue) AS revenue FROM dataset GROUP BY year",
        columns=(
            QueryColumn(name="year", data_type="BIGINT"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
        ),
        rows=((2024, 100.0), (2025, 150.0)),
        row_count=2,
        truncated=False,
        duration_ms=1,
        group_by_columns=("year",),
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


def test_association_assertion_publishes_only_canonical_noncausal_language() -> None:
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
            "row_count": 5,
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


def test_failed_statistical_assumptions_are_preserved_as_evidence_caveats() -> None:
    profile, query_action, _ = context()
    frame = pd.DataFrame(
        {
            "category": ["low"] * 4 + ["mid"] * 4 + ["high"] * 4,
            "segment": ["one", "one", "two", "two"] * 3,
        }
    )
    statistical_result = analyze(
        frame,
        StatisticalRequest(
            operation=StatisticalOperation.CHI_SQUARE,
            x_field="category",
            y_field="segment",
        ),
    )
    statistical_profile = profile.model_copy(
        update={
            "fields": (
                profile.fields[0].model_copy(update={"name": "category", "unique_count": 3}),
                profile.fields[0].model_copy(update={"name": "segment"}),
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
    assert publication.evidence.semantic_annotation_fingerprint == semantic_annotation_fingerprint(
        annotations
    )
    assert (
        invalidate_stale_insight(
            publication,
            current_working_dataset_version=1,
            semantic_annotations=annotations,
        )
        is publication
    )

    stale = invalidate_stale_insight(
        publication,
        current_working_dataset_version=2,
        semantic_annotations=(),
    )

    assert isinstance(stale, StaleInsight)
    assert stale.status is InsightStatus.STALE
    assert "Working Dataset version changed" in stale.reason
    assert "Semantic Annotations changed" in stale.reason
    assert "re-verification" in stale.reason
