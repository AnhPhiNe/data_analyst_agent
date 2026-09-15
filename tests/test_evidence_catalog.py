"""Tests for deterministic completion of small query results."""

from __future__ import annotations

from uuid import uuid4

from tabular_analytics_agent.data import QueryColumn, QueryResult
from tabular_analytics_agent.orchestration.evidence_catalog import (
    MAX_COMPLETED_RESULT_VALUES,
    unasserted_small_result_metrics,
)


def grouped_totals(row_count: int) -> QueryResult:
    return QueryResult(
        query_id=uuid4(),
        dataset_id=uuid4(),
        working_dataset_version=1,
        sql="SELECT region, SUM(revenue) AS total FROM dataset GROUP BY region",
        columns=(
            QueryColumn(name="region", data_type="VARCHAR"),
            QueryColumn(name="total", data_type="DOUBLE"),
        ),
        rows=tuple((f"region-{index}", float(index)) for index in range(row_count)),
        row_count=row_count,
        truncated=False,
        duration_ms=1,
        group_by_columns=("region",),
    )


def test_small_result_values_the_model_left_out_are_listed() -> None:
    assert unasserted_small_result_metrics(grouped_totals(3), {"row[1].total"}) == [
        "row[0].total",
        "row[2].total",
    ]


def test_results_the_model_did_not_answer_from_are_not_completed() -> None:
    assert unasserted_small_result_metrics(grouped_totals(3), set()) == []
    assert unasserted_small_result_metrics(grouped_totals(3), {"result.row_count"}) == []
    assert unasserted_small_result_metrics(grouped_totals(3), {"row[9].total"}) == []


def test_only_complete_grouped_or_single_row_results_of_at_most_20_values_are_completed() -> None:
    asserted = {"row[0].total"}
    assert unasserted_small_result_metrics(grouped_totals(MAX_COMPLETED_RESULT_VALUES), asserted)
    too_large = grouped_totals(MAX_COMPLETED_RESULT_VALUES + 1)
    assert unasserted_small_result_metrics(too_large, asserted) == []
    truncated = grouped_totals(3).model_copy(update={"truncated": True})
    assert unasserted_small_result_metrics(truncated, asserted) == []
    listing = grouped_totals(3).model_copy(update={"group_by_columns": ()})
    assert unasserted_small_result_metrics(listing, asserted) == []
