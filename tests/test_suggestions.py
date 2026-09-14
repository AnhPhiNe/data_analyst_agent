"""Tests for deterministic Analytical Goal suggestions."""

from __future__ import annotations

from pathlib import Path

from tabular_analytics_agent.application.suggestions import suggest_goals
from tabular_analytics_agent.data import TabularDataCore
from tabular_analytics_agent.domain import DataProfile


def profile_for(tmp_path: Path, name: str, content: str) -> DataProfile:
    upload = tmp_path / name
    upload.write_text(content, encoding="utf-8", newline="")
    core = TabularDataCore(tmp_path / f"{name}-session")
    return core.profile(core.ingest(upload))


def test_suggestions_cover_comparison_relationship_trend_and_quality(tmp_path: Path) -> None:
    profile = profile_for(
        tmp_path,
        "sales.csv",
        "order_date,region,revenue,units\n"
        "2026-01-01,North,100,2\n"
        "2026-01-02,South,150,3\n"
        "2026-01-03,North,,4\n"
        "2026-01-04,South,90,1\n",
    )

    suggestions = suggest_goals(profile)

    assert suggestions == (
        "What is the average revenue for each region?",
        "Is revenue correlated with units?",
        "How does the total revenue change over order_date?",
        "Is the average revenue significantly different between the region groups?",
        "Which columns have missing values, and how many duplicate rows are there?",
    )


def test_suggestions_skip_pii_and_still_offer_at_least_three_goals(tmp_path: Path) -> None:
    profile = profile_for(
        tmp_path,
        "contacts.csv",
        "email,city\nan@example.com,Hanoi\nbinh@example.com,Hue\nchi@example.com,Hanoi\n",
    )

    suggestions = suggest_goals(profile)

    assert 3 <= len(suggestions) <= 5
    assert "email" in profile.pii_candidates
    assert not any("email" in suggestion for suggestion in suggestions)
    assert "How many rows are there for each city?" in suggestions
