"""Deterministic Data Overview built from the Data Profile and bounded read-only queries.

No model is called. Every chart is a descriptive fact about the uploaded data, never a Verified
Insight, and fixed design limits keep the overview small whatever the number of fields.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import plotly.graph_objects as go

from tabular_analytics_agent.application.suggestions import is_group_field
from tabular_analytics_agent.data import (
    DatasetHandle,
    QueryExecutionError,
    QueryTimeoutError,
    TabularDataCore,
    UnsafeQueryError,
    quote_identifier,
)
from tabular_analytics_agent.domain import (
    IDENTIFIER_LIKE_WARNING,
    DataProfile,
    FieldKind,
    FieldProfile,
)
from tabular_analytics_agent.visualization import exact_number_format

_MAX_FIELD_CHARTS = 8
_MAX_CORRELATION_FIELDS = 15
_MAX_SCATTER_PAIRS = 3
_MIN_ABSOLUTE_CORRELATION = 0.5
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

    groups = [f for f in eligible if is_group_field(f)]
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
    column = quote_identifier(field.name)
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
        f"CORR({quote_identifier(first)}, {quote_identifier(second)}) AS r_{index}"
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
    first, second = (quote_identifier(name) for name in pair)
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
    measure_column, group_column = quote_identifier(measure.name), quote_identifier(group.name)
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
    column = quote_identifier(field.name)
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


_MAX_EXPLORER_GROUPS = 20
_MAX_LINE_GROUPS = 12
_MAX_FILTER_FIELDS = 3
_MAX_FILTER_VALUES = 100
_AGGREGATIONS = {
    "count": "Row count",
    "sum": "Total",
    "average": "Average",
    "minimum": "Minimum",
    "maximum": "Maximum",
}
_SQL_AGGREGATES = {"sum": "SUM", "average": "AVG", "minimum": "MIN", "maximum": "MAX"}
PERIODS = ("day", "week", "month", "quarter", "year")


@dataclass(frozen=True)
class ExplorerOptions:
    """Fields the explorer may use; PII, identifier-like, and constant fields are excluded."""

    measures: tuple[str, ...]
    groups: tuple[str, ...]
    dates: tuple[str, ...]


@dataclass(frozen=True)
class ExplorerRequest:
    measure: str | None = None
    aggregation: str = "count"
    group: str | None = None
    date_field: str | None = None
    period: str = "month"
    filters: tuple[tuple[str, tuple[str, ...]], ...] = ()
    date_filter: tuple[str, date, date] | None = None


def explorer_options(profile: DataProfile) -> ExplorerOptions:
    eligible = _eligible_fields(profile)
    return ExplorerOptions(
        measures=tuple(f.name for f in eligible if f.kind is FieldKind.NUMERIC),
        groups=tuple(
            f.name for f in eligible if f.kind in {FieldKind.CATEGORICAL, FieldKind.BOOLEAN}
        ),
        dates=tuple(
            f.name
            for f in eligible
            if f.kind is FieldKind.DATETIME and f.temporal_summary is not None
        ),
    )


def filter_values(
    core: TabularDataCore, handle: DatasetHandle, profile: DataProfile, field: str
) -> tuple[str, ...]:
    """Return the most frequent values of one group field, for a filter selector."""
    if field not in explorer_options(profile).groups:
        raise ValueError(f"Field cannot be filtered in the explorer: {field}")
    column = quote_identifier(field)
    result = core.query(
        handle,
        f"SELECT CAST({column} AS VARCHAR) AS filter_value, COUNT(*) AS row_count "
        f"FROM dataset WHERE {column} IS NOT NULL GROUP BY filter_value "
        f"ORDER BY row_count DESC, filter_value LIMIT {_MAX_FILTER_VALUES}",
    )
    return tuple(str(row[0]) for row in result.rows)


def build_explorer_charts(
    core: TabularDataCore,
    handle: DatasetHandle,
    profile: DataProfile,
    request: ExplorerRequest,
) -> tuple[OverviewChart, ...]:
    """Build the user's chart and, for a numeric measure by group, a box plot."""
    _validate_explorer_request(explorer_options(profile), request)
    value = (
        "COUNT(*)"
        if request.aggregation == "count"
        else f"{_SQL_AGGREGATES[request.aggregation]}({quote_identifier(str(request.measure))})"
    )
    label = _AGGREGATIONS[request.aggregation]
    if request.aggregation != "count":
        label = f"{label} {request.measure}"
    conditions = _filter_conditions(request)
    charts = [_explorer_chart(core, handle, request, value, label, conditions)]
    if request.measure and request.group:
        charts.append(_box_plot(core, handle, request.measure, request.group, conditions))
    return tuple(charts)


def _validate_explorer_request(options: ExplorerOptions, request: ExplorerRequest) -> None:
    if request.aggregation not in _AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {request.aggregation}")
    if request.aggregation != "count" and request.measure is None:
        raise ValueError("A numeric measure is required for this aggregation")
    if request.measure is not None and request.measure not in options.measures:
        raise ValueError(f"Field cannot be a measure in the explorer: {request.measure}")
    if request.group is not None and request.group not in options.groups:
        raise ValueError(f"Field cannot group the explorer: {request.group}")
    if request.date_field is not None and request.date_field not in options.dates:
        raise ValueError(f"Field cannot be a time axis in the explorer: {request.date_field}")
    if request.period not in PERIODS:
        raise ValueError(f"Unsupported period: {request.period}")
    if len(request.filters) > _MAX_FILTER_FIELDS:
        raise ValueError(f"At most {_MAX_FILTER_FIELDS} fields can be filtered")
    for field, _ in request.filters:
        if field not in options.groups:
            raise ValueError(f"Field cannot be filtered in the explorer: {field}")
    if request.date_filter is not None:
        field, start, end = request.date_filter
        if field not in options.dates or start > end:
            raise ValueError("The date filter needs a date field and a start before the end")


def _filter_conditions(request: ExplorerRequest) -> list[str]:
    conditions = [
        f"CAST({quote_identifier(field)} AS VARCHAR) "
        f"IN ({', '.join(_literal(value) for value in values)})"
        for field, values in request.filters
        if values
    ]
    if request.date_filter is not None:
        field, start, end = request.date_filter
        column = quote_identifier(field)
        conditions.append(
            f"{column} >= DATE '{start.isoformat()}' "
            f"AND {column} < DATE '{(end + timedelta(days=1)).isoformat()}'"
        )
    return conditions


def _explorer_chart(
    core: TabularDataCore,
    handle: DatasetHandle,
    request: ExplorerRequest,
    value: str,
    label: str,
    conditions: list[str],
) -> OverviewChart:
    group = quote_identifier(request.group) if request.group else None
    period = (
        f"DATE_TRUNC('{request.period}', {quote_identifier(request.date_field)})"
        if request.date_field
        else None
    )
    required = [f"{column} IS NOT NULL" for column in (group, period) if column]
    if request.date_field:
        required[-1] = f"{quote_identifier(request.date_field)} IS NOT NULL"
    where = _where([*required, *conditions])
    caption = "Descriptive values from read-only queries; not Verified Insights."
    if group and period:
        groups = _top_groups(core, handle, group, where, _MAX_LINE_GROUPS)
        in_groups = f"CAST({group} AS VARCHAR) IN ({', '.join(map(_literal, groups))})"
        result = core.query(
            handle,
            f"SELECT {period} AS period_start, CAST({group} AS VARCHAR) AS group_value, "
            f"{value} AS measure_value FROM dataset {_where([*required, *conditions, in_groups])} "
            "GROUP BY period_start, group_value ORDER BY period_start, group_value",
        )
        figure = go.Figure()
        for name in groups:
            rows = [row for row in result.rows if row[1] == name]
            figure.add_trace(
                go.Scatter(
                    x=[_period_label(row[0], request.period) for row in rows],
                    y=[row[2] for row in rows],
                    mode="lines+markers",
                    name=name,
                )
            )
        figure.update_xaxes(type="category")
        title = f"{label} per {request.period} by {request.group}"
        caption = f"At most {_MAX_LINE_GROUPS} groups with the most rows. {caption}"
        chart = _chart(figure, title, caption)
        chart.figure["layout"]["showlegend"] = True
        return chart
    if period:
        result = core.query(
            handle,
            f"SELECT {period} AS period_start, {value} AS measure_value FROM dataset {where} "
            "GROUP BY period_start ORDER BY period_start",
        )
        figure = go.Figure(
            go.Scatter(
                x=[_period_label(row[0], request.period) for row in result.rows],
                y=[row[1] for row in result.rows],
                mode="lines+markers",
            )
        )
        figure.update_xaxes(type="category")
        return _chart(figure, f"{label} per {request.period}", caption)
    if group:
        result = core.query(
            handle,
            f"SELECT CAST({group} AS VARCHAR) AS group_value, {value} AS measure_value, "
            f"COUNT(*) AS row_count FROM dataset {where} GROUP BY group_value "
            f"ORDER BY row_count DESC, group_value LIMIT {_MAX_EXPLORER_GROUPS}",
        )
        figure = go.Figure(
            go.Bar(x=[str(row[0]) for row in result.rows], y=[row[1] for row in result.rows])
        )
        figure.update_xaxes(type="category")
        return _chart(
            figure,
            f"{label} by {request.group}",
            f"At most {_MAX_EXPLORER_GROUPS} groups with the most rows. {caption}",
        )
    result = core.query(handle, f"SELECT {value} AS measure_value FROM dataset {where}")
    measure = result.rows[0][0] if result.rows else None
    number = measure if isinstance(measure, int | float) and not isinstance(measure, bool) else 0
    figure = go.Figure(
        go.Indicator(
            mode="number", value=number, number={"valueformat": exact_number_format(number)}
        )
    )
    return _chart(figure, label, caption)


def _box_plot(
    core: TabularDataCore,
    handle: DatasetHandle,
    measure: str,
    group: str,
    conditions: list[str],
) -> OverviewChart:
    measure_column, group_column = quote_identifier(measure), quote_identifier(group)
    where = _where([f"{measure_column} IS NOT NULL", f"{group_column} IS NOT NULL", *conditions])
    result = core.query(
        handle,
        f"SELECT CAST({group_column} AS VARCHAR) AS group_value, MIN({measure_column}) AS low, "
        f"QUANTILE_CONT({measure_column}, 0.25) AS q1, MEDIAN({measure_column}) AS mid, "
        f"QUANTILE_CONT({measure_column}, 0.75) AS q3, MAX({measure_column}) AS high, "
        f"COUNT(*) AS row_count FROM dataset {where} GROUP BY group_value "
        f"ORDER BY row_count DESC, group_value LIMIT {_MAX_EXPLORER_GROUPS}",
    )
    rows = result.rows
    figure = go.Figure(
        go.Box(
            x=[str(row[0]) for row in rows],
            lowerfence=[row[1] for row in rows],
            q1=[row[2] for row in rows],
            median=[row[3] for row in rows],
            q3=[row[4] for row in rows],
            upperfence=[row[5] for row in rows],
        )
    )
    figure.update_xaxes(type="category")
    return _chart(
        figure,
        f"Distribution of {measure} by {group}",
        "Boxes show quartiles and whiskers the minimum and maximum of each group; "
        f"at most {_MAX_EXPLORER_GROUPS} groups with the most rows.",
    )


def _top_groups(
    core: TabularDataCore, handle: DatasetHandle, group: str, where: str, limit: int
) -> list[str]:
    result = core.query(
        handle,
        f"SELECT CAST({group} AS VARCHAR) AS group_value, COUNT(*) AS row_count FROM dataset "
        f"{where} GROUP BY group_value ORDER BY row_count DESC, group_value LIMIT {limit}",
    )
    return [str(row[0]) for row in result.rows]


def _period_label(value: object, period: str) -> str:
    text = str(value)
    return text[:4] if period == "year" else text[:7] if period == "month" else text[:10]


def _where(conditions: list[str]) -> str:
    return f"WHERE {' AND '.join(conditions)}" if conditions else ""


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = [
    "PERIODS",
    "DataOverview",
    "ExplorerOptions",
    "ExplorerRequest",
    "OverviewChart",
    "build_data_overview",
    "build_explorer_charts",
    "explorer_options",
    "filter_values",
]
