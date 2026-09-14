"""Structured schemas the agent may request from any model provider."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.domain import GoalFamily, InsightAssertion, InsightOperator
from tabular_analytics_agent.statistics import AlternativeHypothesis, StatisticalOperation

_SIGNIFICANCE_OPERATORS = {
    InsightOperator.STATISTICALLY_SIGNIFICANT,
    InsightOperator.NOT_STATISTICALLY_SIGNIFICANT,
}


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
    semantic_annotations: tuple[SemanticAnnotationDraft, ...] = Field(
        default=(),
        description=(
            "Blocking semantic hypotheses only; return an empty list by default and never infer "
            "business definitions or units from names"
        ),
    )
    clarification_question: str | None = Field(
        default=None,
        description=("Required only when semantic_annotations is non-empty; otherwise return null"),
    )

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


class SQLToolRequestDraft(GenerationSchema):
    sql: str = Field(
        min_length=1,
        description="One read-only SQL query for the already-approved plan step",
    )


class StatisticalToolRequestDraft(GenerationSchema):
    value_fields: tuple[str, ...] = ()
    value_field: str | None = None
    group_field: str | None = None
    x_field: str | None = None
    y_field: str | None = None
    group_order: tuple[str | int | float | bool, ...] = ()
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    alternative: AlternativeHypothesis = AlternativeHypothesis.TWO_SIDED
    positive_class: str | int | float | bool | None = None


class InsightAssertionDraft(GenerationSchema):
    """Loosely validated assertion so one malformed draft cannot discard the whole batch."""

    operator: InsightOperator
    left_metric: str = Field(
        min_length=1,
        description="Exact metric identifier from the evidence catalog",
    )
    right_metric: str | None = Field(
        default=None,
        description=(
            "Exact metric identifier; required for equals, greater_than, and less_than, and null "
            "for every other operator"
        ),
    )


class InsightDraft(GenerationSchema):
    plan_step_id: str = Field(min_length=1)
    assertion: InsightAssertionDraft
    evidence_metrics: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "Every metric used by the assertion; significance assertions use p_value as "
            "left_metric and must also include adjusted_alpha"
        ),
    )
    caveats: tuple[str, ...] = ()

    def validated_assertion(self) -> InsightAssertion:
        """Return the domain assertion, or raise ValueError describing the broken contract."""
        assertion = InsightAssertion(
            operator=self.assertion.operator,
            left_metric=self.assertion.left_metric,
            right_metric=self.assertion.right_metric or None,
        )
        assertion_metrics = {assertion.left_metric}
        if assertion.right_metric:
            assertion_metrics.add(assertion.right_metric)
        if assertion.operator in _SIGNIFICANCE_OPERATORS:
            if assertion.left_metric != "p_value":
                raise ValueError("significance assertions require p_value as left_metric")
            assertion_metrics.add("adjusted_alpha")
        if not assertion_metrics.issubset(self.evidence_metrics):
            raise ValueError("insight assertion metrics must be selected evidence")
        return assertion


class InsightDraftBatch(GenerationSchema):
    insights: tuple[InsightDraft, ...] = Field(max_length=12)


class ChartIntentDraft(GenerationSchema):
    artifact_type: str = Field(
        min_length=1,
        description="One of: kpi, table, histogram, bar, line, scatter",
    )
    analytical_purpose: str = Field(min_length=1)
    source_result_ref: str = Field(min_length=1)
    x_field: str | None = Field(
        default=None,
        description="Exact result column name for the x axis, or null; never a placeholder",
    )
    y_fields: tuple[str, ...] = Field(
        default=(),
        description="Exact result column names for the plotted values; never placeholders",
    )
    color_field: str | None = Field(
        default=None,
        description="Exact result column name used for grouping, or null",
    )
    aggregation: str | None = None
    title: str = Field(min_length=1)
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Display labels keyed by exact result column names",
    )
    formatting_intent: dict[str, Any] = Field(default_factory=dict)
    validation_constraints: tuple[str, ...] = ()
