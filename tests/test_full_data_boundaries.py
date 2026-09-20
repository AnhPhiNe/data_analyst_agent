"""Boundary tests for full-data deadlines, cleanup, and the terminable worker seam."""

from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from typing import Any

import pytest

import tabular_analytics_agent.data.core as data_core_module
import tabular_analytics_agent.statistics.service as statistics_service
from tabular_analytics_agent.data import (
    QueryTimeoutError,
    TabularDataCore,
)
from tabular_analytics_agent.statistics import (
    StatisticalAnalysisError,
    StatisticalOperation,
    StatisticalRequest,
    StatisticalTimeoutError,
    StatisticalTool,
)
from tabular_analytics_agent.statistics.worker import statistical_worker_entry
from tests.worker_support import silent_statistical_worker


def _core_with_values(tmp_path: Path, count: int = 4) -> tuple[TabularDataCore, Any]:
    upload = tmp_path / "values.csv"
    upload.write_text(
        "value\n" + "".join(f"{index}\n" for index in range(1, count + 1)),
        encoding="utf-8",
        newline="",
    )
    core = TabularDataCore(tmp_path / "session")
    return core, core.ingest(upload)


def test_profile_timeout_interrupts_and_closes_parent_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core, handle = _core_with_values(tmp_path)
    original_profile_field = core._profile_field

    def delayed_profile_field(connection: Any, name: str, data_type: str, row_count: int) -> Any:
        time.sleep(0.1)
        return original_profile_field(connection, name, data_type, row_count)

    monkeypatch.setattr(core, "_profile_field", delayed_profile_field)
    started = time.perf_counter()
    with pytest.raises(QueryTimeoutError, match="Profiling exceeded"):
        core.profile(handle, timeout_seconds=0.01)

    # The executor is joined during cleanup, so the delayed field work cannot survive the error.
    assert time.perf_counter() - started < 2.0


def test_read_full_timeout_interrupts_a_query_without_returning_partial_rows(
    tmp_path: Path,
) -> None:
    core, handle = _core_with_values(tmp_path, count=1_000)
    sql = (
        "SELECT SUM(a.value + b.value + c.value) AS total FROM dataset AS a "
        "CROSS JOIN dataset AS b CROSS JOIN dataset AS c"
    )

    with pytest.raises(QueryTimeoutError, match="Full-data read exceeded"):
        core.read_full(handle, sql, timeout_seconds=0.2)


@pytest.mark.parametrize("operation", ["profile", "inspect_query", "read_full"])
def test_full_data_operations_reject_nonpositive_deadlines(tmp_path: Path, operation: str) -> None:
    core, handle = _core_with_values(tmp_path)

    with pytest.raises(ValueError, match="positive"):
        if operation == "profile":
            core.profile(handle, timeout_seconds=0)
        elif operation == "inspect_query":
            core.inspect_query(handle, "SELECT value FROM dataset", timeout_seconds=0)
        else:
            core.read_full(handle, "SELECT value FROM dataset", timeout_seconds=0)


def test_row_normalization_checks_the_same_end_to_end_deadline() -> None:
    assert data_core_module._normalize_rows_with_deadline(
        [(1,), (2,)], deadline=time.perf_counter() + 1.0
    ) == ((1,), (2,))
    with pytest.raises(QueryTimeoutError, match="end-to-end deadline"):
        data_core_module._normalize_rows_with_deadline([(1,)], deadline=time.perf_counter() - 1.0)


def test_spawned_worker_exit_without_payload_is_an_analysis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(statistics_service, "statistical_worker_entry", silent_statistical_worker)

    with pytest.raises(StatisticalAnalysisError, match=r"without a result|closed before"):
        statistics_service._run_statistical_worker(
            rows=((1.0,), (2.0,), (3.0,)),
            columns=("value",),
            request_payload={"operation": "descriptive", "value_fields": ["value"]},
            deadline=time.perf_counter() + 5.0,
        )


class _BrokenSender:
    def __init__(self) -> None:
        self.closed = False

    def send(self, _payload: dict[str, Any]) -> None:
        raise BrokenPipeError("receiver disconnected")

    def close(self) -> None:
        self.closed = True


def test_worker_entry_handles_a_disconnected_parent_pipe() -> None:
    sender = _BrokenSender()
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("value",),
    )

    statistical_worker_entry(
        rows=((1.0,), (2.0,), (3.0,)),
        columns=("value",),
        request_payload=request.model_dump(mode="json"),
        sender=sender,  # type: ignore[arg-type]
    )

    assert sender.closed


class _FakeProcess:
    def __init__(self, alive_states: list[bool]) -> None:
        self._alive_states = alive_states
        self.terminated = False
        self.killed = False

    def is_alive(self) -> bool:
        return self._alive_states.pop(0) if self._alive_states else False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def join(self, timeout: float | None = None) -> None:
        del timeout


def test_worker_termination_escalates_to_kill_when_terminate_is_ignored() -> None:
    process = _FakeProcess([True, True, False])

    statistics_service._terminate_worker(process)

    assert process.terminated
    assert process.killed


def test_worker_termination_reports_an_unstoppable_process() -> None:
    process = _FakeProcess([True, True, True])

    with pytest.raises(StatisticalAnalysisError, match="could not be terminated"):
        statistics_service._terminate_worker(process)


class _PipeEnd:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StartFailureProcess:
    def start(self) -> None:
        raise OSError("process creation failed")


class _StartFailureContext:
    def __init__(self) -> None:
        self.receiver = _PipeEnd()
        self.sender = _PipeEnd()

    def Pipe(self, *, duplex: bool) -> tuple[_PipeEnd, _PipeEnd]:
        assert duplex is False
        return self.receiver, self.sender

    def Process(self, **_kwargs: Any) -> _StartFailureProcess:
        return _StartFailureProcess()


def test_worker_start_failure_closes_both_parent_pipe_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _StartFailureContext()
    monkeypatch.setattr(mp, "get_context", lambda _name: context)

    with pytest.raises(StatisticalAnalysisError, match="could not be started"):
        statistics_service._run_statistical_worker(
            rows=((1.0,), (2.0,), (3.0,)),
            columns=("value",),
            request_payload={"operation": "descriptive", "value_fields": ["value"]},
            deadline=time.perf_counter() + 1.0,
        )

    assert context.receiver.closed
    assert context.sender.closed


def test_statistical_tool_rejects_expired_deadline_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core, handle = _core_with_values(tmp_path)
    profile = core.profile(handle)
    request = StatisticalRequest(
        operation=StatisticalOperation.DESCRIPTIVE,
        value_fields=("value",),
    )
    monkeypatch.setattr(statistics_service, "remaining_seconds", lambda *_args: 0.0)

    with pytest.raises(StatisticalTimeoutError, match="execution deadline"):
        StatisticalTool(core).execute(
            handle=handle,
            profile=profile,
            request=request,
            timeout_seconds=1.0,
        )
