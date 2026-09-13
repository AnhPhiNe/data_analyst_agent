"""Structured schemas the agent may request from any model provider."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.domain import GoalFamily, InsightAssertion, InsightOperator
from tabular_analytics_agent.statistics import StatisticalOperation, StatisticalRequest


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
    expected_tool: Literal["read_only_sql", "statistical_analysis"]
    statistical_operation: StatisticalOperation | None = None
    required_fields: tuple[str, ...] = ()
    intended_output: str = Field(min_length=1)
    caveats: tuple[str, ...] = ()
    requires_approval: bool = False

    @model_validator(mode="after")
    def statistical_operation_matches_tool(self) -> Self:
        if self.expected_tool == "statistical_analysis" and self.statistical_operation is None:
            raise ValueError("statistical plan steps require statistical_operation")
        if self.expected_tool != "statistical_analysis" and self.statistical_operation is not None:
            raise ValueError("only statistical plan steps may declare statistical_operation")
        return self


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
    tool_name: Literal["read_only_sql", "statistical_analysis"]
    purpose: str = Field(min_length=1)
    sql: str | None = None
    statistical_request: StatisticalRequest | None = None
    required_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def payload_matches_tool(self) -> Self:
        if self.tool_name == "read_only_sql":
            if not self.sql or self.statistical_request is not None:
                raise ValueError("read_only_sql requires only a SQL payload")
        elif self.statistical_request is None or self.sql is not None:
            raise ValueError("statistical_analysis requires only a statistical request")
        if self.statistical_request is not None and set(self.required_fields) != set(
            self.statistical_request.source_fields
        ):
            raise ValueError("statistical request fields must match required_fields")
        return self


class InsightDraft(GenerationSchema):
    plan_step_id: str = Field(min_length=1)
    assertion: InsightAssertion
    evidence_metrics: tuple[str, ...] = Field(min_length=1)
    caveats: tuple[str, ...] = ()

    @model_validator(mode="after")
    def assertion_metrics_are_selected_evidence(self) -> Self:
        assertion_metrics = {self.assertion.left_metric}
        if self.assertion.right_metric:
            assertion_metrics.add(self.assertion.right_metric)
        if self.assertion.operator in {
            InsightOperator.STATISTICALLY_SIGNIFICANT,
            InsightOperator.NOT_STATISTICALLY_SIGNIFICANT,
        }:
            if self.assertion.left_metric != "p_value":
                raise ValueError("significance assertions require p_value as left_metric")
            assertion_metrics.add("adjusted_alpha")
        if not assertion_metrics.issubset(self.evidence_metrics):
            raise ValueError("insight assertion metrics must be selected evidence")
        return self


class InsightDraftBatch(GenerationSchema):
    insights: tuple[InsightDraft, ...] = Field(max_length=12)


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
