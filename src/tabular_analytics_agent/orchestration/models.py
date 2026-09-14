"""Validated inputs and JSON-safe state for the single-agent graph."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tabular_analytics_agent.data import DatasetHandle
from tabular_analytics_agent.domain import DataProfile
from tabular_analytics_agent.model_gateway.schemas import SemanticAnnotationDraft
from tabular_analytics_agent.statistics import StatisticalRequest


class AgentRunStatus(StrEnum):
    INTERPRETING = "interpreting"
    AWAITING_SEMANTIC_REVIEW = "awaiting_semantic_review"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    REQUESTING_TOOL = "requesting_tool"
    RETRYING_TOOL = "retrying_tool"
    SYNTHESIZING = "synthesizing"
    PROPOSING_ARTIFACT = "proposing_artifact"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class OrchestrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AgentRunRequest(OrchestrationModel):
    session_id: str = Field(min_length=1)
    user_request: str = Field(min_length=1)
    dataset_handle: DatasetHandle
    data_profile: DataProfile

    @model_validator(mode="after")
    def dataset_context_matches(self) -> Self:
        if self.dataset_handle.dataset != self.data_profile.dataset:
            raise ValueError("DatasetHandle and DataProfile must describe the same dataset")
        return self


class BoundToolRequest(OrchestrationModel):
    """Tool request enriched with policy and approval data by the application."""

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
            raise ValueError("statistical request fields must match bound required_fields")
        return self


class ApprovalDecision(OrchestrationModel):
    approved: bool
    reason: str | None = None
    revision_request: str | None = None
    annotations: tuple[SemanticAnnotationDraft, ...] | None = None

    @model_validator(mode="after")
    def decision_is_consistent(self) -> Self:
        if self.approved and self.revision_request:
            raise ValueError("an approved decision cannot request a revision")
        return self


class AgentState(TypedDict, total=False):
    session_id: str
    run_started_at: str
    active_run_seconds: float
    active_segment_started_at: str
    user_request: str
    dataset_handle: dict[str, Any]
    data_profile: dict[str, Any]
    answered_from_profile: bool
    goal: dict[str, Any]
    clarification_question: str
    proposed_annotations: list[dict[str, Any]]
    semantic_annotations: list[dict[str, Any]]
    plan: dict[str, Any]
    current_step_index: int
    tool_request: dict[str, Any]
    query_result: dict[str, Any]
    query_results: list[dict[str, Any]]
    statistical_result: dict[str, Any]
    statistical_results: list[dict[str, Any]]
    tool_actions: list[dict[str, Any]]
    attempted_action_signatures: list[str]
    tool_repair_count: int
    model_traces: list[dict[str, Any]]
    verified_insights: list[dict[str, Any]]
    unsupported_claims: list[dict[str, Any]]
    chart_renders: list[dict[str, Any]]
    artifact_error: str
    status: str
    error: str
    error_kind: str
