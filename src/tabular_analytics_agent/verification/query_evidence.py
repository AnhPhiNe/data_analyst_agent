"""Baseline gates for evidence derived from an analytical query."""

from __future__ import annotations

from tabular_analytics_agent.data import (
    DATASET_TABLE,
    ROW_COUNT_DEPENDENCY,
    ROW_NUMBER_COLUMN,
    QueryResult,
)
from tabular_analytics_agent.data.sql_policy import is_whole_dataset_count
from tabular_analytics_agent.domain import (
    DataProfile,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)
from tabular_analytics_agent.verification.provenance import query_provenance_status


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
    verified_dataset_count = verified_dataset_count_matches_profile(profile, result)
    provenance_valid, provenance_message = query_provenance_status(result)
    lineage_valid, lineage_message = _query_lineage_matches_profile(result, profile_fields)
    schema_grounded = (bool(source_fields) and not unknown_fields) or (
        not source_fields and verified_dataset_count
    )
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
            name="sql_provenance",
            passed=provenance_valid,
            message=provenance_message,
        ),
        VerificationCheck(
            name="output_lineage",
            passed=lineage_valid,
            message=lineage_message,
        ),
        VerificationCheck(
            name="schema_grounding",
            passed=schema_grounded,
            message=(
                "All referenced source fields exist in the Data Profile."
                if source_fields and not unknown_fields
                else (
                    "Whole-dataset COUNT(*) matches the profiled row count."
                    if verified_dataset_count
                    else _schema_failure_message(source_fields, unknown_fields)
                )
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


def verified_dataset_count_matches_profile(profile: DataProfile, result: QueryResult) -> bool:
    """Revalidate a whole-dataset count marker against SQL and the profiled row total."""
    if (
        result.dataset_count_scope != "whole_dataset"
        or not is_whole_dataset_count(result.sql, allowed_table=DATASET_TABLE)
        or not query_provenance_status(result)[0]
        or result.truncated
        or result.row_count != 1
        or len(result.rows) != 1
        or len(result.columns) != 1
    ):
        return False
    count_value = result.rows[0][0]
    return (
        isinstance(count_value, int)
        and not isinstance(count_value, bool)
        and count_value >= 0
        and count_value == profile.row_count
    )


def _query_lineage_matches_profile(
    result: QueryResult, profile_fields: set[str]
) -> tuple[bool, str]:
    """Ensure every inspected output dependency is grounded in the profiled source schema."""
    inspection = result.inspection
    if inspection is None:
        return False, "Query output lineage is unavailable for this legacy result."
    output_names = {column.name.casefold() for column in result.columns}
    inspected_names = {name.casefold() for name, _ in inspection.output_dependencies}
    if not output_names <= inspected_names:
        missing = sorted(output_names - inspected_names)
        return False, f"Query output lineage is missing columns: {', '.join(missing)}."
    known_fields = {field.casefold() for field in profile_fields}
    unknown = sorted(
        dependency
        for output, dependencies in inspection.output_dependencies
        for dependency in dependencies
        if dependency != ROW_COUNT_DEPENDENCY
        and not (
            output.casefold() == ROW_NUMBER_COLUMN.casefold() and dependency.casefold() == "rowid"
        )
        and dependency.casefold() not in known_fields
    )
    if unknown:
        return False, (
            f"Query output lineage has unknown source fields: {', '.join(dict.fromkeys(unknown))}."
        )
    return True, "Every query output is linked to profiled source fields or a row-count marker."


def _schema_failure_message(source_fields: tuple[str, ...], unknown_fields: list[str]) -> str:
    if not source_fields:
        return "No source fields were supplied for verification."
    return f"Unknown source fields: {', '.join(unknown_fields)}"
