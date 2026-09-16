"""Session-scoped adapter that executes statistical requests against a Working Dataset."""

from __future__ import annotations

import multiprocessing as mp
import time
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from tabular_analytics_agent.data import (
    DatasetHandle,
    FullDataRead,
    QueryResult,
    TabularDataCore,
    quote_identifier,
)
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
from tabular_analytics_agent.statistics.errors import (
    InsufficientSampleError,
    StatisticalAnalysisError,
    StatisticalParameterError,
    StatisticalTimeoutError,
)
from tabular_analytics_agent.statistics.models import (
    StatisticalOperation,
    StatisticalRequest,
    StatisticalResult,
)
from tabular_analytics_agent.statistics.worker import statistical_worker_entry

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
    full_input_row_count: int

    @model_validator(mode="after")
    def identities_match(self) -> Self:
        if self.action.working_dataset_version != self.input_query.working_dataset_version:
            raise ValueError("statistical output versions must match")
        if self.result.rows_loaded != self.full_input_row_count:
            raise ValueError("statistical output full-input count does not match its result")
        if self.sampled:
            raise ValueError("the MVP statistical tool does not support sampled output")
        return self


class StatisticalTool:
    """Execute allowlisted statistics over the complete approved input scope."""

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
        started = time.perf_counter()
        effective_timeout = _effective_timeout(self._data_core, timeout_seconds)
        if handle.dataset != profile.dataset:
            raise StatisticalAnalysisError(
                "DatasetHandle and DataProfile must describe the same Source Dataset"
            )
        field_profiles = {field.name: field for field in profile.fields}
        unknown = sorted(set(request.source_fields) - set(field_profiles))
        if unknown:
            raise StatisticalAnalysisError(f"unknown statistical fields: {', '.join(unknown)}")
        _validate_field_types(request, field_profiles)

        quoted_fields = ", ".join(quote_identifier(field) for field in request.source_fields)
        extraction_sql = f"SELECT {quoted_fields} FROM {quote_identifier(handle.table_name)}"
        remaining = _remaining_seconds(started, effective_timeout)
        if remaining <= 0:
            raise StatisticalTimeoutError("Statistical analysis exceeded its execution deadline")
        full_read = self._data_core.read_full(
            handle,
            extraction_sql,
            timeout_seconds=remaining,
        )
        rows_loaded = full_read.row_count
        population_row_count = full_read.population_row_count
        dataset_row_count = full_read.dataset_row_count
        result = _run_statistical_worker(
            rows=full_read.rows,
            columns=tuple(column.name for column in full_read.columns),
            request_payload=request.model_dump(mode="json"),
            deadline=started + effective_timeout,
        )
        result_payload = result.model_dump(mode="json")
        result_payload.update(
            {
                "dataset_row_count": dataset_row_count,
                "population_row_count": population_row_count,
                "rows_loaded": rows_loaded,
                "sampled": False,
                "sampling_method": None,
                "sampling_seed": None,
                "partial": False,
                "truncated": False,
            }
        )
        result = StatisticalResult.model_validate(result_payload)
        query_result = full_read.as_query_result(self._data_core.limits.max_query_rows)
        verification = _verify_statistical_result(
            handle=handle,
            profile=profile,
            request=request,
            full_read=full_read,
            result=result,
            sampled=False,
        )
        action = ToolAction(
            action_id=uuid4(),
            tool_name="statistical_analysis",
            schema_version="1",
            working_dataset_version=handle.working_dataset_version,
            inputs={
                "request": request.reproducible_parameters(),
                "input_sql": full_read.sql,
                "dataset_row_count": dataset_row_count,
                "population_row_count": population_row_count,
                "rows_loaded": rows_loaded,
                "sampled": False,
                "sampling_method": None,
                "sampling_seed": None,
                "partial": False,
                "truncated": False,
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
            sampled=False,
            full_input_row_count=rows_loaded,
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
    full_read: FullDataRead,
    result: StatisticalResult,
    sampled: bool,
) -> VerificationResult:
    checks = (
        VerificationCheck(
            name="dataset_identity",
            passed=full_read.dataset_id == profile.dataset.dataset_id,
            message=(
                "Statistical input belongs to the profiled Source Dataset."
                if full_read.dataset_id == profile.dataset.dataset_id
                else "Statistical input belongs to a different Source Dataset."
            ),
        ),
        VerificationCheck(
            name="working_dataset_version",
            passed=full_read.working_dataset_version == handle.working_dataset_version,
            message=(
                "Statistical input uses the current Working Dataset version."
                if full_read.working_dataset_version == handle.working_dataset_version
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
            name="full_data_scope",
            passed=(
                not sampled
                and result.sampled is False
                and result.dataset_row_count == full_read.dataset_row_count
                and full_read.dataset_row_count == profile.row_count
                and result.population_row_count == full_read.population_row_count
                and result.rows_loaded == full_read.row_count
                and result.rows_loaded == result.population_row_count
                and result.partial is False
                and result.truncated is False
                and full_read.row_count == full_read.population_row_count
            ),
            message=(
                "Statistical input was read completely within the approved scope."
                if (
                    not sampled
                    and result.sampled is False
                    and result.dataset_row_count == full_read.dataset_row_count
                    and full_read.dataset_row_count == profile.row_count
                    and result.population_row_count == full_read.population_row_count
                    and result.rows_loaded == full_read.row_count
                    and result.rows_loaded == result.population_row_count
                    and result.partial is False
                    and result.truncated is False
                )
                else "Statistical input does not satisfy the full-data invariant."
            ),
        ),
        VerificationCheck(
            name="sample_and_missing_data",
            passed=(
                result.sample_size > 0
                and result.missing_row_count >= 0
                and result.sample_size <= full_read.row_count
                and result.missing_row_count <= full_read.row_count
            ),
            message="Usable observations and missing-data exclusions are recorded for all rows.",
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


def _effective_timeout(data_core: TabularDataCore, timeout_seconds: float | None) -> float:
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return min(
        timeout_seconds or data_core.limits.query_timeout_seconds,
        data_core.limits.query_timeout_seconds,
    )


def _remaining_seconds(started: float, timeout_seconds: float) -> float:
    return max(0.0, timeout_seconds - (time.perf_counter() - started))


def _run_statistical_worker(
    *,
    rows: tuple[tuple[Any, ...], ...],
    columns: tuple[str, ...],
    request_payload: dict[str, Any],
    deadline: float,
) -> StatisticalResult:
    """Run Python analysis in a spawned process that the parent can terminate for real."""
    remaining = max(0.0, deadline - time.perf_counter())
    if remaining <= 0:
        raise StatisticalTimeoutError("Statistical analysis exceeded its execution deadline")

    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=statistical_worker_entry,
        args=(rows, columns, request_payload, sender),
        name="tabular-statistical-worker",
        daemon=True,
    )
    try:
        process.start()
    except Exception as exc:
        receiver.close()
        raise StatisticalAnalysisError("Statistical worker could not be started") from exc
    finally:
        # The child owns its duplicated send handle after start; the parent must close its copy.
        sender.close()

    try:
        process.join(timeout=max(0.0, deadline - time.perf_counter()))
        if process.is_alive():
            _terminate_worker(process)
            raise StatisticalTimeoutError(
                "Statistical analysis exceeded its execution deadline; worker terminated"
            )
        if not receiver.poll(0.1):
            raise StatisticalAnalysisError(
                f"Statistical worker exited without a result (exit code {process.exitcode})"
            )
        payload = receiver.recv()
    except (EOFError, OSError) as exc:
        raise StatisticalAnalysisError(
            "Statistical worker closed before returning a result"
        ) from exc
    finally:
        if process.is_alive():
            _terminate_worker(process)
        receiver.close()
        process.close()

    if not isinstance(payload, dict) or not payload.get("ok"):
        _raise_worker_error(payload if isinstance(payload, dict) else {})
    try:
        return StatisticalResult.model_validate(payload["result"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StatisticalAnalysisError("Statistical worker returned an invalid result") from exc


def _terminate_worker(process: Any) -> None:
    """Terminate and join a worker, escalating to kill if terminate did not stop it."""
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)
    if process.is_alive():
        raise StatisticalAnalysisError("Statistical worker could not be terminated")


def _raise_worker_error(payload: dict[str, Any]) -> None:
    error = str(payload.get("error") or "Statistical worker failed")
    error_type = str(payload.get("error_type") or "")
    if error_type.endswith("InsufficientSampleError"):
        raise InsufficientSampleError(error)
    if error_type.endswith("StatisticalParameterError"):
        raise StatisticalParameterError(error)
    if error_type.endswith("StatisticalTimeoutError"):
        raise StatisticalTimeoutError(error)
    raise StatisticalAnalysisError(error)
