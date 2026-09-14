"""Runtime smoke test for the Streamlit presentation adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def test_streamlit_upload_screen_starts_without_runtime_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("TABULAR_AGENT_DATA_DIR", str(tmp_path / "app-data"))
    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    application = AppTest.from_file(app_path).run(timeout=30)

    assert not application.exception
    assert [title.value for title in application.title] == [
        "Turn tabular data into evidence-backed decisions"
    ]
    assert len(application.get("file_uploader")) == 1


def test_streamlit_reports_an_invalid_resource_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("TABULAR_AGENT_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("TABULAR_AGENT_MAX_TOOL_ACTIONS", "0")
    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    application = AppTest.from_file(app_path).run(timeout=30)

    assert not application.exception
    assert "TABULAR_AGENT_MAX_TOOL_ACTIONS" in application.error[0].value
