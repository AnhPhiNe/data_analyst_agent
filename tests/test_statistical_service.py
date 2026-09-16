"""Integration tests for session-scoped statistical Tool Actions."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.data import DataCoreLimits, TabularDataCore
from tabular_analytics_agent.statistics import (
    StatisticalAnalysisError,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalTimeoutError,
    StatisticalTool,
)
from tabular_analytics_agent.statistics.service import _run_statistical_worker


def write_scores(path: Path) -> Path:
    rows = ["score,group,label"]
    rows.extend(f"{index},{'A' if index <= 6 else 'B'},row-{index}" for index in range(1, 13))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="")
    return path


def write_large_scores(path: Path, count: int = 10_001) -> Path:
    rows = ["score,group,label"]
    rows.extend(f"{index},{'A' if index % 2 else 'B'},row-{index}" for index in range(1, count + 1))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="")
    return path


def test_statistical_tool_reads_full_input_and_keeps_display_preview_bounded(
    tmp_path: Path,
) -> None:
    core = TabularDataCore(
        tmp_path / "session",
        limits=DataCoreLimits(max_query_rows=5),
    )
    handle = core.ingest(write_scores(tmp_path / "scores.csv"))
    profile = core.profile(handle)
    tool = StatisticalTool(core)
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("score",),
        random_seed=7,
    )

    first = tool.execute(handle=handle, profile=profile, request=request)
    second = tool.execute(handle=handle, profile=profile, request=request)

    assert core.limits.max_query_rows == 5
    assert first.sampled is False
    assert first.result.sampled is False
    assert first.result.dataset_row_count == 12
    assert first.result.population_row_count == 12
    assert first.result.rows_loaded == 12
    assert first.result.partial is False
    assert first.result.truncated is False
    assert first.result.sampling_method is None
    assert first.result.sampling_seed is None
    assert first.input_query.rows == second.input_query.rows
    assert first.input_query.row_count == 5
    assert first.input_query.truncated is True
    assert "SAMPLE" not in first.input_query.sql.upper()
    assert first.action.status.value == "succeeded"
    assert first.action.output_ref == f"statistical-result:{first.result.result_id}"
    assert first.action.inputs["request"] == request.reproducible_parameters()
    assert first.action.inputs["dataset_row_count"] == 12
    assert first.action.inputs["population_row_count"] == 12
    assert first.action.inputs["rows_loaded"] == 12
    assert first.action.inputs["sampled"] is False
    assert first.action.inputs["sampling_method"] is None
    assert first.action.inputs["sampling_seed"] is None
    assert first.action.inputs["partial"] is False
    assert first.action.inputs["truncated"] is False
    assert "sampling" not in first.action.inputs
    assert first.action.verification_results[0].status.value == "passed"
    assert all(check.passed for check in first.action.verification_results[0].checks)


def test_statistical_tool_rejects_profile_mismatch_and_incompatible_field_type(
    tmp_path: Path,
) -> None:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(write_scores(tmp_path / "scores.csv"))
    profile = core.profile(handle)
    tool = StatisticalTool(core)

    with pytest.raises(StatisticalAnalysisError, match="same Source Dataset"):
        tool.execute(
            handle=handle,
            profile=profile.model_copy(
                update={"dataset": profile.dataset.model_copy(update={"dataset_id": uuid4()})}
            ),
            request=StatisticalRequest(
                operation=StatisticalOperation.DESCRIPTIVE,
                value_fields=("score",),
            ),
        )

    with pytest.raises(StatisticalAnalysisError, match="requires numeric fields: label"):
        tool.execute(
            handle=handle,
            profile=profile,
            request=StatisticalRequest(
                operation=StatisticalOperation.DESCRIPTIVE,
                value_fields=("label",),
            ),
        )


def test_statistical_tool_uses_actual_10001_row_values_and_counts(tmp_path: Path) -> None:
    core = TabularDataCore(
        tmp_path / "session",
        limits=DataCoreLimits(max_query_rows=5),
    )
    handle = core.ingest(write_large_scores(tmp_path / "scores.csv"))
    profile = core.profile(handle)
    output = StatisticalTool(core).execute(
        handle=handle,
        profile=profile,
        request=StatisticalRequest(
            operation=StatisticalOperation.DESCRIPTIVE,
            value_fields=("score",),
        ),
    )

    estimates = {estimate.metric: estimate.value for estimate in output.result.estimates}
    assert estimates["score.count"] == 10_001
    assert estimates["score.mean"] == pytest.approx(5_001.0)
    assert output.result.sample_size == 10_001
    assert output.result.dataset_row_count == 10_001
    assert output.result.population_row_count == 10_001
    assert output.result.rows_loaded == 10_001
    assert output.input_query.row_count == 5
    assert output.input_query.truncated is True


def test_full_read_tracks_supported_sql_filter_scope_without_new_statistics_filter_api(
    tmp_path: Path,
) -> None:
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(write_scores(tmp_path / "scores.csv"))
    full_read = core.read_full(handle, 'SELECT "score" FROM "dataset" WHERE "score" <= 3')

    assert full_read.dataset_row_count == 12
    assert full_read.population_row_count == 3
    assert full_read.row_count == 3
    assert full_read.rows == ((1,), (2,), (3,))
    assert full_read.inspection.filters == ('"score" <= 3',)


def test_statistical_worker_is_terminated_when_full_deadline_expires() -> None:
    rows = tuple((float(index),) for index in range(2_000_000))
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("score",),
    )

    with pytest.raises(StatisticalTimeoutError, match=r"worker terminated|execution deadline"):
        _run_statistical_worker(
            rows=rows,
            columns=("score",),
            request_payload=request.model_dump(mode="json"),
            deadline=time.perf_counter() + 0.01,
        )
