"""Runtime smoke test for the Streamlit presentation adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit_app import legacy_evidence_message


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {"provenance_version": None},
        {"provenance_version": "v1", "sampled": None, "sample_size": 4},
        {"provenance_version": "v1", "sampled": True},
        {
            "provenance_version": "v1",
            "sampled": False,
            "dataset_row_count": 4,
            "population_row_count": 3,
            "rows_loaded": 2,
            "partial": False,
            "truncated": False,
        },
    ],
)
def test_legacy_evidence_is_marked_for_rerun(evidence: dict[str, object]) -> None:
    message = legacy_evidence_message(evidence)
    assert message is not None
    assert "rerun" in message.lower()


def test_current_sql_and_full_data_evidence_are_not_marked() -> None:
    assert legacy_evidence_message({"provenance_version": "v1"}) is None
    assert (
        legacy_evidence_message(
            {
                "provenance_version": "v1",
                "sampled": False,
                "dataset_row_count": 4,
                "population_row_count": 4,
                "rows_loaded": 4,
                "partial": False,
                "truncated": False,
            }
        )
        is None
    )


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


def test_rejected_upload_is_explained_instead_of_raised(tmp_path: Path) -> None:
    def script(root: str, data_dir: str) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, root)
        import streamlit_app

        from tabular_analytics_agent.application import LocalAnalysisApplication
        from tabular_analytics_agent.model_gateway import FakeModelGateway

        application = LocalAnalysisApplication(Path(data_dir), FakeModelGateway([]))
        streamlit_app.stage_uploaded_file(application, "bad.csv", b"region,,revenue\nNorth,1,2\n")

    root = str(Path(__file__).resolve().parents[1])
    app = AppTest.from_function(script, args=(root, str(tmp_path / "app-data"))).run(timeout=30)

    assert not app.exception
    assert "This file cannot be used" in app.error[0].value
    assert "non-empty header" in app.error[0].value
