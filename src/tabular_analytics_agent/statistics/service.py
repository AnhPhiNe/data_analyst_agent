"""Session-scoped adapter that executes statistical requests against a Working Dataset."""

from __future__ import annotations

import time
from typing import Self
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, ConfigDict, model_validator

from tabular_analytics_agent.data import DatasetHandle, QueryResult, TabularDataCore
from tabular_analytics_agent.domain import (
    ActionStatus,
    DataProfile,
    FieldKind,
    FieldProfile,
    ToolAction,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)
from tabular_analytics_agent.statistics.engine import analyze
from tabular_analytics_agent.statistics.errors import StatisticalAnalysisError
from tabular_analytics_agent.statistics.models import (
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
)

_NUMERIC_OPERATIONS = {
    StatisticalOperation.DESCRIPTIVE,
    StatisticalOperation.CONFIDENCE_INTERVAL,
    StatisticalOperation.T_TEST,
    StatisticalOperation.MANN_WHITNEY,
    StatisticalOperation.ANOVA,
    StatisticalOperation.KRUSKAL_WALLIS,
    StatisticalOperation.CORRELATION,
    StatisticalOperation.LINEAR_REGRESSION,
    StatisticalOperation.LOGISTIC_REGRESSION,
}


class StatisticalToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: ToolAction
    result: StatisticalResult
    input_query: QueryResult
    sampled: bool

    @model_validator(mode="after")
    def identities_match(self) -> Self:
        if self.action.working_dataset_version != self.input_query.working_dataset_version:
            raise ValueError("statistical output versions must match")
        return self


class StatisticalTool:
    """Execute allowlisted statistics with reproducible, bounded data extraction."""

    def __init__(self, data_core: TabularDataCore) -> None:
        self._data_core = data_core

    def execute(
        self,
        *,
        handle: DatasetHandle,
        profile: DataProfile,
        request: StatisticalRequest,
        timeout_seconds: float | None = None,
    ) -> StatisticalToolOutput:
        if handle.dataset != profile.dataset:
            raise StatisticalAnalysisError(
                "DatasetHandle and DataProfile must describe the same Source Dataset"
            )
        field_profiles = {field.name: field for field in profile.fields}
        unknown = sorted(set(request.source_fields) - set(field_profiles))
        if unknown:
            raise StatisticalAnalysisError(f"unknown statistical fields: {', '.join(unknown)}")
        _validate_field_types(request, field_profiles)

        quoted_fields = ", ".join(_quote_identifier(field) for field in request.source_fields)
        sample_size = self._data_core.limits.max_query_rows
        extraction_sql = (
            f"SELECT {quoted_fields} FROM {_quote_identifier(handle.table_name)} "
            f"USING SAMPLE reservoir({sample_size} ROWS) REPEATABLE({request.random_seed})"
        )
        started = time.perf_counter()
        query_result = self._data_core.query(
            handle,
            extraction_sql,
            timeout_seconds=timeout_seconds,
        )
        if query_result.truncated:
            raise StatisticalAnalysisError(
                "statistical input exceeded the bounded query result; reduce the sample size"
            )
        frame = pd.DataFrame(
            query_result.rows,
            columns=[column.name for column in query_result.columns],
        )
        result = analyze(frame, request)
        sampled = profile.row_count > query_result.row_count
        verification = _verify_statistical_result(
            handle=handle,
            profile=profile,
            request=request,
            query_result=query_result,
            result=result,
            sampled=sampled,
        )
        action = ToolAction(
            action_id=uuid4(),
            tool_name="statistical_analysis",
            schema_version="1",
            working_dataset_version=handle.working_dataset_version,
            inputs={
                "request": request.reproducible_parameters(),
                "input_sql": query_result.sql,
                "sampling": {
                    "method": "reservoir",
                    "maximum_rows": sample_size,
                    "random_seed": request.random_seed,
                    "sampled": sampled,
                },
            },
            status=ActionStatus.SUCCEEDED,
            output_ref=f"statistical-result:{result.result_id}",
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
            verification_results=(verification,),
        )
        return StatisticalToolOutput(
            action=action,
            result=result,
            input_query=query_result,
            sampled=sampled,
        )


def _validate_field_types(request: StatisticalRequest, profiles: dict[str, FieldProfile]) -> None:
    if request.operation not in _NUMERIC_OPERATIONS:
        return
    numeric_fields = set(request.value_fields)
    if request.value_field:
        numeric_fields.add(request.value_field)
    if request.x_field:
        numeric_fields.add(request.x_field)
    if request.y_field and request.operation is not StatisticalOperation.LOGISTIC_REGRESSION:
        numeric_fields.add(request.y_field)
    invalid = sorted(
        field for field in numeric_fields if profiles[field].kind is not FieldKind.NUMERIC
    )
    if invalid:
        raise StatisticalAnalysisError(
            f"operation {request.operation.value} requires numeric fields: {', '.join(invalid)}"
        )


def _verify_statistical_result(
    *,
    handle: DatasetHandle,
    profile: DataProfile,
    request: StatisticalRequest,
    query_result: QueryResult,
    result: StatisticalResult,
    sampled: bool,
) -> VerificationResult:
    checks = (
        VerificationCheck(
            name="dataset_identity",
            passed=query_result.dataset_id == profile.dataset.dataset_id,
            message=(
                "Statistical input belongs to the profiled Source Dataset."
                if query_result.dataset_id == profile.dataset.dataset_id
                else "Statistical input belongs to a different Source Dataset."
            ),
        ),
        VerificationCheck(
            name="working_dataset_version",
            passed=query_result.working_dataset_version == handle.working_dataset_version,
            message=(
                "Statistical input uses the current Working Dataset version."
                if query_result.working_dataset_version == handle.working_dataset_version
                else "Statistical input is stale relative to the Working Dataset."
            ),
        ),
        VerificationCheck(
            name="schema_and_operation",
            passed=result.source_fields == request.source_fields,
            message=(
                "Result fields match the validated statistical request."
                if result.source_fields == request.source_fields
                else "Result fields do not match the statistical request."
            ),
        ),
        VerificationCheck(
            name="sample_and_missing_data",
            passed=result.sample_size > 0 and result.missing_row_count >= 0,
            message=(
                "Sample size and missing-data handling are recorded"
                + (" for a deterministic reservoir sample." if sampled else " for all rows.")
            ),
        ),
        VerificationCheck(
            name="statistical_reporting",
            passed=(
                bool(result.estimates)
                and (
                    result.p_value is None
                    or (result.effect_size is not None and result.adjusted_alpha is not None)
                )
            ),
            message=(
                "Estimates, significance, effect size, and assumptions are reported "
                "when applicable."
            ),
        ),
    )
    status = (
        VerificationStatus.PASSED
        if all(check.passed for check in checks)
        else VerificationStatus.FAILED
    )
    return VerificationResult(status=status, checks=checks)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
