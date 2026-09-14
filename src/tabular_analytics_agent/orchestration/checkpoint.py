"""Local durable checkpoint factory for synchronous Streamlit workflows."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

_LEGACY_CHECKPOINT_TYPES = (("tabular_analytics_agent.orchestration.models", "AgentRunStatus"),)


@contextmanager
def open_sqlite_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    """Open a session-safe SQLite checkpointer with restricted deserialization."""
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, check_same_thread=False)
    serializer = JsonPlusSerializer(allowed_msgpack_modules=_LEGACY_CHECKPOINT_TYPES)
    try:
        yield SqliteSaver(connection, serde=serializer)
    finally:
        connection.close()
