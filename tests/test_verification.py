"""Tests through the deterministic Verification Gate interface."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tabular_analytics_agent.data import (
    QueryColumn,
    QueryInspection,
    QueryResult,
    analyze_read_only_sql,
)
from tabular_analytics_agent.domain import (
    DataProfile,
    DatasetIdentity,
    FieldKind,
    FieldProfile,
    VerificationStatus,
)
from tabular_analytics_agent.verification import verify_query_evidence

Scalar = str | int | float | bool | None


def make_profile() -> DataProfile:
    dataset = DatasetIdentity(
        dataset_id=uuid4(),
        original_filename="sales.csv",
        sha256="a" * 64,
        size_bytes=100,
    )
    return DataProfile(
        profile_id=uuid4(),
        dataset=dataset,
        row_count=5,
        fields=(
            FieldProfile(
                name="region",
                kind=FieldKind.CATEGORICAL,
                missing_count=0,
                missing_rate=0.0,
                unique_count=2,
            ),
            FieldProfile(
                name="revenue",
                kind=FieldKind.NUMERIC,
                missing_count=0,
                missing_rate=0.0,
                unique_count=5,
            ),
        ),
        profile_version="1",
        created_at=datetime.now(UTC),
    )


def make_result(
    profile: DataProfile,
    *,
    rows: tuple[tuple[Scalar, ...], ...] | None = None,
) -> QueryResult:
    selected_rows = rows if rows is not None else (("North", 350.0), ("South", 200.0))
    sql = "SELECT region, SUM(revenue) AS revenue FROM dataset GROUP BY region"
    analysis = analyze_read_only_sql(sql, allowed_table="dataset")
    return QueryResult(
        query_id=uuid4(),
        dataset_id=profile.dataset.dataset_id,
        working_dataset_version=1,
        sql=analysis.normalized_sql,
        columns=(
            QueryColumn(name="region", data_type="VARCHAR"),
            QueryColumn(name="revenue", data_type="DOUBLE"),
        ),
        rows=selected_rows,
        row_count=len(selected_rows),
        truncated=False,
        duration_ms=2,
        inspection=QueryInspection(
            normalized_sql=analysis.normalized_sql,
            referenced_columns=analysis.referenced_columns,
            has_wildcard=analysis.has_wildcard,
            group_by_columns=analysis.group_by_columns,
            unaliased_outputs=analysis.unaliased_outputs,
            filters=analysis.filters,
            filter_scopes=analysis.filter_scopes,
            dataset_count_scope=analysis.dataset_count_scope,
            provenance_version=analysis.provenance_version,
            base_relations=analysis.base_relations,
            output_dependencies=analysis.output_dependencies,
        ),
    )


def test_valid_query_evidence_passes_all_gates() -> None:
    profile = make_profile()
    result = make_result(profile)

    verification = verify_query_evidence(
        profile=profile,
        result=result,
        source_fields=("region", "revenue"),
        current_working_dataset_version=1,
    )

    assert verification.status is VerificationStatus.PASSED
    assert all(check.passed for check in verification.checks)


@pytest.mark.parametrize(
    ("source_fields", "version", "use_other_dataset", "empty_rows", "failed_check"),
    [
        (("missing",), 1, False, False, "schema_grounding"),
        ((), 1, False, False, "schema_grounding"),
        (("region",), 2, False, False, "working_dataset_version"),
        (("region",), 1, True, False, "dataset_identity"),
        (("region",), 1, False, True, "result_evidence"),
    ],
)
def test_invalid_query_evidence_fails_the_relevant_gate(
    source_fields: tuple[str, ...],
    version: int,
    use_other_dataset: bool,
    empty_rows: bool,
    failed_check: str,
) -> None:
    profile = make_profile()
    result = make_result(profile, rows=() if empty_rows else None)
    if use_other_dataset:
        result = result.model_copy(update={"dataset_id": uuid4()})

    verification = verify_query_evidence(
        profile=profile,
        result=result,
        source_fields=source_fields,
        current_working_dataset_version=version,
    )

    assert verification.status is VerificationStatus.FAILED
    assert not next(check for check in verification.checks if check.name == failed_check).passed


@pytest.mark.parametrize(
    "update",
    [
        {"row_count": 2, "rows": (("North", 350.0),)},
        {"columns": ()},
        {"columns": (QueryColumn(name="same", data_type="VARCHAR"),) * 2},
        {"rows": (("North",),), "row_count": 1},
    ],
)
def test_query_result_rejects_invalid_shape(update: dict[str, object]) -> None:
    profile = make_profile()
    valid = make_result(profile)

    with pytest.raises(ValidationError):
        QueryResult.model_validate({**valid.model_dump(), **update})
