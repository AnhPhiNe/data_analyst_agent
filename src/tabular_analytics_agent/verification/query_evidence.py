"""Baseline gates for evidence derived from an analytical query."""

from __future__ import annotations

from tabular_analytics_agent.data import QueryResult
from tabular_analytics_agent.domain import (
    DataProfile,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)


def verify_query_evidence(
    *,
    profile: DataProfile,
    result: QueryResult,
    source_fields: tuple[str, ...],
    current_working_dataset_version: int,
) -> VerificationResult:
    """Verify dataset identity, version, schema grounding, and usable query evidence."""
    profile_fields = {field.name for field in profile.fields}
    unknown_fields = sorted(set(source_fields) - profile_fields)
    checks = (
        VerificationCheck(
            name="dataset_identity",
            passed=result.dataset_id == profile.dataset.dataset_id,
            message=(
                "Query result belongs to the profiled Source Dataset."
                if result.dataset_id == profile.dataset.dataset_id
                else "Query result belongs to a different Source Dataset."
            ),
        ),
        VerificationCheck(
            name="working_dataset_version",
            passed=result.working_dataset_version == current_working_dataset_version,
            message=(
                "Query result uses the current Working Dataset version."
                if result.working_dataset_version == current_working_dataset_version
                else "Query result is stale relative to the current Working Dataset."
            ),
        ),
        VerificationCheck(
            name="schema_grounding",
            passed=bool(source_fields) and not unknown_fields,
            message=(
                "All referenced source fields exist in the Data Profile."
                if source_fields and not unknown_fields
                else _schema_failure_message(source_fields, unknown_fields)
            ),
        ),
        VerificationCheck(
            name="result_evidence",
            passed=result.row_count > 0,
            message=(
                "Query returned evidence rows."
                if result.row_count > 0
                else "Query returned no evidence rows."
            ),
        ),
    )
    status = (
        VerificationStatus.PASSED
        if all(check.passed for check in checks)
        else VerificationStatus.FAILED
    )
    return VerificationResult(status=status, checks=checks)


def _schema_failure_message(source_fields: tuple[str, ...], unknown_fields: list[str]) -> str:
    if not source_fields:
        return "No source fields were supplied for verification."
    return f"Unknown source fields: {', '.join(unknown_fields)}"
