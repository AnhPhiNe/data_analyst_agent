"""Structured schemas the agent may request from any model provider."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.domain import GoalFamily


class GenerationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SemanticAnnotationDraft(GenerationSchema):
    field_name: str = Field(min_length=1)
    meaning: str = Field(min_length=1)
    unit: str | None = None
    role: str | None = None


class GoalInterpretation(GenerationSchema):
    goal_text: str = Field(min_length=1)
    goal_family: GoalFamily
    semantic_annotations: tuple[SemanticAnnotationDraft, ...] = ()
    clarification_question: str | None = None

    @model_validator(mode="after")
    def clarification_matches_annotations(self) -> Self:
        if self.semantic_annotations and not self.clarification_question:
            raise ValueError("semantic annotation proposals require a clarification question")
        return self


class PlanStepDraft(GenerationSchema):
    step_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_tool: Literal["read_only_sql"]
    required_fields: tuple[str, ...] = ()
    intended_output: str = Field(min_length=1)
    caveats: tuple[str, ...] = ()
    requires_approval: bool = False


class PlanDraft(GenerationSchema):
    steps: tuple[PlanStepDraft, ...]

    @model_validator(mode="after")
    def valid_steps(self) -> Self:
        if not self.steps:
            raise ValueError("a plan requires at least one step")
        if len(self.steps) > 12:
            raise ValueError("a plan cannot exceed 12 steps")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step IDs must be unique")
        return self


class ToolRequestDraft(GenerationSchema):
    tool_name: Literal["read_only_sql"]
    purpose: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    required_fields: tuple[str, ...] = ()


class InsightDraft(GenerationSchema):
    claim: str = Field(min_length=1)
    evidence_metrics: tuple[str, ...]
    caveats: tuple[str, ...] = ()


class ChartIntentDraft(GenerationSchema):
    artifact_type: str = Field(min_length=1)
    analytical_purpose: str = Field(min_length=1)
    source_result_ref: str = Field(min_length=1)
    x_field: str | None = None
    y_fields: tuple[str, ...] = ()
    color_field: str | None = None
    aggregation: str | None = None
    title: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)
    formatting_intent: dict[str, Any] = Field(default_factory=dict)
    validation_constraints: tuple[str, ...] = ()
