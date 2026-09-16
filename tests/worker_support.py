"""Pickleable worker targets used by full-data timeout acceptance tests."""

from __future__ import annotations

import os
import time
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any


def slow_statistical_worker(
    _rows: tuple[tuple[Any, ...], ...],
    _columns: tuple[str, ...],
    _request_payload: dict[str, Any],
    _sender: Connection,
) -> None:
    """Mark process start, then keep computing until the parent must terminate it."""
    marker = os.environ["TABULAR_TEST_WORKER_MARKER"]
    Path(marker).write_text(str(os.getpid()), encoding="ascii")
    time.sleep(30)


def error_statistical_worker(
    _rows: tuple[tuple[Any, ...], ...],
    _columns: tuple[str, ...],
    _request_payload: dict[str, Any],
    sender: Connection,
) -> None:
    """Return a typed child error so the parent error mapping is exercised."""
    sender.send(
        {
            "ok": False,
            "error_type": "tabular_analytics_agent.statistics.errors.InsufficientSampleError",
            "error": "child has no usable values",
        }
    )
    sender.close()


def invalid_result_statistical_worker(
    _rows: tuple[tuple[Any, ...], ...],
    _columns: tuple[str, ...],
    _request_payload: dict[str, Any],
    sender: Connection,
) -> None:
    """Return a malformed success payload so invalid child output cannot publish."""
    sender.send({"ok": True, "result": {"not": "a StatisticalResult"}})
    sender.close()


def silent_statistical_worker(
    _rows: tuple[tuple[Any, ...], ...],
    _columns: tuple[str, ...],
    _request_payload: dict[str, Any],
    sender: Connection,
) -> None:
    """Exit after closing the pipe, modelling a crashed worker with no result payload."""
    sender.close()
