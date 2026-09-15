"""Tests for deterministic Chart Intent validation and Plotly rendering."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from tabular_analytics_agent.data import QueryColumn, QueryResult
from tabular_analytics_agent.domain import (
    ActionStatus,
    ArtifactType,
    ChartIntent,
    SemanticAnnotation,
    ToolAction,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)
from tabular_analytics_agent.visualization import (
    ChartRenderResult,
    ChartValidationError,
    make_query_result_reference,
    render_chart,
    validate_chart_intent,
)


def sales_result() -> QueryResult:
    return QueryResult(
        query_id=uuid4(),
        dataset_id=uuid4(),
        working_dataset_version=1,
        sql="SELECT month, region, revenue, quantity FROM dataset ORDER BY month",
        columns=(
            QueryColumn(name="month", data_type="DATE"),
            QueryColumn(name="region", data_type="VARCHAR"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
            QueryColumn(name="quantity", data_type="INTEGER"),
        ),
        rows=(
            ("2026-01-01", "North", 100.0, 1),
            ("2026-02-01", "South", 150.0, 2),
            ("2026-03-01", "North", 125.0, 3),
        ),
        row_count=3,
        truncated=False,
        duration_ms=1,
    )


def intent(
    result: QueryResult,
    artifact_type: ArtifactType,
    *,
    x_field: str | None = None,
    y_fields: tuple[str, ...] = (),
    color_field: str | None = None,
    formatting_intent: dict[str, object] | None = None,
    semantic_annotations: tuple[SemanticAnnotation, ...] = (),
) -> ChartIntent:
    return ChartIntent(
        artifact_type=artifact_type,
        analytical_purpose="Explain the verified sales result",
        source_result_ref=make_query_result_reference(result, semantic_annotations),
        x_field=x_field,
        y_fields=y_fields,
        color_field=color_field,
        title="Sales result",
        labels={"month": "Month", "revenue": "Revenue (USD)"},
        formatting_intent=formatting_intent or {},
        validation_constraints=("Use only verified result values",),
    )


def verified_action(result: QueryResult) -> ToolAction:
    verification = VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="query", passed=True, message="Query passed."),),
    )
    return ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=result.working_dataset_version,
        inputs={
            "sql": result.sql,
            "required_fields": ["month", "region", "revenue", "quantity"],
        },
        status=ActionStatus.SUCCEEDED,
        output_ref=f"query-result:{result.query_id}",
        verification_results=(verification,),
    )


def confirmed_revenue_annotation(*, meaning: str = "Gross sales") -> SemanticAnnotation:
    return SemanticAnnotation(
        field_name="revenue",
        meaning=meaning,
        unit="USD",
        role="measure",
        confirmed_by_user=True,
    )


@pytest.mark.parametrize(
    ("artifact_type", "x_field", "y_fields", "expected_trace"),
    [
        (ArtifactType.TABLE, None, (), "table"),
        (ArtifactType.HISTOGRAM, "revenue", (), "histogram"),
        (ArtifactType.BAR, "month", ("revenue",), "bar"),
        (ArtifactType.LINE, "month", ("revenue",), "scatter"),
        (ArtifactType.SCATTER, "quantity", ("revenue",), "scatter"),
    ],
)
def test_supported_charts_render_json_safe_plotly_specs(
    artifact_type: ArtifactType,
    x_field: str | None,
    y_fields: tuple[str, ...],
    expected_trace: str,
) -> None:
    result = sales_result()
    publication = render_chart(
        intent(result, artifact_type, x_field=x_field, y_fields=y_fields),
        result,
        verified_action(result),
    )

    assert publication.verification.status is VerificationStatus.PASSED
    assert publication.source_result_ref == make_query_result_reference(result)
    assert publication.plotly_spec["data"][0]["type"] == expected_trace
    assert publication.plotly_spec["layout"]["title"]["text"] == "Sales result"


def test_bar_uses_a_category_axis_and_kpi_shows_every_digit() -> None:
    source = sales_result()
    bar = render_chart(
        intent(source, ArtifactType.BAR, x_field="quantity", y_fields=("revenue",)),
        source,
        verified_action(source),
    )
    single = source.model_copy(
        update={"rows": (("2026-01-01", "North", 5372.0, 1),), "row_count": 1}
    )
    kpi = render_chart(
        intent(single, ArtifactType.KPI, y_fields=("revenue",)),
        single,
        verified_action(single),
    )

    text_months = source.model_copy(
        update={"columns": (QueryColumn(name="month", data_type="VARCHAR"), *source.columns[1:])}
    )
    line = render_chart(
        intent(text_months, ArtifactType.LINE, x_field="month", y_fields=("revenue",)),
        text_months,
        verified_action(text_months),
    )

    assert bar.plotly_spec["layout"]["xaxis"]["type"] == "category"
    assert line.plotly_spec["layout"]["xaxis"]["type"] == "category"
    assert kpi.plotly_spec["data"][0]["number"]["valueformat"] == ",.12~g"


def test_kpi_requires_one_row_and_renders_exact_value() -> None:
    source = sales_result()
    result = source.model_copy(
        update={"rows": (("2026-01-01", "North", 375.0, 6),), "row_count": 1}
    )
    publication = render_chart(
        intent(
            result,
            ArtifactType.KPI,
            y_fields=("revenue",),
            formatting_intent={"number_format": ",.2f", "height": 320},
        ),
        result,
        verified_action(result),
    )

    trace = publication.plotly_spec["data"][0]
    assert trace["type"] == "indicator"
    assert trace["value"] == 375.0
    assert trace["number"]["valueformat"] == ",.2f"
    assert publication.plotly_spec["layout"]["height"] == 320


def test_color_grouping_creates_one_trace_per_group() -> None:
    result = sales_result()
    publication = render_chart(
        intent(
            result,
            ArtifactType.SCATTER,
            x_field="quantity",
            y_fields=("revenue",),
            color_field="region",
        ),
        result,
        verified_action(result),
    )

    assert [trace["name"] for trace in publication.plotly_spec["data"]] == [
        "North",
        "South",
    ]


@pytest.mark.parametrize(
    ("chart_intent", "error_fragment"),
    [
        (
            lambda result: intent(
                result, ArtifactType.BAR, x_field="unknown", y_fields=("revenue",)
            ),
            "Unknown chart fields",
        ),
        (
            lambda result: intent(result, ArtifactType.HISTOGRAM, x_field="region"),
            "Numeric encodings required",
        ),
        (
            lambda result: intent(result, ArtifactType.HEATMAP, x_field="region"),
            "not implemented",
        ),
        (
            lambda result: intent(
                result,
                ArtifactType.BAR,
                x_field="region",
                y_fields=("revenue",),
                formatting_intent={"template": "custom"},
            ),
            "Unsupported formatting keys",
        ),
    ],
)
def test_invalid_intents_fail_before_plotly_rendering(
    chart_intent: object,
    error_fragment: str,
) -> None:
    result = sales_result()
    assert callable(chart_intent)
    candidate = chart_intent(result)
    assert isinstance(candidate, ChartIntent)

    with pytest.raises(ChartValidationError, match=error_fragment):
        render_chart(candidate, result, verified_action(result))


def test_exact_result_binding_and_complete_rows_are_required() -> None:
    result = sales_result()
    candidate = intent(result, ArtifactType.BAR, x_field="region", y_fields=("revenue",))
    mismatched = candidate.model_copy(
        update={
            "source_result_ref": candidate.source_result_ref.model_copy(
                update={"query_id": uuid4()}
            )
        }
    )
    truncated = result.model_copy(update={"truncated": True})

    source_action = verified_action(result)
    binding = validate_chart_intent(mismatched, result, source_action)
    completeness = validate_chart_intent(candidate, truncated, source_action)

    assert binding.status is VerificationStatus.FAILED
    assert not next(
        check for check in binding.checks if check.name == "source_result_binding"
    ).passed
    assert completeness.status is VerificationStatus.FAILED
    assert not next(check for check in completeness.checks if check.name == "usable_result").passed


def test_confirmed_semantic_annotations_bind_query_chart_successfully() -> None:
    result = sales_result()
    annotations = (confirmed_revenue_annotation(),)
    candidate = intent(
        result,
        ArtifactType.BAR,
        x_field="month",
        y_fields=("revenue",),
        semantic_annotations=annotations,
    )

    publication = render_chart(candidate, result, verified_action(result), annotations)

    assert publication.verification.status is VerificationStatus.PASSED
    assert publication.source_result_ref == make_query_result_reference(result, annotations)


def test_changed_semantic_annotations_reject_existing_chart_binding() -> None:
    result = sales_result()
    confirmed = (confirmed_revenue_annotation(),)
    changed = (confirmed_revenue_annotation(meaning="Net revenue"),)
    candidate = intent(
        result,
        ArtifactType.BAR,
        x_field="month",
        y_fields=("revenue",),
        semantic_annotations=confirmed,
    )

    verification = validate_chart_intent(candidate, result, verified_action(result), changed)

    assert verification.status is VerificationStatus.FAILED
    assert not next(
        check for check in verification.checks if check.name == "source_result_binding"
    ).passed


def test_semantic_binding_keeps_result_and_version_checks() -> None:
    result = sales_result()
    annotations = (confirmed_revenue_annotation(),)
    candidate = intent(
        result,
        ArtifactType.BAR,
        x_field="month",
        y_fields=("revenue",),
        semantic_annotations=annotations,
    )
    wrong_version_action = verified_action(result).model_copy(
        update={"working_dataset_version": result.working_dataset_version + 1}
    )

    verification = validate_chart_intent(candidate, result, wrong_version_action, annotations)

    assert verification.status is VerificationStatus.FAILED
    assert next(
        check for check in verification.checks if check.name == "source_result_binding"
    ).passed
    assert not next(
        check for check in verification.checks if check.name == "verified_source_action"
    ).passed


def test_render_result_contract_requires_verified_matching_source() -> None:
    result = sales_result()
    publication = render_chart(
        intent(result, ArtifactType.LINE, x_field="month", y_fields=("revenue",)),
        result,
        verified_action(result),
    )
    other_source = publication.source_result_ref.model_copy(update={"query_id": uuid4()})
    with pytest.raises(ValidationError, match="same Query Result"):
        ChartRenderResult(
            intent=publication.intent,
            source_result_ref=other_source,
            plotly_spec=publication.plotly_spec,
            verification=publication.verification,
        )

    failed = VerificationResult(
        status=VerificationStatus.FAILED,
        checks=(VerificationCheck(name="chart", passed=False, message="Chart failed."),),
    )
    with pytest.raises(ValidationError, match="requires passed verification"):
        ChartRenderResult(
            intent=publication.intent,
            source_result_ref=publication.source_result_ref,
            plotly_spec=publication.plotly_spec,
            verification=failed,
        )


def test_renderer_never_performs_model_requested_aggregation() -> None:
    result = sales_result()
    candidate = intent(
        result,
        ArtifactType.BAR,
        x_field="region",
        y_fields=("revenue",),
    ).model_copy(update={"aggregation": "sum"})

    with pytest.raises(ChartValidationError, match="does not aggregate"):
        render_chart(candidate, result, verified_action(result))


def test_unverified_or_mismatched_tool_action_cannot_source_a_chart() -> None:
    result = sales_result()
    candidate = intent(result, ArtifactType.BAR, x_field="region", y_fields=("revenue",))
    action = verified_action(result).model_copy(update={"output_ref": "query-result:other"})

    verification = validate_chart_intent(candidate, result, action)

    assert verification.status is VerificationStatus.FAILED
    assert not next(
        check for check in verification.checks if check.name == "source_result_binding"
    ).passed

    wrong_sql_action = verified_action(result).model_copy(
        update={"inputs": {"sql": "SELECT * FROM another_result"}}
    )
    sql_verification = validate_chart_intent(candidate, result, wrong_sql_action)
    assert not next(
        check for check in sql_verification.checks if check.name == "reproducible_query"
    ).passed

    failed_action = ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=result.working_dataset_version,
        inputs={"sql": result.sql},
        status=ActionStatus.FAILED,
        output_ref=f"query-result:{result.query_id}",
        error="Query verification failed",
    )
    failed_verification = validate_chart_intent(candidate, result, failed_action)
    assert not next(
        check for check in failed_verification.checks if check.name == "verified_source_action"
    ).passed


def test_formatting_values_are_validated_before_plotly() -> None:
    result = sales_result()
    candidate = intent(
        result,
        ArtifactType.BAR,
        x_field="region",
        y_fields=("revenue",),
        formatting_intent={"height": "huge"},
    )

    with pytest.raises(ChartValidationError, match="height must be an integer"):
        render_chart(candidate, result, verified_action(result))


def test_bar_requires_query_to_produce_unique_aggregate_keys() -> None:
    result = sales_result()
    candidate = intent(
        result,
        ArtifactType.BAR,
        x_field="region",
        y_fields=("revenue",),
    )

    with pytest.raises(ChartValidationError, match="aggregate the query first"):
        render_chart(candidate, result, verified_action(result))
