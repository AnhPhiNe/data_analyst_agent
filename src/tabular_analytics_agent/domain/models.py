"""Validated domain records shared across application modules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    """Base record with immutable values and strict input validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SessionStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    COMPLETED = "completed"


class FieldKind(StrEnum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    SHORT_TEXT = "short_text"
    UNKNOWN = "unknown"


class GoalFamily(StrEnum):
    DATA_QUALITY = "data_quality"
    SUMMARY = "summary"
    COMPARISON = "comparison"
    TREND = "trend"
    RELATIONSHIP = "relationship"
    DRIVER_EXPLORATION = "driver_exploration"


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class InsightStatus(StrEnum):
    VERIFIED = "verified"
    STALE = "stale"
    UNSUPPORTED = "unsupported"


class InsightOperator(StrEnum):
    REPORTS = "reports"
    EQUALS = "equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    STATISTICALLY_SIGNIFICANT = "statistically_significant"
    NOT_STATISTICALLY_SIGNIFICANT = "not_statistically_significant"


class ArtifactStatus(StrEnum):
    CANDIDATE = "candidate"
    PINNED = "pinned"


class ArtifactType(StrEnum):
    KPI = "kpi"
    TABLE = "table"
    HISTOGRAM = "histogram"
    BOX_PLOT = "box_plot"
    BAR = "bar"
    LINE = "line"
    SCATTER = "scatter"
    HEATMAP = "heatmap"
    STACKED_BAR = "stacked_bar"
    MISSING_VALUES = "missing_values"


class DatasetIdentity(DomainModel):
    dataset_id: UUID
    original_filename: NonEmptyText
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: PositiveInt


class NumericSummary(DomainModel):
    count: NonNegativeInt
    mean: float | None = None
    standard_deviation: float | None = None
    minimum: float | None = None
    first_quartile: float | None = None
    median: float | None = None
    third_quartile: float | None = None
    maximum: float | None = None
    outlier_count: NonNegativeInt = 0
    outlier_method: NonEmptyText = "Tukey IQR (1.5 * IQR)"


class CategoryFrequency(DomainModel):
    value: str | int | float | bool | None
    count: PositiveInt
    rate: Probability


class TemporalSummary(DomainModel):
    earliest: NonEmptyText
    latest: NonEmptyText


IDENTIFIER_LIKE_WARNING = "identifier-like unique field"
INCONSISTENT_SPELLING_WARNING = "inconsistent spelling (values differ only by case or spaces)"
POSSIBLE_PII_WARNING = "possible PII"


class FieldProfile(DomainModel):
    name: NonEmptyText
    kind: FieldKind
    missing_count: NonNegativeInt
    missing_rate: Probability
    unique_count: NonNegativeInt
    inference_confidence: Probability = 1.0
    type_ambiguous: bool = False
    warnings: tuple[str, ...] = ()
    numeric_summary: NumericSummary | None = None
    top_values: tuple[CategoryFrequency, ...] = ()
    temporal_summary: TemporalSummary | None = None

    @model_validator(mode="after")
    def summaries_match_kind(self) -> Self:
        if self.numeric_summary and self.kind is not FieldKind.NUMERIC:
            raise ValueError("numeric_summary requires a numeric field")
        if self.temporal_summary and self.kind is not FieldKind.DATETIME:
            raise ValueError("temporal_summary requires a datetime field")
        return self


class DataProfile(DomainModel):
    profile_id: UUID
    dataset: DatasetIdentity
    row_count: NonNegativeInt
    fields: tuple[FieldProfile, ...]
    duplicate_row_count: NonNegativeInt = 0
    pii_candidates: tuple[str, ...] = ()
    profile_version: NonEmptyText
    created_at: datetime

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        if self.duplicate_row_count > self.row_count:
            raise ValueError("duplicate_row_count cannot exceed row_count")
        for field in self.fields:
            if field.missing_count > self.row_count:
                raise ValueError(f"missing_count for {field.name!r} cannot exceed row_count")
            if field.unique_count > self.row_count:
                raise ValueError(f"unique_count for {field.name!r} cannot exceed row_count")
        if not self.created_at.tzinfo:
            raise ValueError("created_at must be timezone-aware")
        return self


class SemanticAnnotation(DomainModel):
    field_name: NonEmptyText
    meaning: NonEmptyText
    unit: str | None = None
    role: str | None = None
    confirmed_by_user: bool

    @model_validator(mode="after")
    def require_confirmation(self) -> Self:
        if not self.confirmed_by_user:
            raise ValueError("semantic annotations must be confirmed by the user")
        return self


class AnalyticalGoal(DomainModel):
    text: NonEmptyText
    family: GoalFamily


class PlanStep(DomainModel):
    step_id: NonEmptyText
    description: NonEmptyText
    expected_tool: NonEmptyText
    statistical_operation: str | None = None
    required_fields: tuple[str, ...] = ()
    intended_output: NonEmptyText
    caveats: tuple[str, ...] = ()
    requires_approval: bool = False

    @model_validator(mode="after")
    def statistical_operation_matches_tool(self) -> Self:
        if self.expected_tool == "statistical_analysis" and not self.statistical_operation:
            raise ValueError("statistical plan steps require statistical_operation")
        if self.expected_tool != "statistical_analysis" and self.statistical_operation:
            raise ValueError("only statistical plan steps may declare statistical_operation")
        return self


class ExecutionBudget(DomainModel):
    max_tool_actions: PositiveInt = 12
    max_repairs_per_action: NonNegativeInt = 2
    model_call_timeout_seconds: PositiveInt = 30
    tool_timeout_seconds: PositiveInt = 30
    run_timeout_seconds: PositiveInt = 300


class AnalysisPlan(DomainModel):
    plan_id: UUID
    goal: AnalyticalGoal
    steps: tuple[PlanStep, ...]
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    status: PlanStatus = PlanStatus.PROPOSED
    version: PositiveInt = 1

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        if not self.steps:
            raise ValueError("an analysis plan requires at least one step")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step IDs must be unique")
        if len(self.steps) > self.budget.max_tool_actions:
            raise ValueError("plan steps exceed the tool-action budget")
        return self


class VerificationCheck(DomainModel):
    name: NonEmptyText
    passed: bool
    message: NonEmptyText


class VerificationResult(DomainModel):
    status: VerificationStatus
    checks: tuple[VerificationCheck, ...]

    @model_validator(mode="after")
    def status_matches_checks(self) -> Self:
        if not self.checks:
            raise ValueError("verification requires at least one check")
        all_passed = all(check.passed for check in self.checks)
        if (self.status is VerificationStatus.PASSED) != all_passed:
            raise ValueError("verification status must match its checks")
        return self


class ToolAction(DomainModel):
    action_id: UUID
    tool_name: NonEmptyText
    schema_version: NonEmptyText
    working_dataset_version: PositiveInt
    inputs: dict[str, Any]
    status: ActionStatus = ActionStatus.PENDING
    output_ref: str | None = None
    error: str | None = None
    retry_count: NonNegativeInt = 0
    duration_ms: NonNegativeInt | None = None
    verification_results: tuple[VerificationResult, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status is ActionStatus.SUCCEEDED and not self.output_ref:
            raise ValueError("a succeeded action requires output_ref")
        if self.status is ActionStatus.SUCCEEDED and not self.verification_results:
            raise ValueError("a succeeded action requires verification results")
        if self.status is ActionStatus.FAILED and not self.error:
            raise ValueError("a failed action requires an error")
        if self.status is not ActionStatus.FAILED and self.error:
            raise ValueError("only a failed action may contain an error")
        return self


class EvidenceValue(DomainModel):
    metric: NonEmptyText
    value: str | int | float | bool | None
    unit: str | None = None


class FilterScope(DomainModel):
    """One SQL predicate together with the SELECT scope that owns it."""

    scope: NonEmptyText
    clause: Literal["where", "having"]
    expression: NonEmptyText


class InsightAssertion(DomainModel):
    operator: InsightOperator
    left_metric: NonEmptyText
    right_metric: str | None = None

    @model_validator(mode="after")
    def operands_match_operator(self) -> Self:
        comparison_operators = {
            InsightOperator.EQUALS,
            InsightOperator.GREATER_THAN,
            InsightOperator.LESS_THAN,
        }
        if self.operator in comparison_operators and not self.right_metric:
            raise ValueError("comparison insight assertions require right_metric")
        if self.operator not in comparison_operators and self.right_metric:
            raise ValueError("unary insight assertions cannot include right_metric")
        if self.right_metric == self.left_metric:
            raise ValueError("insight assertion metrics must be distinct")
        return self


class EvidenceTrail(DomainModel):
    trail_id: UUID
    dataset_id: UUID
    working_dataset_version: PositiveInt
    semantic_annotation_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_fields: tuple[str, ...]
    filters: tuple[str, ...] = ()
    filter_scopes: tuple[FilterScope, ...] = ()
    dataset_count_scope: Literal["whole_dataset"] | None = None
    source_row_count: NonNegativeInt
    result_row_count: NonNegativeInt
    missing_data_handling: NonEmptyText
    tool_action_ids: tuple[UUID, ...]
    tool_parameters: tuple[dict[str, Any], ...]
    values: tuple[EvidenceValue, ...]
    caveats: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_reproducible_evidence(self) -> Self:
        if not self.source_fields and self.dataset_count_scope != "whole_dataset":
            raise ValueError(
                "an evidence trail requires source fields or whole-dataset count evidence"
            )
        if not self.tool_action_ids:
            raise ValueError("an evidence trail requires a tool action")
        if len(self.tool_parameters) != len(self.tool_action_ids) or any(
            not parameters for parameters in self.tool_parameters
        ):
            raise ValueError("every evidence tool action requires exact parameters")
        if not self.values:
            raise ValueError("an evidence trail requires computed values")
        return self


class VerifiedInsight(DomainModel):
    insight_id: UUID
    status: InsightStatus = InsightStatus.VERIFIED
    claim: NonEmptyText
    evidence: EvidenceTrail
    verification: VerificationResult
    artifact_id: UUID | None = None
    # Older persisted insights predate assertion storage; new publications always supply it.
    assertion: InsightAssertion | None = None

    @model_validator(mode="after")
    def require_passed_verification(self) -> Self:
        if self.status is not InsightStatus.VERIFIED:
            raise ValueError("a VerifiedInsight must have verified status")
        if self.verification.status is not VerificationStatus.PASSED:
            raise ValueError("a VerifiedInsight requires passed verification")
        return self


class UnsupportedClaim(DomainModel):
    claim_id: UUID
    status: InsightStatus = InsightStatus.UNSUPPORTED
    claim: NonEmptyText
    reason: NonEmptyText
    verification: VerificationResult
    evidence: EvidenceTrail | None = None

    @model_validator(mode="after")
    def require_failed_verification(self) -> Self:
        if self.status is not InsightStatus.UNSUPPORTED:
            raise ValueError("an UnsupportedClaim must have unsupported status")
        if self.verification.status is not VerificationStatus.FAILED:
            raise ValueError("an UnsupportedClaim requires failed verification")
        return self


class StaleInsight(DomainModel):
    insight_id: UUID
    status: InsightStatus = InsightStatus.STALE
    claim: NonEmptyText
    evidence: EvidenceTrail
    reason: NonEmptyText

    @model_validator(mode="after")
    def require_stale_status(self) -> Self:
        if self.status is not InsightStatus.STALE:
            raise ValueError("a StaleInsight must have stale status")
        return self


class QueryResultReference(DomainModel):
    """Stable identity for the exact query result visualized by an artifact."""

    query_id: UUID
    dataset_id: UUID
    working_dataset_version: PositiveInt
    semantic_annotation_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @property
    def canonical_ref(self) -> str:
        """Return the URI-shaped reference used by Tool Action output records."""
        return f"query-result:{self.query_id}"


def fingerprint_semantic_annotations(annotations: Iterable[SemanticAnnotation]) -> str:
    """Hash confirmed semantic context independent of annotation input order."""
    return _annotation_hash(annotations, case_sensitive=False)


def semantic_fingerprint_matches(
    fingerprint: str, annotations: Iterable[SemanticAnnotation]
) -> bool:
    """Compare a stored fingerprint, also accepting the case-sensitive order that charts stored
    before insights and charts shared one fingerprint."""
    annotations = tuple(annotations)
    return fingerprint in {
        _annotation_hash(annotations, case_sensitive=False),
        _annotation_hash(annotations, case_sensitive=True),
    }


def _annotation_hash(annotations: Iterable[SemanticAnnotation], *, case_sensitive: bool) -> str:
    canonical = sorted(
        (annotation.model_dump(mode="json") for annotation in annotations),
        key=lambda item: (
            str(item["field_name"]) if case_sensitive else str(item["field_name"]).casefold(),
            json.dumps(item, sort_keys=True),
        ),
    )
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ChartIntent(DomainModel):
    artifact_type: ArtifactType
    analytical_purpose: NonEmptyText
    source_result_ref: QueryResultReference
    x_field: str | None = None
    y_fields: tuple[str, ...] = ()
    color_field: str | None = None
    aggregation: str | None = None
    title: NonEmptyText
    labels: dict[str, str] = Field(default_factory=dict)
    formatting_intent: dict[str, Any] = Field(default_factory=dict)
    validation_constraints: tuple[NonEmptyText, ...] = ()


class AnalyticalArtifact(DomainModel):
    artifact_id: UUID
    session_id: UUID
    status: ArtifactStatus = ArtifactStatus.CANDIDATE
    intent: ChartIntent
    source_result_ref: QueryResultReference
    insight_ids: tuple[UUID, ...] = ()
    render_spec_ref: NonEmptyText
    verification: VerificationResult
    created_at: datetime
    updated_at: datetime
    version: PositiveInt = 1

    @model_validator(mode="after")
    def validate_publication(self) -> Self:
        if not self.created_at.tzinfo or not self.updated_at.tzinfo:
            raise ValueError("artifact timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.verification.status is not VerificationStatus.PASSED:
            raise ValueError("an AnalyticalArtifact requires passed verification")
        if self.source_result_ref != self.intent.source_result_ref:
            raise ValueError("artifact and intent must reference the same Query Result")
        return self


class AnalysisSession(DomainModel):
    session_id: UUID
    status: SessionStatus
    source_dataset_id: UUID | None = None
    working_dataset_version: NonNegativeInt = 0
    data_profile_id: UUID | None = None
    semantic_annotations: tuple[SemanticAnnotation, ...] = ()
    active_goal: AnalyticalGoal | None = None
    active_plan_id: UUID | None = None
    graph_checkpoint_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_session(self) -> Self:
        if not self.created_at.tzinfo or not self.updated_at.tzinfo:
            raise ValueError("session timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        fields = [annotation.field_name for annotation in self.semantic_annotations]
        if len(fields) != len(set(fields)):
            raise ValueError("a field may have only one active semantic annotation")
        if self.working_dataset_version and not self.source_dataset_id:
            raise ValueError("a Working Dataset requires a Source Dataset")
        return self
