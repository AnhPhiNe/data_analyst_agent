"""Contracts for deterministic golden evaluation cases."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.domain import ArtifactType, GoalFamily

NonEmptyText = Annotated[str, Field(min_length=1)]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExpectedOutcome(StrEnum):
    ANSWERED = "answered"
    PROFILE = "profile"
    CLARIFICATION = "clarification"
    REFUSED = "refused"
    FAILED = "failed"


Scalar = str | int | float | bool | None


class ExpectedGroup(EvaluationModel):
    """The group identity that selects one row from a grouped output.

    A field of None matches any GROUP BY output column. Use it for a group on a derived
    expression, such as a normalized label, whose output name the model chooses.
    """

    field: NonEmptyText | None = None
    value: Scalar


class ExpectedCalculation(EvaluationModel):
    metric: NonEmptyText
    expected: Scalar
    absolute_tolerance: Annotated[float, Field(ge=0.0)] = 0.0
    aliases: tuple[NonEmptyText, ...] = ()
    group: ExpectedGroup | None = None
    # Further identities of a row grouped by several columns; every identity must match.
    groups: tuple[ExpectedGroup, ...] = ()


class ExpectedProfileFact(EvaluationModel):
    fact: Literal[
        "field_names",
        "row_count",
        "duplicate_row_count",
        "field_kind",
        "missing_count",
        "missing_rate",
        "unique_count",
        "mean",
        "standard_deviation",
        "minimum",
        "first_quartile",
        "median",
        "third_quartile",
        "maximum",
    ]
    expected: Scalar | tuple[Scalar, ...]
    field: NonEmptyText | None = None
    absolute_tolerance: Annotated[float, Field(ge=0.0)] = 0.0

    @model_validator(mode="after")
    def field_scope_matches_fact(self) -> Self:
        dataset_facts = {"field_names", "row_count", "duplicate_row_count"}
        if self.fact in dataset_facts and self.field is not None:
            raise ValueError(f"{self.fact} is a dataset-level profile fact")
        if self.fact not in dataset_facts and self.field is None:
            raise ValueError(f"{self.fact} requires a field")
        return self


class GoldenCase(EvaluationModel):
    case_id: NonEmptyText
    dataset_path: NonEmptyText
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    user_request: NonEmptyText
    goal_family: GoalFamily
    expected_outcome: ExpectedOutcome = ExpectedOutcome.ANSWERED
    acceptable_outcomes: tuple[ExpectedOutcome, ...] = ()
    required_calculations: tuple[ExpectedCalculation, ...] = ()
    required_profile_facts: tuple[ExpectedProfileFact, ...] = ()
    allowed_fields: tuple[str, ...] = ()
    allowed_filters: tuple[str, ...] = ()
    supported_conclusions: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    valid_chart_types: tuple[ArtifactType, ...] = ()
    # Other complete calculation sets that are equally valid, such as a t-test's group means
    # or a Mann-Whitney test's group medians; grading uses the set the run matched best.
    alternative_calculations: tuple[tuple[ExpectedCalculation, ...], ...] = ()

    @property
    def accepted_outcomes(self) -> frozenset[ExpectedOutcome]:
        return frozenset((self.expected_outcome, *self.acceptable_outcomes))

    @model_validator(mode="after")
    def require_gradable_expectations(self) -> Self:
        if any(not calculations for calculations in self.alternative_calculations):
            raise ValueError("an alternative calculation set cannot be empty")
        if self.expected_outcome is not ExpectedOutcome.ANSWERED:
            if self.expected_outcome is ExpectedOutcome.PROFILE and not self.required_profile_facts:
                raise ValueError("a profile golden case requires expected profile facts")
            return self
        if not self.required_calculations:
            raise ValueError("an answered golden case requires at least one calculation")
        if not self.allowed_fields:
            raise ValueError("an answered golden case requires allowed fields")
        if not self.supported_conclusions:
            raise ValueError("an answered golden case requires supported conclusions")
        return self
