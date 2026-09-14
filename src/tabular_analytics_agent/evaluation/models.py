"""Contracts for deterministic golden evaluation cases."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.domain import ArtifactType, GoalFamily

NonEmptyText = Annotated[str, Field(min_length=1)]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExpectedOutcome(StrEnum):
    ANSWERED = "answered"
    PROFILE = "profile"
    REFUSED = "refused"


class ExpectedCalculation(EvaluationModel):
    metric: NonEmptyText
    expected: int | float | str | bool | None
    absolute_tolerance: Annotated[float, Field(ge=0.0)] = 0.0


class GoldenCase(EvaluationModel):
    case_id: NonEmptyText
    dataset_path: NonEmptyText
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    user_request: NonEmptyText
    goal_family: GoalFamily
    expected_outcome: ExpectedOutcome = ExpectedOutcome.ANSWERED
    required_calculations: tuple[ExpectedCalculation, ...] = ()
    allowed_fields: tuple[str, ...] = ()
    allowed_filters: tuple[str, ...] = ()
    supported_conclusions: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    valid_chart_types: tuple[ArtifactType, ...] = ()
    clarification_required: bool = False

    @model_validator(mode="after")
    def require_gradable_expectations(self) -> Self:
        if self.expected_outcome is not ExpectedOutcome.ANSWERED:
            return self
        if not self.required_calculations:
            raise ValueError("an answered golden case requires at least one calculation")
        if not self.allowed_fields:
            raise ValueError("an answered golden case requires allowed fields")
        if not self.supported_conclusions:
            raise ValueError("an answered golden case requires supported conclusions")
        return self
