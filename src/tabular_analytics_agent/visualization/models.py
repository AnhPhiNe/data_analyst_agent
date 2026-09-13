"""Typed output contracts for deterministic Plotly rendering."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator

from tabular_analytics_agent.domain import (
    ChartIntent,
    QueryResultReference,
    VerificationResult,
    VerificationStatus,
)


class ChartRenderResult(BaseModel):
    """A JSON-safe Plotly specification with its verified source binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: ChartIntent
    source_result_ref: QueryResultReference
    plotly_spec: dict[str, Any]
    verification: VerificationResult

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        if self.source_result_ref != self.intent.source_result_ref:
            raise ValueError("render and intent must reference the same Query Result")
        if self.verification.status is not VerificationStatus.PASSED:
            raise ValueError("a ChartRenderResult requires passed verification")
        return self
