"""Integration tests for session-scoped statistical Tool Actions."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from tabular_analytics_agent.data import DataCoreLimits, TabularDataCore
from tabular_analytics_agent.statistics import (
    StatisticalAnalysisError,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalTool,
)


def write_scores(path: Path) -> Path:
    rows = ["score,group,label"]
    rows.extend(f"{index},{'A' if index <= 6 else 'B'},row-{index}" for index in range(1, 13))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="")
    return path


def test_statistical_tool_uses_seeded_bounded_sample_and_records_provenance(
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
    assert first.sampled is True
    assert first.input_query.rows == second.input_query.rows
    assert first.input_query.row_count == 5
    assert "SAMPLE RESERVOIR (5 ROWS) REPEATABLE (7)" in first.input_query.sql
    assert first.action.status.value == "succeeded"
    assert first.action.output_ref == f"statistical-result:{first.result.result_id}"
    assert first.action.inputs["request"] == request.reproducible_parameters()
    assert first.action.inputs["sampling"] == {
        "method": "reservoir",
        "maximum_rows": 5,
        "random_seed": 7,
        "sampled": True,
    }
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
