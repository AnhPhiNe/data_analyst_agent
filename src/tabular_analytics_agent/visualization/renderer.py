"""Validate Chart Intents and render exact QueryResult values as Plotly JSON."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import plotly.graph_objects as go

from tabular_analytics_agent.data import QueryResult
from tabular_analytics_agent.domain import (
    ActionStatus,
    ArtifactType,
    ChartIntent,
    QueryResultReference,
    SemanticAnnotation,
    ToolAction,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    fingerprint_semantic_annotations,
)
from tabular_analytics_agent.visualization.errors import ChartValidationError
from tabular_analytics_agent.visualization.models import ChartRenderResult

_SUPPORTED_TYPES = {
    ArtifactType.KPI,
    ArtifactType.TABLE,
    ArtifactType.HISTOGRAM,
    ArtifactType.BAR,
    ArtifactType.LINE,
    ArtifactType.SCATTER,
}
_NUMERIC_TYPE_MARKERS = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "REAL",
)
_MAX_TABLE_ROWS = 500
_MAX_BAR_CATEGORIES = 50
_MAX_COLOR_GROUPS = 12
_MAX_POINT_ROWS = 5_000
SUPPORTED_FORMATTING_KEYS = {"height", "number_format", "show_legend"}


def make_query_result_reference(
    result: QueryResult,
    semantic_annotations: tuple[SemanticAnnotation, ...] = (),
) -> QueryResultReference:
    """Create the typed identity used by Chart Intents and persisted artifacts."""
    return QueryResultReference(
        query_id=result.query_id,
        dataset_id=result.dataset_id,
        working_dataset_version=result.working_dataset_version,
        semantic_annotation_fingerprint=fingerprint_semantic_annotations(semantic_annotations),
    )


def validate_chart_intent(
    intent: ChartIntent,
    result: QueryResult,
    source_action: ToolAction,
    semantic_annotations: tuple[SemanticAnnotation, ...] = (),
) -> VerificationResult:
    """Check source identity, encodings, types, and MVP readability limits."""
    # Build the expected identity from trusted current session state.  The intent is
    # model-provided data and must never be used to choose the expected fingerprint.
    expected_ref = make_query_result_reference(result, semantic_annotations)
    column_types = {column.name: column.data_type for column in result.columns}
    referenced_fields = tuple(
        dict.fromkeys(
            field
            for field in (intent.x_field, *intent.y_fields, intent.color_field)
            if field is not None
        )
    )
    unknown_fields = sorted(set(referenced_fields) - set(column_types))
    structural_errors = _structural_errors(intent)
    type_errors = _type_errors(intent, column_types) if not unknown_fields else ()
    readability_errors = _readability_errors(intent, result) if not unknown_fields else ()
    formatting_errors = _formatting_errors(intent)
    unknown_labels = sorted(set(intent.labels) - set(column_types))
    source_action_verified = _is_verified_source_action(source_action, result)
    recorded_sql = source_action.inputs.get("sql")
    checks = (
        VerificationCheck(
            name="source_result_binding",
            passed=(
                intent.source_result_ref == expected_ref
                and source_action.output_ref == expected_ref.canonical_ref
            ),
            message=(
                "Chart Intent and Tool Action reference the exact Query Result."
                if intent.source_result_ref == expected_ref
                and source_action.output_ref == expected_ref.canonical_ref
                else "Chart Intent or Tool Action does not reference this Query Result."
            ),
        ),
        VerificationCheck(
            name="verified_source_action",
            passed=source_action_verified,
            message=(
                "Source Tool Action succeeded, passed verification, and matches the result version."
                if source_action_verified
                else (
                    "Chart source must be a passed read-only SQL Tool Action at the result version."
                )
            ),
        ),
        VerificationCheck(
            name="reproducible_query",
            passed=isinstance(recorded_sql, str) and recorded_sql == result.sql,
            message=(
                "Tool Action records the exact SQL that produced the Query Result."
                if isinstance(recorded_sql, str) and recorded_sql == result.sql
                else "Tool Action SQL does not exactly match the Query Result SQL."
            ),
        ),
        VerificationCheck(
            name="supported_artifact_type",
            passed=intent.artifact_type in _SUPPORTED_TYPES,
            message=(
                "Artifact type is supported by the MVP renderer."
                if intent.artifact_type in _SUPPORTED_TYPES
                else f"Artifact type {intent.artifact_type.value!r} is not implemented in M5.1."
            ),
        ),
        VerificationCheck(
            name="usable_result",
            passed=result.row_count > 0 and not result.truncated,
            message=(
                "Query Result is non-empty and complete."
                if result.row_count > 0 and not result.truncated
                else "Charts require a non-empty, non-truncated Query Result."
            ),
        ),
        VerificationCheck(
            name="field_grounding",
            passed=not unknown_fields and not unknown_labels,
            message=(
                "All encodings and labels reference exact result columns."
                if not unknown_fields and not unknown_labels
                else "Unknown chart fields: " + ", ".join((*unknown_fields, *unknown_labels)) + "."
            ),
        ),
        VerificationCheck(
            name="encoding_shape",
            passed=not structural_errors,
            message=(
                "Chart encodings match the artifact type."
                if not structural_errors
                else "; ".join(structural_errors)
            ),
        ),
        VerificationCheck(
            name="field_types",
            passed=not type_errors,
            message=(
                "Encoded fields have compatible result types."
                if not type_errors
                else "; ".join(type_errors)
            ),
        ),
        VerificationCheck(
            name="readability",
            passed=not readability_errors,
            message=(
                "Result size and cardinality are readable for this chart."
                if not readability_errors
                else "; ".join(readability_errors)
            ),
        ),
        VerificationCheck(
            name="formatting_policy",
            passed=not formatting_errors,
            message=(
                "Formatting uses the deterministic allowlist."
                if not formatting_errors
                else "; ".join(formatting_errors)
            ),
        ),
    )
    passed = all(check.passed for check in checks)
    return VerificationResult(
        status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
        checks=checks,
    )


def render_chart(
    intent: ChartIntent,
    result: QueryResult,
    source_action: ToolAction,
    semantic_annotations: tuple[SemanticAnnotation, ...] = (),
) -> ChartRenderResult:
    """Render a validated Chart Intent without recomputing or aggregating source values."""
    verification = validate_chart_intent(
        intent,
        result,
        source_action,
        semantic_annotations,
    )
    if verification.status is VerificationStatus.FAILED:
        reasons = "; ".join(check.message for check in verification.checks if not check.passed)
        raise ChartValidationError(reasons)

    rows = [
        dict(zip((column.name for column in result.columns), row, strict=True))
        for row in result.rows
    ]
    renderers: dict[ArtifactType, Callable[[ChartIntent, list[dict[str, Any]]], go.Figure]] = {
        ArtifactType.KPI: _render_kpi,
        ArtifactType.TABLE: _render_table,
        ArtifactType.HISTOGRAM: _render_histogram,
        ArtifactType.BAR: _render_bar,
        ArtifactType.LINE: _render_line,
        ArtifactType.SCATTER: _render_scatter,
    }
    figure = renderers[intent.artifact_type](intent, rows)
    _apply_layout(figure, intent)
    return ChartRenderResult(
        intent=intent,
        source_result_ref=intent.source_result_ref,
        plotly_spec=json.loads(figure.to_json()),
        verification=verification,
    )


def _structural_errors(intent: ChartIntent) -> tuple[str, ...]:
    errors: list[str] = []
    chart_type = intent.artifact_type
    if intent.aggregation:
        errors.append("Renderer does not aggregate; aggregation must occur in the verified query")
    if chart_type is ArtifactType.KPI:
        if intent.x_field or len(intent.y_fields) != 1 or intent.color_field:
            errors.append("KPI requires exactly one y field and no x or color field")
    elif chart_type is ArtifactType.TABLE:
        if intent.x_field or intent.y_fields or intent.color_field:
            errors.append("Table renders all result columns and takes no encoding fields")
    elif chart_type is ArtifactType.HISTOGRAM:
        if not intent.x_field or intent.y_fields or intent.color_field:
            errors.append("Histogram requires one x field and no y or color field")
    elif chart_type in {ArtifactType.BAR, ArtifactType.LINE}:
        if not intent.x_field or not intent.y_fields:
            errors.append("Bar and line charts require x and at least one y field")
        if intent.color_field and len(intent.y_fields) != 1:
            errors.append("Color grouping requires exactly one y field")
    elif chart_type is ArtifactType.SCATTER and (
        not intent.x_field or len(intent.y_fields) != 1 or intent.x_field == intent.y_fields[0]
    ):
        errors.append("Scatter requires distinct x and y fields")
    return tuple(errors)


def _type_errors(intent: ChartIntent, column_types: dict[str, str]) -> tuple[str, ...]:
    errors: list[str] = []
    numeric_fields: tuple[str, ...] = ()
    if intent.artifact_type is ArtifactType.KPI:
        numeric_fields = intent.y_fields
    elif intent.artifact_type is ArtifactType.HISTOGRAM and intent.x_field:
        numeric_fields = (intent.x_field,)
    elif intent.artifact_type in {ArtifactType.BAR, ArtifactType.LINE}:
        numeric_fields = intent.y_fields
    elif intent.artifact_type is ArtifactType.SCATTER:
        numeric_fields = tuple(
            field for field in (intent.x_field, *intent.y_fields) if field is not None
        )
    invalid_numeric = [field for field in numeric_fields if not _is_numeric(column_types[field])]
    if invalid_numeric:
        errors.append("Numeric encodings required for: " + ", ".join(invalid_numeric))
    return tuple(errors)


def _formatting_errors(intent: ChartIntent) -> tuple[str, ...]:
    errors = [
        "Unsupported formatting keys: " + ", ".join(unknown) + "."
        for unknown in [sorted(set(intent.formatting_intent) - SUPPORTED_FORMATTING_KEYS)]
        if unknown
    ]
    height = intent.formatting_intent.get("height")
    if height is not None and (
        isinstance(height, bool) or not isinstance(height, int) or not 240 <= height <= 1_200
    ):
        errors.append("Formatting height must be an integer from 240 to 1200")
    number_format = intent.formatting_intent.get("number_format")
    if number_format is not None and (
        not isinstance(number_format, str) or len(number_format) > 32
    ):
        errors.append("Number format must be a string no longer than 32 characters")
    show_legend = intent.formatting_intent.get("show_legend")
    if show_legend is not None and not isinstance(show_legend, bool):
        errors.append("show_legend must be boolean")
    return tuple(errors)


def _readability_errors(intent: ChartIntent, result: QueryResult) -> tuple[str, ...]:
    errors: list[str] = []
    values = _column_values(result)
    if intent.artifact_type is ArtifactType.KPI and result.row_count != 1:
        errors.append("KPI requires exactly one result row")
    if intent.artifact_type is ArtifactType.TABLE and result.row_count > _MAX_TABLE_ROWS:
        errors.append(f"Table exceeds the {_MAX_TABLE_ROWS}-row display limit")
    if (
        intent.artifact_type is ArtifactType.BAR
        and intent.x_field
        and len(set(values[intent.x_field])) > _MAX_BAR_CATEGORIES
    ):
        errors.append(f"Bar chart exceeds the {_MAX_BAR_CATEGORIES}-category limit")
    if intent.artifact_type is ArtifactType.BAR and intent.x_field:
        if intent.color_field:
            row_keys: list[object] = list(
                zip(values[intent.x_field], values[intent.color_field], strict=True)
            )
        else:
            row_keys = list(values[intent.x_field])
        if len(set(row_keys)) != len(row_keys):
            errors.append(
                "Bar chart requires one verified row per x and color key; aggregate the query first"
            )
    if (
        intent.artifact_type in {ArtifactType.LINE, ArtifactType.SCATTER}
        and result.row_count > _MAX_POINT_ROWS
    ):
        errors.append(f"Chart exceeds the {_MAX_POINT_ROWS}-point limit")
    if intent.color_field and len(set(values[intent.color_field])) > _MAX_COLOR_GROUPS:
        errors.append(f"Color encoding exceeds the {_MAX_COLOR_GROUPS}-group limit")
    return tuple(errors)


def _render_kpi(intent: ChartIntent, rows: list[dict[str, Any]]) -> go.Figure:
    field = intent.y_fields[0]
    return go.Figure(
        go.Indicator(
            mode="number",
            value=rows[0][field],
            title={"text": intent.labels.get(field, field)},
            # Plotly's default indicator format rounds to three significant digits (5372 -> 5370).
            number={"valueformat": intent.formatting_intent.get("number_format", ",.12~g")},
        )
    )


def _render_table(intent: ChartIntent, rows: list[dict[str, Any]]) -> go.Figure:
    fields = tuple(rows[0])
    return go.Figure(
        go.Table(
            header={"values": [intent.labels.get(field, field) for field in fields]},
            cells={"values": [[row[field] for row in rows] for field in fields]},
        )
    )


def _render_histogram(intent: ChartIntent, rows: list[dict[str, Any]]) -> go.Figure:
    field = _required(intent.x_field)
    return go.Figure(
        go.Histogram(x=[row[field] for row in rows], name=intent.labels.get(field, field))
    )


def _render_bar(intent: ChartIntent, rows: list[dict[str, Any]]) -> go.Figure:
    figure = _render_xy(intent, rows, go.Bar)
    # Bars label discrete groups; a numeric key such as month must not become a continuous axis.
    figure.update_xaxes(type="category")
    return figure


def _render_line(intent: ChartIntent, rows: list[dict[str, Any]]) -> go.Figure:
    figure = _render_xy(intent, rows, lambda **kwargs: go.Scatter(mode="lines+markers", **kwargs))
    x_field = _required(intent.x_field)
    if any(isinstance(row[x_field], str) for row in rows):
        # Text periods such as "2026-01" or "Q1" are ordered labels kept in result order.
        figure.update_xaxes(type="category")
    return figure


def _render_scatter(intent: ChartIntent, rows: list[dict[str, Any]]) -> go.Figure:
    return _render_xy(intent, rows, lambda **kwargs: go.Scatter(mode="markers", **kwargs))


def _render_xy(
    intent: ChartIntent,
    rows: list[dict[str, Any]],
    trace_factory: Callable[..., Any],
) -> go.Figure:
    x_field = _required(intent.x_field)
    figure = go.Figure()
    if intent.color_field:
        color_field = intent.color_field
        groups = tuple(dict.fromkeys(row[color_field] for row in rows))
        y_field = intent.y_fields[0]
        for group in groups:
            selected = [row for row in rows if row[color_field] == group]
            figure.add_trace(
                trace_factory(
                    x=[row[x_field] for row in selected],
                    y=[row[y_field] for row in selected],
                    name=str(group),
                )
            )
    else:
        for y_field in intent.y_fields:
            figure.add_trace(
                trace_factory(
                    x=[row[x_field] for row in rows],
                    y=[row[y_field] for row in rows],
                    name=intent.labels.get(y_field, y_field),
                )
            )
    return figure


def _apply_layout(figure: go.Figure, intent: ChartIntent) -> None:
    layout: dict[str, Any] = {
        "title": {"text": intent.title},
        "template": "plotly_white",
        "showlegend": intent.formatting_intent.get("show_legend", True),
    }
    if "height" in intent.formatting_intent:
        layout["height"] = intent.formatting_intent["height"]
    if intent.x_field:
        layout["xaxis_title"] = intent.labels.get(intent.x_field, intent.x_field)
    if len(intent.y_fields) == 1:
        field = intent.y_fields[0]
        layout["yaxis_title"] = intent.labels.get(field, field)
    figure.update_layout(**layout)


def _column_values(result: QueryResult) -> dict[str, tuple[Any, ...]]:
    return {
        column.name: tuple(row[index] for row in result.rows)
        for index, column in enumerate(result.columns)
    }


def _is_verified_source_action(action: ToolAction, result: QueryResult) -> bool:
    return (
        action.tool_name == "read_only_sql"
        and action.status is ActionStatus.SUCCEEDED
        and bool(action.verification_results)
        and all(
            verification.status is VerificationStatus.PASSED
            for verification in action.verification_results
        )
        and action.working_dataset_version == result.working_dataset_version
    )


def _is_numeric(data_type: str) -> bool:
    normalized = data_type.upper()
    return any(normalized.startswith(marker) for marker in _NUMERIC_TYPE_MARKERS)


def _required(value: str | None) -> str:
    if value is None:
        raise AssertionError("validated chart field is unexpectedly absent")
    return value
