"""Deterministic Data Overview: fixed limits, exclusions, and no model calls."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from tabular_analytics_agent.application import LocalAnalysisApplication
from tabular_analytics_agent.application.overview import DataOverview, build_data_overview
from tabular_analytics_agent.data import TabularDataCore
from tabular_analytics_agent.model_gateway import FakeModelGateway


def _wide_csv(numeric_fields: int = 10, rows: int = 40) -> str:
    header = [
        "customer_email",
        "order_code",
        "constant",
        "region",
        "order_date",
        *(f"n{k}" for k in range(numeric_fields)),
    ]
    lines = [",".join(header)]
    for i in range(rows):
        numbers = []
        for k in range(numeric_fields):
            if k == 0:
                value = f"{i * 1.5:.1f}"
            elif k == 1:
                value = f"{3.0 * i + (i % 3) * 0.1:.1f}"
            elif k == 2:
                value = f"{60.0 - i * 1.25:.2f}"
            elif k == 5 and i < 5:
                value = ""
            else:
                value = f"{((i * (k + 3)) % 11) + 0.5:.1f}"
            numbers.append(value)
        lines.append(
            ",".join(
                [
                    f"user{i}@example.com",
                    f"C{i:04d}",
                    "x",
                    ["North", "South", "East"][i % 3],
                    str(date(2026, 1, 1) + timedelta(days=5 * i)),
                    *numbers,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _overview(tmp_path: Path, content: str) -> DataOverview:
    upload = tmp_path / "data.csv"
    upload.write_text(content, encoding="utf-8", newline="")
    core = TabularDataCore(tmp_path / "session")
    handle = core.ingest(upload)
    return build_data_overview(core, handle, core.profile(handle))


def _titles(overview: DataOverview, prefix: str) -> list[str]:
    return [chart.title for chart in overview.charts if chart.title.startswith(prefix)]


def test_overview_follows_fixed_limits_and_excludes_pii_identifiers_and_constants(
    tmp_path: Path,
) -> None:
    overview = _overview(tmp_path, _wide_csv())
    text = " ".join(f"{chart.title} {chart.caption}" for chart in overview.charts)

    assert overview.omitted == ()
    assert (overview.row_count, overview.column_count) == (40, 15)
    assert overview.missing_rate == 5 / (40 * 15)
    for excluded in ("customer_email", "order_code", "constant"):
        assert excluded not in text
    field_charts = _titles(overview, "Distribution of") + _titles(
        overview, "Most frequent values of"
    )
    assert len(field_charts) == 8
    assert _titles(overview, "Missing values by field") == ["Missing values by field"]
    assert "p-value" not in text and "significan" not in text
    assert _titles(overview, "Average n0 by region") == ["Average n0 by region"]
    assert _titles(overview, "Rows per month of order_date") == ["Rows per month of order_date"]


def test_correlation_heatmap_is_capped_and_offers_only_strong_pairs(tmp_path: Path) -> None:
    overview = _overview(tmp_path, _wide_csv(numeric_fields=18))
    heatmaps = [chart for chart in overview.charts if chart.title.startswith("Correlation")]
    scatters = [chart for chart in overview.charts if chart.suggested_question]

    assert len(heatmaps) == 1
    trace = heatmaps[0].figure["data"][0]
    assert trace["type"] == "heatmap"
    assert len(trace["x"]) == 15
    assert all(trace["z"][index][index] == 1.0 for index in range(15))
    assert "causation" in heatmaps[0].caption
    assert 1 <= len(scatters) <= 3
    for chart in scatters:
        coefficient = float(chart.caption.split("r = ")[1].split(";")[0])
        assert abs(coefficient) >= 0.5
        assert chart.suggested_question is not None
        assert chart.suggested_question.startswith("Is ")


def test_overview_without_numeric_fields_has_no_heatmap(tmp_path: Path) -> None:
    overview = _overview(tmp_path, "city,note\nHanoi,a\nHue,b\nHanoi,b\n")

    assert overview.omitted == ()
    assert not [chart for chart in overview.charts if chart.title.startswith("Correlation")]
    assert _titles(overview, "Most frequent values of") == [
        "Most frequent values of city",
        "Most frequent values of note",
    ]


def test_application_builds_the_overview_without_model_calls(tmp_path: Path) -> None:
    gateway = FakeModelGateway([])
    application = LocalAnalysisApplication(tmp_path / "app-data", gateway)
    workspace = application.ingest(
        application.stage_upload("sales.csv", b"region,revenue\nNorth,100\nSouth,200\nNorth,50\n")
    )

    overview = application.data_overview(workspace)

    assert _titles(overview, "Distribution of revenue") == ["Distribution of revenue"]
    assert not gateway.requests
