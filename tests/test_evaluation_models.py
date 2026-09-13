"""Tests for golden evaluation case contracts."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tabular_analytics_agent.domain import ArtifactType, GoalFamily
from tabular_analytics_agent.evaluation import GoldenCase

ROOT = Path(__file__).resolve().parents[1]


def test_tiny_sales_case_is_valid_and_bound_to_fixture() -> None:
    case_path = ROOT / "tests" / "evaluation_cases" / "tiny_sales_summary.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    case = GoldenCase.model_validate(payload)
    fixture_path = ROOT / case.dataset_path

    assert fixture_path.is_file()
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == case.dataset_sha256


@pytest.mark.parametrize(
    "empty_field",
    [
        "required_calculations",
        "allowed_fields",
        "supported_conclusions",
        "valid_chart_types",
    ],
)
def test_golden_case_requires_gradable_expectations(empty_field: str) -> None:
    payload: dict[str, object] = {
        "case_id": "invalid-case",
        "dataset_path": "tests/fixtures/tiny_sales.csv",
        "dataset_sha256": "a" * 64,
        "user_request": "Summarize revenue",
        "goal_family": GoalFamily.SUMMARY,
        "required_calculations": [{"metric": "total", "expected": 550}],
        "allowed_fields": ["revenue"],
        "supported_conclusions": ["Revenue totals 550."],
        "valid_chart_types": [ArtifactType.TABLE],
        "clarification_required": False,
    }
    payload[empty_field] = []

    with pytest.raises(ValidationError):
        GoldenCase.model_validate(payload)
