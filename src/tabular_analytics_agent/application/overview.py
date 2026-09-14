"""Deterministic Data Overview built from the Data Profile and bounded read-only queries.

No model is called. Every chart is a descriptive fact about the uploaded data, never a Verified
Insight, and fixed design limits keep the overview small whatever the number of fields.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import plotly.graph_objects as go

from tabular_analytics_agent.data import (
    DatasetHandle,
    QueryExecutionError,
    QueryTimeoutError,
    TabularDataCore,
    UnsafeQueryError,
)
from tabular_analytics_agent.domain import (
    IDENTIFIER_LIKE_WARNING,
    DataProfile,
    FieldKind,
    FieldProfile,
)

_MAX_FIELD_CHARTS = 8
_MAX_CORRELATION_FIELDS = 15
_MAX_SCATTER_PAIRS = 3
_MIN_ABSOLUTE_CORRELATION = 0.5
_MAX_GROUP_CATEGORIES = 20
_MAX_TIME_FIELDS = 2
_HISTOGRAM_BINS = 20
_MAX_SCATTER_POINTS = 2_000
_DAILY_SPAN_DAYS = 62
_QUERY_ERRORS = (QueryExecutionError, QueryTimeoutError, UnsafeQueryError)


@dataclass(frozen=True)
class OverviewChart:
    title: str
    caption: str
    figure: dict[str, Any]
    # Offered next to a descriptive chart so the agent can test it properly.
    suggested_question: str | None = None


@dataclass(frozen=True)
class DataOverview:
    row_count: int
    column_count: int
    duplicate_row_count: int
    missing_rate: float
    charts: tuple[OverviewChart, ...]
    omitted: tuple[str, ...] = ()


def build_data_overview(
    core: TabularDataCore, handle: DatasetHandle, profile: DataProfile
) -> DataOverview:
    """Build the overview; a chart whose query fails is listed as omitted instead of raising."""
    charts: list[OverviewChart] = []
    omitted: list[str] = []

    def add(title: str, build: Any) -> None:
        try:
            chart = build()
        except _QUERY_ERRORS as exc:
            omitted.append(f"{title} was omitted: {exc}")
            return
        if chart is not None:
            charts.append(chart)

    add("Missing values chart", lambda: _missing_values_chart(profile))
    eligible = _eligible_fields(profile)
    field_charts = [f for f in eligible if f.kind is not FieldKind.DATETIME][:_MAX_FIELD_CHARTS]
    for field in field_charts:
        add(f"Chart of {field.name}", lambda field=field: _field_chart(core, handle, field))

    numeric = [f for f in eligible if f.kind is FieldKind.NUMERIC][:_MAX_CORRELATION_FIELDS]
    coefficients: dict[tuple[str, str], float] = {}
    if len(numeric) >= 2:
        try:
            coefficients = _correlations(core, handle, numeric)
        except _QUERY_ERRORS as exc:
            omitted.append(f"Correlation heatmap was omitted: {exc}")
        else:
            charts.append(_heatmap(numeric, coefficients))
    strongest = sorted(
        (pair for pair, value in coefficients.items() if abs(value) >= _MIN_ABSOLUTE_CORRELATION),
        key=lambda pair: -abs(coefficients[pair]),
    )[:_MAX_SCATTER_PAIRS]
    for pair in strongest:
        add(
            f"Scatter of {pair[0]} and {pair[1]}",
            lambda pair=pair: _scatter_chart(core, handle, pair, coefficients[pair]),
        )

    groups = [
        f
        for f in eligible
        if f.kind in {FieldKind.CATEGORICAL, FieldKind.BOOLEAN}
        and 2 <= f.unique_count <= _MAX_GROUP_CATEGORIES
    ]
    if numeric and groups:
        add(
            f"Average {numeric[0].name} by {groups[0].name}",
            lambda: _group_average_chart(core, handle, numeric[0], groups[0]),
        )
    dates = [f for f in eligible if f.kind is FieldKind.DATETIME][:_MAX_TIME_FIELDS]
    for field in dates:
        add(f"Rows over {field.name}", lambda field=field: _time_chart(core, handle, field))

    cells = profile.row_count * len(profile.fields)
    return DataOverview(
        row_count=profile.row_count,
        column_count=len(profile.fields),
        duplicate_row_count=profile.duplicate_row_count,
        missing_rate=sum(f.missing_count for f in profile.fields) / cells if cells else 0.0,
        charts=tuple(charts),
        omitted=tuple(omitted),
    )


def _eligible_fields(profile: DataProfile) -> list[FieldProfile]:
    pii = set(profile.pii_candidates)
    usable = [
        field
        for field in profile.fields
        if field.name not in pii
        and IDENTIFIER_LIKE_WARNING not in field.warnings
        and field.unique_count > 1
        and field.kind
        in {FieldKind.NUMERIC, FieldKind.CATEGORICAL, FieldKind.BOOLEAN, FieldKind.DATETIME}
    ]
    # Fields with fewer missing values describe more of the data; sorting is stable.
    return sorted(usable, key=lambda field: field.missing_count)


def _missing_values_chart(profile: DataProfile) -> OverviewChart | None:
    missing = [field for field in profile.fields if field.missing_count]
    if not missing:
        return None
    figure = go.Figure(
        go.Bar(
            x=[field.missing_rate for field in missing],
            y=[field.name for field in missing],
            orientation="h",
            text=[f"{field.missing_count:,}" for field in missing],
        )
    )
    figure.update_xaxes(tickformat=".0%", range=[0, 1])
    figure.update_yaxes(type="category")
    return _chart(
        figure,
        "Missing values by field",
        "Share of rows with a missing value; labels show the count.",
    )


def _field_chart(
    core: TabularDataCore, handle: DatasetHandle, field: FieldProfile
) -> OverviewChart | None:
    if field.kind is FieldKind.NUMERIC:
        return _histogram(core, handle, field)
    if not field.top_values:
        return None
    figure = go.Figure(
        go.Bar(
            x=[str(item.value) for item in field.top_values],
            y=[item.count for item in field.top_values],
        )
    )
    figure.update_xaxes(type="category")
    return _chart(
        figure,
        f"Most frequent values of {field.name}",
        f"Top {len(field.top_values)} of {field.unique_count:,} distinct values.",
    )


def _histogram(
    core: TabularDataCore, handle: DatasetHandle, field: FieldProfile
) -> OverviewChart | None:
    summary = field.numeric_summary
    if summary is None or summary.minimum is None or summary.maximum is None:
        return None
    if summary.maximum <= summary.minimum:
        return None
    width = (summary.maximum - summary.minimum) / _HISTOGRAM_BINS
    column = _quote(field.name)
    result = core.query(
        handle,
        f"SELECT LEAST(FLOOR(({column} - ({summary.minimum!r})) / {width!r}), "
        f"{_HISTOGRAM_BINS - 1}) AS bin_index, COUNT(*) AS row_count FROM dataset "
        f"WHERE {column} IS NOT NULL GROUP BY bin_index ORDER BY bin_index",
    )
    counts = {int(index): count for index, count in result.rows if isinstance(index, int | float)}
    figure = go.Figure(
        go.Bar(
            x=[summary.minimum + (index + 0.5) * width for index in counts],
            y=list(counts.values()),
            width=width,
        )
    )
    return _chart(
        figure,
        f"Distribution of {field.name}",
        f"Row counts in {_HISTOGRAM_BINS} equal-width bins; missing values are excluded.",
    )


def _correlations(
    core: TabularDataCore, handle: DatasetHandle, fields: list[FieldProfile]
) -> dict[tuple[str, str], float]:
    pairs = [
        (first.name, second.name)
        for index, first in enumerate(fields)
        for second in fields[index + 1 :]
    ]
    expressions = ", ".join(
        f"CORR({_quote(first)}, {_quote(second)}) AS r_{index}"
        for index, (first, second) in enumerate(pairs)
    )
    result = core.query(handle, f"SELECT {expressions} FROM dataset")
    values = result.rows[0] if result.rows else ()
    return {
        pair: float(value)
        for pair, value in zip(pairs, values, strict=False)
        if isinstance(value, int | float) and math.isfinite(value)
    }


def _heatmap(
    fields: list[FieldProfile], coefficients: dict[tuple[str, str], float]
) -> OverviewChart:
    names = [field.name for field in fields]

    def coefficient(first: str, second: str) -> float | None:
        if first == second:
            return 1.0
        return coefficients.get((first, second), coefficients.get((second, first)))

    figure = go.Figure(
        go.Heatmap(
            z=[[coefficient(row, column) for column in names] for row in names],
            x=names,
            y=names,
            zmin=-1,
            zmax=1,
            zmid=0,
            colorscale="RdBu",
            texttemplate="%{z:.2f}",
        )
    )
    figure.update_xaxes(type="category")
    figure.update_yaxes(type="category", autorange="reversed")
    return _chart(
        figure,
        "Correlation between numeric fields",
        "Pearson coefficients on pairwise-complete rows. Descriptive only: no test was run, "
        "and correlation does not imply causation.",
    )


def _scatter_chart(
    core: TabularDataCore,
    handle: DatasetHandle,
    pair: tuple[str, str],
    coefficient: float,
) -> OverviewChart:
    first, second = (_quote(name) for name in pair)
    result = core.query(
        handle,
        f"SELECT {first}, {second} FROM dataset "
        f"WHERE {first} IS NOT NULL AND {second} IS NOT NULL LIMIT {_MAX_SCATTER_POINTS}",
    )
    figure = go.Figure(
        go.Scatter(
            x=[row[0] for row in result.rows],
            y=[row[1] for row in result.rows],
            mode="markers",
            marker={"opacity": 0.6},
        )
    )
    figure.update_xaxes(title_text=pair[0])
    figure.update_yaxes(title_text=pair[1])
    shown = f"the first {len(result.rows):,} complete rows"
    return OverviewChart(
        title=f"{pair[0]} and {pair[1]}",
        caption=f"Pearson r = {coefficient:.2f}; showing {shown}. Descriptive only.",
        figure=_figure_json(figure, f"{pair[0]} and {pair[1]}"),
        suggested_question=f"Is {pair[0]} correlated with {pair[1]}?",
    )


def _group_average_chart(
    core: TabularDataCore,
    handle: DatasetHandle,
    measure: FieldProfile,
    group: FieldProfile,
) -> OverviewChart:
    measure_column, group_column = _quote(measure.name), _quote(group.name)
    result = core.query(
        handle,
        f"SELECT {group_column}, AVG({measure_column}) AS average_value FROM dataset "
        f"WHERE {group_column} IS NOT NULL GROUP BY {group_column} ORDER BY {group_column}",
    )
    figure = go.Figure(
        go.Bar(x=[str(row[0]) for row in result.rows], y=[row[1] for row in result.rows])
    )
    figure.update_xaxes(type="category")
    return _chart(
        figure,
        f"Average {measure.name} by {group.name}",
        "Descriptive averages; missing values are excluded.",
    )


def _time_chart(
    core: TabularDataCore, handle: DatasetHandle, field: FieldProfile
) -> OverviewChart | None:
    summary = field.temporal_summary
    if summary is None:
        return None
    span = date.fromisoformat(summary.latest[:10]) - date.fromisoformat(summary.earliest[:10])
    unit, label_length = ("day", 10) if span.days <= _DAILY_SPAN_DAYS else ("month", 7)
    column = _quote(field.name)
    result = core.query(
        handle,
        f"SELECT DATE_TRUNC('{unit}', {column}) AS period, COUNT(*) AS row_count "
        f"FROM dataset WHERE {column} IS NOT NULL GROUP BY period ORDER BY period",
    )
    figure = go.Figure(
        go.Scatter(
            x=[str(row[0])[:label_length] for row in result.rows],
            y=[row[1] for row in result.rows],
            mode="lines+markers",
        )
    )
    figure.update_xaxes(type="category")
    return _chart(
        figure,
        f"Rows per {unit} of {field.name}",
        "Row counts per period; missing dates are excluded.",
    )


def _chart(figure: go.Figure, title: str, caption: str) -> OverviewChart:
    return OverviewChart(title=title, caption=caption, figure=_figure_json(figure, title))


def _figure_json(figure: go.Figure, title: str) -> dict[str, Any]:
    figure.update_layout(
        title=title,
        template="plotly_white",
        height=340,
        margin={"l": 40, "r": 20, "t": 50, "b": 40},
        showlegend=False,
    )
    payload: dict[str, Any] = json.loads(figure.to_json())
    return payload


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


__all__ = ["DataOverview", "OverviewChart", "build_data_overview"]
