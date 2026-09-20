"""Shared provenance and full-data checks used by verification and publication boundaries."""

from __future__ import annotations

import re
from typing import Any

from tabular_analytics_agent.data import DATASET_TABLE, ROW_COUNT_DEPENDENCY, QueryResult
from tabular_analytics_agent.domain import ToolAction
from tabular_analytics_agent.statistics import StatisticalResult

_ROW_METRIC = re.compile(r"^row\[(\d+)]\.(.+)$")
_FULL_DATA_FIELDS = (
    "dataset_row_count",
    "population_row_count",
    "rows_loaded",
    "sampled",
    "partial",
    "truncated",
)


EvidenceResult = QueryResult | StatisticalResult


def result_reference(result: EvidenceResult) -> str:
    """Return the stable reference string a Tool Action stores for a result."""
    if isinstance(result, QueryResult):
        return f"query-result:{result.query_id}"
    return f"statistical-result:{result.result_id}"


def statistical_full_data_status(result: StatisticalResult) -> tuple[bool, str]:
    """Return whether a statistical result explicitly satisfies the MVP full-data contract."""
    missing = [name for name in _FULL_DATA_FIELDS if getattr(result, name, None) is None]
    if missing:
        return False, "Statistical evidence has legacy or incomplete full-data metadata."
    dataset_row_count = result.dataset_row_count
    population_row_count = result.population_row_count
    rows_loaded = result.rows_loaded
    sampled = result.sampled
    partial = result.partial
    truncated = result.truncated
    # The null sampling method and seed are required values for MVP runs.  They are intentionally
    # checked separately from the required scope fields above, because ``None`` means “no
    # sampling” rather than missing metadata for these two fields.
    assert (
        dataset_row_count is not None
        and population_row_count is not None
        and rows_loaded is not None
        and sampled is not None
        and partial is not None
        and truncated is not None
    )
    if sampled is not False:
        return False, "Statistical evidence was sampled and cannot be published as MVP output."
    if result.sampling_method is not None or result.sampling_seed is not None:
        return False, "Full-data statistical evidence cannot declare a sampling method or seed."
    if partial is not False or truncated is not False:
        return False, "Partial or truncated statistical evidence cannot be published."
    if rows_loaded != population_row_count:
        return False, "Statistical evidence did not analyze every row in its approved scope."
    if population_row_count > dataset_row_count:
        return False, "Statistical population cannot exceed the source dataset row count."
    if result.sample_size > rows_loaded:
        return False, "Statistical sample_size exceeds the rows loaded for analysis."
    if result.missing_row_count > rows_loaded:
        return False, "Statistical missing_row_count exceeds the rows loaded for analysis."
    return True, "Statistical evidence covers every row in its approved analysis scope."


def query_provenance_status(result: QueryResult) -> tuple[bool, str]:
    """Validate the v1 inspection attached to a newly executed query result."""
    inspection = result.inspection
    if inspection is None or inspection.provenance_version != "v1":
        return False, "Query evidence has legacy or missing SQL provenance and must be rerun."
    if inspection.normalized_sql != result.sql:
        return False, "Query result SQL does not match its inspected normalized SQL."
    base_relations = {value.casefold() for value in inspection.base_relations}
    if DATASET_TABLE.casefold() not in base_relations:
        return False, "Query provenance does not identify the session dataset as a base relation."
    return True, "Query evidence is bound to the inspected dataset relation and SQL."


def query_output_dependencies(result: QueryResult, metric: str) -> tuple[str, ...] | None:
    """Return source dependencies for a row metric, or ``None`` for a non-output metric."""
    match = _ROW_METRIC.fullmatch(metric)
    if not match or result.inspection is None:
        return None
    output_name = match.group(2)
    for name, dependencies in result.inspection.output_dependencies:
        if name == output_name:
            return dependencies
    # SQL result columns preserve their exact names; case-insensitive matching is useful for
    # persisted model references while keeping the canonical inspection untouched.
    folded = output_name.casefold()
    for name, dependencies in result.inspection.output_dependencies:
        if name.casefold() == folded:
            return dependencies
    return None


def query_claim_status(
    result: QueryResult,
    metrics: tuple[str, ...],
    *,
    profile_fields: set[str],
) -> tuple[bool, str]:
    """Check that each claimed query output is tied to a source field or row count marker."""
    valid, message = query_provenance_status(result)
    if not valid:
        return False, message
    for metric in metrics:
        if metric == "result.row_count":
            if result.truncated:
                return False, "A truncated query result cannot support a result row-count claim."
            continue
        dependencies = query_output_dependencies(result, metric)
        if dependencies is None:
            return False, f"Claimed query metric {metric!r} is not a deterministic output column."
        if not dependencies:
            return False, (
                f"Claimed query metric {metric!r} is a literal or otherwise has no source-field "
                "dependency. Literal labels may accompany evidence but cannot be claimed "
                "as metrics."
            )
        unknown = sorted(
            dependency
            for dependency in dependencies
            if dependency != ROW_COUNT_DEPENDENCY and dependency.casefold() not in profile_fields
        )
        if unknown:
            return False, (
                f"Claimed query metric {metric!r} has unverified source dependencies: "
                f"{', '.join(unknown)}."
            )
    return True, "Claimed query metrics have deterministic source dependencies."


def statistical_action_status(action: ToolAction, result: StatisticalResult) -> tuple[bool, str]:
    """Ensure a statistical Tool Action records the same full-data scope as its result."""
    valid, message = statistical_full_data_status(result)
    if not valid:
        return False, message
    inputs = action.inputs
    required = (
        "dataset_row_count",
        "population_row_count",
        "rows_loaded",
        "sampled",
        "sampling_method",
        "sampling_seed",
        "partial",
        "truncated",
    )
    missing = [name for name in required if name not in inputs]
    if missing:
        return False, (
            "Tool Action does not record full-data metadata: " + ", ".join(missing) + "."
        )
    if "sampling" in inputs or "maximum_rows" in inputs:
        return False, "Tool Action still contains legacy sampling limits and must be rerun."
    for name in required:
        if inputs[name] != getattr(result, name):
            return False, f"Tool Action {name} does not match the executed statistical result."
    return True, "Tool Action records the executed statistical full-data scope."


def result_provenance_metadata(result: QueryResult | StatisticalResult) -> dict[str, Any]:
    """Return serializable provenance fields for evidence catalogs and trails.

    Query results expose SQL provenance; statistical results expose the full-data execution
    contract.  Missing fields stay missing for historical records so callers can mark them as
    legacy instead of fabricating a migration.
    """
    if isinstance(result, StatisticalResult):
        return {
            "provenance_version": "v1" if result.sampled is False else None,
            "dataset_row_count": result.dataset_row_count,
            "population_row_count": result.population_row_count,
            "rows_loaded": result.rows_loaded,
            "sample_size": result.sample_size,
            "missing_row_count": result.missing_row_count,
            "sampled": result.sampled,
            "sampling_method": result.sampling_method,
            "sampling_seed": result.sampling_seed,
            "partial": result.partial,
            "truncated": result.truncated,
        }
    inspection = result.inspection
    return {
        "provenance_version": inspection.provenance_version if inspection else None,
        "base_relations": list(inspection.base_relations) if inspection else [],
        "output_dependencies": (
            [
                {"output": output, "source_fields": list(dependencies)}
                for output, dependencies in inspection.output_dependencies
            ]
            if inspection
            else []
        ),
    }


__all__ = [
    "query_claim_status",
    "query_output_dependencies",
    "query_provenance_status",
    "result_provenance_metadata",
    "statistical_action_status",
    "statistical_full_data_status",
]
