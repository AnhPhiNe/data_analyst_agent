"""Typed output contracts for deterministic Plotly rendering."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from tabular_analytics_agent.domain import ChartIntent, VerificationResult


class ChartRenderResult(BaseModel):
    """A JSON-safe Plotly specification with its verified source binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: ChartIntent
    source_result_ref: str
    plotly_spec: dict[str, Any]
    verification: VerificationResult
