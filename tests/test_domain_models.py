"""Tests through the public domain interface."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisPlan,
    AnalysisSession,
    AnalyticalArtifact,
    AnalyticalGoal,
    ArtifactType,
    CategoryFrequency,
    ChartIntent,
    DataProfile,
    DatasetIdentity,
    EvidenceTrail,
    EvidenceValue,
    FieldKind,
    FieldProfile,
    GoalFamily,
    InsightAssertion,
    InsightOperator,
    NumericSummary,
    PlanStep,
    QueryResultReference,
    SemanticAnnotation,
    SessionStatus,
    TemporalSummary,
    ToolAction,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    VerifiedInsight,
    fingerprint_semantic_annotations,
)


def make_dataset() -> DatasetIdentity:
    return DatasetIdentity(
        dataset_id=uuid4(),
        original_filename="tiny.csv",
        sha256="a" * 64,
        size_bytes=10,
    )


def make_field(name: str = "revenue") -> FieldProfile:
    return FieldProfile(
        name=name,
        kind=FieldKind.NUMERIC,
        missing_count=0,
        missing_rate=0.0,
        unique_count=2,
    )


def test_field_summaries_must_match_field_kind() -> None:
    summary = NumericSummary(count=1, mean=10.0)
    with pytest.raises(ValidationError, match="requires a numeric field"):
        FieldProfile(
            name="region",
            kind=FieldKind.CATEGORICAL,
            missing_count=0,
            missing_rate=0.0,
            unique_count=1,
            numeric_summary=summary,
        )

    with pytest.raises(ValidationError, match="requires a datetime field"):
        FieldProfile(
            name="region",
            kind=FieldKind.CATEGORICAL,
            missing_count=0,
            missing_rate=0.0,
            unique_count=1,
            temporal_summary=TemporalSummary(earliest="2026-01-01", latest="2026-01-02"),
        )

    field = FieldProfile(
        name="region",
        kind=FieldKind.CATEGORICAL,
        missing_count=0,
        missing_rate=0.0,
        unique_count=1,
        top_values=(CategoryFrequency(value="North", count=1, rate=1.0),),
    )
    assert field.top_values[0].value == "North"


def make_evidence() -> EvidenceTrail:
    return EvidenceTrail(
        trail_id=uuid4(),
        dataset_id=make_dataset().dataset_id,
        working_dataset_version=1,
        semantic_annotation_fingerprint="0" * 64,
        source_fields=("region", "revenue"),
        source_row_count=5,
        result_row_count=2,
        missing_data_handling="No missing values were present.",
        tool_action_ids=(uuid4(),),
        tool_parameters=({"sql": "SELECT region, SUM(revenue) FROM dataset"},),
        values=(EvidenceValue(metric="total_revenue", value=550.0),),
    )


def make_verification() -> VerificationResult:
    return VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="schema", passed=True, message="Fields exist"),),
    )


def make_action(
    *,
    status: ActionStatus,
    output_ref: str | None = None,
    error: str | None = None,
    verification_results: tuple[VerificationResult, ...] = (),
) -> ToolAction:
    return ToolAction(
        action_id=uuid4(),
        tool_name="read_only_sql",
        schema_version="1",
        working_dataset_version=1,
        inputs={"query": "SELECT 1"},
        status=status,
        output_ref=output_ref,
        error=error,
        verification_results=verification_results,
    )


def test_data_profile_rejects_impossible_counts() -> None:
    with pytest.raises(ValidationError, match="missing_count"):
        DataProfile(
            profile_id=uuid4(),
            dataset=make_dataset(),
            row_count=2,
            fields=(
                FieldProfile(
                    name="revenue",
                    kind=FieldKind.NUMERIC,
                    missing_count=3,
                    missing_rate=1.0,
                    unique_count=0,
                ),
            ),
            profile_version="1",
            created_at=datetime.now(UTC),
        )


def test_data_profile_rejects_duplicate_names_and_counts() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="field names must be unique"):
        DataProfile(
            profile_id=uuid4(),
            dataset=make_dataset(),
            row_count=2,
            fields=(make_field(), make_field()),
            profile_version="1",
            created_at=now,
        )

    with pytest.raises(ValidationError, match="duplicate_row_count"):
        DataProfile(
            profile_id=uuid4(),
            dataset=make_dataset(),
            row_count=2,
            fields=(make_field(),),
            duplicate_row_count=3,
            profile_version="1",
            created_at=now,
        )

    with pytest.raises(ValidationError, match="unique_count"):
        DataProfile(
            profile_id=uuid4(),
            dataset=make_dataset(),
            row_count=1,
            fields=(make_field(),),
            profile_version="1",
            created_at=now,
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        DataProfile(
            profile_id=uuid4(),
            dataset=make_dataset(),
            row_count=2,
            fields=(make_field(),),
            profile_version="1",
            created_at=datetime.now(),
        )


def test_valid_profile_and_annotation() -> None:
    profile = DataProfile(
        profile_id=uuid4(),
        dataset=make_dataset(),
        row_count=2,
        fields=(make_field(),),
        profile_version="1",
        created_at=datetime.now(UTC),
    )
    annotation = SemanticAnnotation(
        field_name="revenue",
        meaning="Gross sales before tax",
        unit="USD",
        confirmed_by_user=True,
    )

    assert profile.row_count == 2
    assert annotation.confirmed_by_user


def test_annotation_requires_user_confirmation() -> None:
    with pytest.raises(ValidationError, match="must be confirmed"):
        SemanticAnnotation(
            field_name="revenue",
            meaning="Gross sales before tax",
            confirmed_by_user=False,
        )


def test_plan_rejects_steps_over_budget() -> None:
    goal = AnalyticalGoal(text="Summarize revenue", family=GoalFamily.SUMMARY)
    steps = tuple(
        PlanStep(
            step_id=str(index),
            description="Aggregate",
            expected_tool="sql",
            intended_output="Summary table",
        )
        for index in range(13)
    )

    with pytest.raises(ValidationError, match="tool-action budget"):
        AnalysisPlan(plan_id=uuid4(), goal=goal, steps=steps)


def test_plan_requires_steps_with_unique_ids() -> None:
    goal = AnalyticalGoal(text="Summarize revenue", family=GoalFamily.SUMMARY)
    with pytest.raises(ValidationError, match="at least one step"):
        AnalysisPlan(plan_id=uuid4(), goal=goal, steps=())

    duplicate_steps = (
        PlanStep(
            step_id="same",
            description="Aggregate",
            expected_tool="sql",
            intended_output="Summary table",
        ),
        PlanStep(
            step_id="same",
            description="Visualize",
            expected_tool="chart",
            intended_output="Chart",
        ),
    )
    with pytest.raises(ValidationError, match="step IDs must be unique"):
        AnalysisPlan(plan_id=uuid4(), goal=goal, steps=duplicate_steps)


def test_valid_plan() -> None:
    plan = AnalysisPlan(
        plan_id=uuid4(),
        goal=AnalyticalGoal(text="Summarize revenue", family=GoalFamily.SUMMARY),
        steps=(
            PlanStep(
                step_id="aggregate",
                description="Aggregate",
                expected_tool="sql",
                intended_output="Regional totals",
            ),
        ),
    )

    assert plan.version == 1


def test_failed_action_requires_error() -> None:
    with pytest.raises(ValidationError, match="requires an error"):
        ToolAction(
            action_id=uuid4(),
            tool_name="read_only_sql",
            schema_version="1",
            working_dataset_version=1,
            inputs={"query": "SELECT 1"},
            status=ActionStatus.FAILED,
        )


def test_action_outcome_is_internally_consistent() -> None:
    with pytest.raises(ValidationError, match="requires output_ref"):
        make_action(status=ActionStatus.SUCCEEDED)

    with pytest.raises(ValidationError, match="only a failed action"):
        make_action(status=ActionStatus.PENDING, error="unexpected")

    with pytest.raises(ValidationError, match="requires verification results"):
        make_action(status=ActionStatus.SUCCEEDED, output_ref="results/action-1.parquet")

    succeeded = make_action(
        status=ActionStatus.SUCCEEDED,
        output_ref="results/action-1.parquet",
        verification_results=(make_verification(),),
    )
    failed = make_action(status=ActionStatus.FAILED, error="query timed out")

    assert succeeded.output_ref
    assert failed.error


def test_verification_requires_checks_and_matching_status() -> None:
    with pytest.raises(ValidationError, match="at least one check"):
        VerificationResult(status=VerificationStatus.PASSED, checks=())

    with pytest.raises(ValidationError, match="status must match"):
        VerificationResult(
            status=VerificationStatus.PASSED,
            checks=(VerificationCheck(name="schema", passed=False, message="Unknown field"),),
        )

    passed = VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="schema", passed=True, message="Fields exist"),),
    )
    assert passed.status is VerificationStatus.PASSED


@pytest.mark.parametrize(
    ("source_fields", "tool_action_ids", "values", "message"),
    [
        ((), (uuid4(),), (EvidenceValue(metric="value", value=1),), "source fields"),
        (("value",), (), (EvidenceValue(metric="value", value=1),), "tool action"),
        (("value",), (uuid4(),), (), "computed values"),
    ],
)
def test_evidence_trail_requires_reproducible_content(
    source_fields: tuple[str, ...],
    tool_action_ids: tuple[UUID, ...],
    values: tuple[EvidenceValue, ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        EvidenceTrail(
            trail_id=uuid4(),
            dataset_id=make_dataset().dataset_id,
            working_dataset_version=1,
            semantic_annotation_fingerprint="0" * 64,
            source_fields=source_fields,
            source_row_count=1,
            result_row_count=1,
            missing_data_handling="Complete-case analysis.",
            tool_action_ids=tool_action_ids,
            tool_parameters=tuple({"operation": "test"} for _ in tool_action_ids),
            values=values,
        )


def test_verified_insight_requires_passed_verification() -> None:
    verification = VerificationResult(
        status=VerificationStatus.FAILED,
        checks=(VerificationCheck(name="schema", passed=False, message="Unknown field"),),
    )

    with pytest.raises(ValidationError, match="requires passed verification"):
        VerifiedInsight(
            insight_id=uuid4(),
            claim="North is higher",
            evidence=make_evidence(),
            verification=verification,
        )


def test_verified_insight_accepts_passed_evidence() -> None:
    verification = VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="schema", passed=True, message="Fields exist"),),
    )
    insight = VerifiedInsight(
        insight_id=uuid4(),
        claim="North has higher total revenue than South.",
        evidence=make_evidence(),
        verification=verification,
    )

    assert insight.verification.status is VerificationStatus.PASSED


def test_insight_assertion_requires_operator_specific_operands() -> None:
    with pytest.raises(ValidationError, match="require right_metric"):
        InsightAssertion(
            operator=InsightOperator.GREATER_THAN,
            left_metric="row[0].revenue",
        )
    with pytest.raises(ValidationError, match="cannot include right_metric"):
        InsightAssertion(
            operator=InsightOperator.REPORTS,
            left_metric="row[0].revenue",
            right_metric="row[1].revenue",
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        InsightAssertion(
            operator=InsightOperator.EQUALS,
            left_metric="row[0].revenue",
            right_metric="row[0].revenue",
        )


def test_artifact_requires_timezone_aware_timestamp() -> None:
    source_result_ref = QueryResultReference(
        query_id=uuid4(),
        dataset_id=uuid4(),
        working_dataset_version=1,
        semantic_annotation_fingerprint=fingerprint_semantic_annotations(()),
    )
    intent = ChartIntent(
        artifact_type=ArtifactType.BAR,
        analytical_purpose="Compare regional revenue",
        source_result_ref=source_result_ref,
        x_field="region",
        y_fields=("revenue",),
        title="Revenue by region",
        labels={"revenue": "Revenue (USD)"},
        formatting_intent={"currency": "USD"},
        validation_constraints=("y_fields must be numeric",),
    )
    verification = VerificationResult(
        status=VerificationStatus.PASSED,
        checks=(VerificationCheck(name="chart", passed=True, message="Chart passed."),),
    )
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="timezone-aware"):
        AnalyticalArtifact(
            artifact_id=uuid4(),
            session_id=uuid4(),
            intent=intent,
            source_result_ref=source_result_ref,
            render_spec_ref="artifacts/chart.json",
            verification=verification,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    artifact = AnalyticalArtifact(
        artifact_id=uuid4(),
        session_id=uuid4(),
        intent=intent,
        source_result_ref=source_result_ref,
        render_spec_ref="artifacts/chart.json",
        verification=verification,
        created_at=now,
        updated_at=now,
    )
    assert artifact.intent.artifact_type is ArtifactType.BAR
    assert artifact.intent.validation_constraints
    assert artifact.version == 1

    with pytest.raises(ValidationError, match="earlier than created_at"):
        AnalyticalArtifact.model_validate(
            {
                **artifact.model_dump(),
                "updated_at": datetime(2020, 1, 1, tzinfo=UTC),
            }
        )

    failed_verification = VerificationResult(
        status=VerificationStatus.FAILED,
        checks=(VerificationCheck(name="chart", passed=False, message="Chart failed."),),
    )
    with pytest.raises(ValidationError, match="requires passed verification"):
        AnalyticalArtifact.model_validate(
            {**artifact.model_dump(), "verification": failed_verification}
        )

    other_source = source_result_ref.model_copy(update={"query_id": uuid4()})
    with pytest.raises(ValidationError, match="same Query Result"):
        AnalyticalArtifact.model_validate(
            {**artifact.model_dump(), "source_result_ref": other_source}
        )


def test_session_requires_source_before_working_dataset() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError, match="requires a Source Dataset"):
        AnalysisSession(
            session_id=uuid4(),
            status=SessionStatus.RUNNING,
            working_dataset_version=1,
            created_at=now,
            updated_at=now,
        )


def test_session_timestamp_and_annotation_invariants() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="timezone-aware"):
        AnalysisSession(
            session_id=uuid4(),
            status=SessionStatus.RUNNING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    with pytest.raises(ValidationError, match="earlier than created_at"):
        AnalysisSession(
            session_id=uuid4(),
            status=SessionStatus.RUNNING,
            created_at=now,
            updated_at=datetime(2020, 1, 1, tzinfo=UTC),
        )

    annotation = SemanticAnnotation(
        field_name="revenue",
        meaning="Gross sales",
        confirmed_by_user=True,
    )
    with pytest.raises(ValidationError, match="one active semantic annotation"):
        AnalysisSession(
            session_id=uuid4(),
            status=SessionStatus.RUNNING,
            semantic_annotations=(annotation, annotation),
            created_at=now,
            updated_at=now,
        )

    valid = AnalysisSession(
        session_id=uuid4(),
        status=SessionStatus.RUNNING,
        source_dataset_id=uuid4(),
        working_dataset_version=1,
        semantic_annotations=(annotation,),
        created_at=now,
        updated_at=now,
    )
    assert valid.working_dataset_version == 1
