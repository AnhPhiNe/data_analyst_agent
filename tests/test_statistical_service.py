"""Integration tests for session-scoped statistical Tool Actions."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.data import DataCoreLimits, TabularDataCore
from tabular_analytics_agent.domain import flags_inconsistent_spelling
from tabular_analytics_agent.statistics import (
    StatisticalAnalysisError,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalTimeoutError,
    StatisticalTool,
    display_group_label,
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


def write_spelling_variants(path: Path) -> Path:
    """One group column whose values differ only by letter case or surrounding spaces."""
    north = ["Bac"] * 7 + ["bac"] * 3 + [" Bac "] * 2
    south = ["Nam"] * 8 + ["NAM"] * 4
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["score", "khu_vuc"])
        for index, region in enumerate(north + south, start=1):
            writer.writerow([index, region])
    return path


def test_group_spellings_differing_only_by_case_or_spaces_form_one_group(tmp_path: Path) -> None:
    """The profiler calls these one value, so an analysis must not split them into many groups.

    Before this was handled, each spelling became its own group: the comparison either found more
    than two groups or refused the whole analysis for having too few usable values, even though
    every group had plenty.
    """
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(write_spelling_variants(tmp_path / "regions.csv"))
    profile = core.profile(handle)
    region = next(field for field in profile.fields if field.name == "khu_vuc")
    assert flags_inconsistent_spelling(region)
    assert region.unique_count == 5

    output = StatisticalTool(core).execute(
        handle=handle,
        profile=profile,
        request=StatisticalRequest(
            operation=StatisticalOperation.T_TEST,
            value_field="score",
            group_field="khu_vuc",
            group_order=("Bac", "Nam"),
        ),
    )

    sizes = {display_group_label(label): size for label, size in output.result.group_sizes.items()}
    assert sizes == {"Bac": 12, "Nam": 12}
    # The spelling the data uses most often represents the group, so the label stays readable.
    assert sorted(sizes) == ["Bac", "Nam"]
    assert output.action.inputs["spelling_normalized_fields"] == ["khu_vuc"]
    # Every row was still read; only the copy handed to the worker shows one spelling per group.
    assert output.result.rows_loaded == 24
    assert output.result.sample_size == 24


@pytest.mark.parametrize("requested", [("bac", "NAM"), (" Bac ", "Nam"), ("BAC", "nam")])
def test_group_order_matches_any_spelling_of_the_same_value(
    tmp_path: Path, requested: tuple[str, str]
) -> None:
    """group_order names a category, so it is matched the same way the rows are grouped."""
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(write_spelling_variants(tmp_path / "regions.csv"))
    profile = core.profile(handle)

    output = StatisticalTool(core).execute(
        handle=handle,
        profile=profile,
        request=StatisticalRequest(
            operation=StatisticalOperation.T_TEST,
            value_field="score",
            group_field="khu_vuc",
            group_order=requested,
        ),
    )

    assert tuple(display_group_label(label) for label in output.result.group_sizes) == (
        "Bac",
        "Nam",
    )


def test_consistent_group_values_are_left_exactly_as_they_are(tmp_path: Path) -> None:
    """A field the profiler did not flag keeps every value unchanged, including its case."""
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(write_scores(tmp_path / "scores.csv"))
    profile = core.profile(handle)
    group = next(field for field in profile.fields if field.name == "group")
    assert not flags_inconsistent_spelling(group)

    output = StatisticalTool(core).execute(
        handle=handle,
        profile=profile,
        request=StatisticalRequest(
            operation=StatisticalOperation.T_TEST,
            value_field="score",
            group_field="group",
            group_order=("A", "B"),
        ),
    )

    assert output.action.inputs["spelling_normalized_fields"] == []
    assert tuple(display_group_label(label) for label in output.result.group_sizes) == ("A", "B")


def test_chi_square_groups_both_categorical_fields_by_their_spelling(tmp_path: Path) -> None:
    """Both fields of a chi-square are keys, so both are grouped by spelling when flagged."""
    path = tmp_path / "orders.csv"
    with path.open("w", encoding="utf-8", newline="") as handle_file:
        writer = csv.writer(handle_file)
        writer.writerow(["kenh", "trang_thai"])
        for index in range(40):
            channel = ["Online", "online", "ONLINE", "Cua hang"][index % 4]
            status = ["Xong", "xong", "Huy", " Huy "][index % 4]
            writer.writerow([channel, status])

    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(path)
    profile = core.profile(handle)

    output = StatisticalTool(core).execute(
        handle=handle,
        profile=profile,
        request=StatisticalRequest(
            operation=StatisticalOperation.CHI_SQUARE,
            x_field="kenh",
            y_field="trang_thai",
        ),
    )

    assert output.action.inputs["spelling_normalized_fields"] == ["kenh", "trang_thai"]
    assert output.result.sample_size == 40
