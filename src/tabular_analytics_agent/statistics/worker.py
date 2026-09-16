"""Terminable, serializable worker boundary for Python statistical calculations.

The worker deliberately has no dependency on ``TabularDataCore`` or DuckDB.  The parent process
reads and normalizes the complete input, then passes only ordinary serializable values and the
validated request payload across the process boundary.  This is required for Windows, where the
``spawn`` start method never inherits the parent's live database connection safely.
"""

from __future__ import annotations

from contextlib import suppress
from multiprocessing.connection import Connection
from typing import Any

import pandas as pd

from tabular_analytics_agent.statistics.engine import analyze
from tabular_analytics_agent.statistics.models import StatisticalRequest


def statistical_worker_entry(
    rows: tuple[tuple[Any, ...], ...],
    columns: tuple[str, ...],
    request_payload: dict[str, Any],
    sender: Connection,
) -> None:
    """Run deterministic Python analysis and send a JSON-serializable result or typed error."""
    try:
        request = StatisticalRequest.model_validate(request_payload)
        frame = pd.DataFrame(list(rows), columns=list(columns))
        result = analyze(frame, request)
        sender.send({"ok": True, "result": result.model_dump(mode="json")})
    except BaseException as exc:  # pragma: no cover - exercised through the parent process
        with suppress(BrokenPipeError, EOFError, OSError):
            sender.send(
                {
                    "ok": False,
                    "error_type": f"{type(exc).__module__}.{type(exc).__name__}",
                    "error": str(exc),
                }
            )
    finally:
        sender.close()
