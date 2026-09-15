"""Grounding checks that bind model plans and tool requests to the approved scope."""

from __future__ import annotations

import hashlib
import json

from tabular_analytics_agent.data import (
    DatasetHandle,
    TabularDataCore,
    replace_column_references,
)
from tabular_analytics_agent.domain import (
    AnalysisPlan,
    DataProfile,
    PlanStep,
)
from tabular_analytics_agent.model_gateway import (
    RequestedMetricMapping,
    RequestedMetricStatus,
    SQLToolRequestDraft,
    StatisticalToolRequestDraft,
)
from tabular_analytics_agent.orchestration.field_ids import (
    PlanRepairError,
    canonicalize_profile_fields,
    canonicalize_statistical_fields,
    field_ids,
    field_key,
)
from tabular_analytics_agent.orchestration.models import (
    AgentState,
    BoundToolRequest,
)
from tabular_analytics_agent.statistics import (
    StatisticalOperation,
    StatisticalRequest,
)

INFERENTIAL_OPERATIONS = {
    StatisticalOperation.CORRELATION,
    StatisticalOperation.T_TEST,
    StatisticalOperation.MANN_WHITNEY,
    StatisticalOperation.CHI_SQUARE,
    StatisticalOperation.ANOVA,
    StatisticalOperation.KRUSKAL_WALLIS,
    StatisticalOperation.LINEAR_REGRESSION,
    StatisticalOperation.LOGISTIC_REGRESSION,
}


INFERENCE_ALPHA = 0.05


def canonicalize_requested_metric_mappings(
    profile: DataProfile,
    mappings: object,
) -> tuple[RequestedMetricMapping, ...]:
    """Validate mapping source fields, while tolerating legacy state without mappings."""
    if mappings is None:
        return ()
    if not isinstance(mappings, (list, tuple)):
        raise ValueError("requested_metric_mappings must be a list")
    parsed = tuple(RequestedMetricMapping.model_validate(item) for item in mappings)
    return tuple(
        mapping.model_copy(
            update={
                "source_fields": canonicalize_profile_fields(profile, mapping.source_fields),
            }
        )
        for mapping in parsed
    )


def unavailable_metric_clarification_question(
    mappings: tuple[RequestedMetricMapping, ...],
) -> str:
    labels = [
        mapping.requested_label
        for mapping in mappings
        if mapping.status is RequestedMetricStatus.UNAVAILABLE
    ]
    if not labels:
        return ""
    joined = ", ".join(labels)
    return (
        f"The requested metric {joined} is unavailable from the dataset. "
        "Would you like to correct the request or confirm that it cannot be answered?"
    )


def unavailable_metric_refusal_reason(
    mappings: tuple[RequestedMetricMapping, ...],
    *,
    has_clarification: bool,
) -> str | None:
    """Return a refusal reason only after an unavailable mapping is explicitly accepted."""
    if has_clarification:
        return None
    unavailable = [
        mapping for mapping in mappings if mapping.status is RequestedMetricStatus.UNAVAILABLE
    ]
    if not unavailable:
        return None
    reasons = [mapping.reason for mapping in unavailable if mapping.reason]
    if reasons:
        return " ".join(reasons)
    labels = ", ".join(mapping.requested_label for mapping in unavailable)
    return f"Requested metric {labels} is unavailable without a safe derivation from this dataset."


def validate_plan_metric_grounding(
    steps: tuple[PlanStep, ...],
    mappings: tuple[RequestedMetricMapping, ...],
) -> None:
    """Require mapped metric inputs without rejecting legitimate grouping dimensions."""
    for mapping in mappings:
        if mapping.status is RequestedMetricStatus.UNAVAILABLE:
            raise ValueError(
                f"Cannot plan for unavailable requested metric: {mapping.requested_label}"
            )
        source_fields = {field_key(field) for field in mapping.source_fields}
        if not source_fields:
            raise ValueError(
                f"Requested metric mapping has no source fields: {mapping.requested_label}"
            )
        if not any(
            source_fields <= {field_key(field) for field in step.required_fields} for step in steps
        ):
            fields = ", ".join(mapping.source_fields)
            raise ValueError(
                f"Plan steps omit source fields for requested metric "
                f"{mapping.requested_label}: {fields}"
            )


def current_plan_step(state: AgentState, plan: AnalysisPlan) -> PlanStep:
    step_index = state.get("current_step_index", 0)
    if step_index < 0 or step_index >= len(plan.steps):
        raise ValueError("Current plan step is outside the approved plan")
    return plan.steps[step_index]


def bind_tool_request_to_step(
    request: SQLToolRequestDraft | StatisticalToolRequestDraft,
    *,
    step: PlanStep,
    plan: AnalysisPlan,
    profile: DataProfile,
    handle: DatasetHandle,
    data_core: TabularDataCore,
) -> BoundToolRequest:

    approved_fields = {field_key(field) for field in step.required_fields}
    if step.expected_tool == "statistical_analysis":
        if not isinstance(request, StatisticalToolRequestDraft):
            raise ValueError("Statistical plan step requires statistical parameters")
        if step.statistical_operation is None:
            raise ValueError("Statistical plan step has no approved operation")
        statistical_operation = StatisticalOperation(step.statistical_operation)
        statistical_request_draft = canonicalize_statistical_fields(
            request.model_dump(mode="json"), profile
        )
        statistical_request_data = {
            **statistical_request_draft,
            "operation": statistical_operation.value,
        }
        if statistical_operation in INFERENTIAL_OPERATIONS:
            family_size = sum(
                plan_step.statistical_operation
                in {operation.value for operation in INFERENTIAL_OPERATIONS}
                for plan_step in plan.steps
            )
            statistical_request_data.update(
                {
                    "alpha": INFERENCE_ALPHA,
                    "multiple_testing_count": max(1, family_size),
                }
            )
        statistical_request = StatisticalRequest.model_validate(statistical_request_data)
        requested_fields = {field_key(field) for field in statistical_request.source_fields}
        if requested_fields != approved_fields:
            raise ValueError(
                "Statistical fields do not match the approved plan step: requested "
                f"{sorted(statistical_request.source_fields)}, approved "
                f"{sorted(step.required_fields)}"
            )
        return BoundToolRequest(
            tool_name="statistical_analysis",
            purpose=step.description,
            statistical_request=statistical_request,
            required_fields=step.required_fields,
        )

    if step.expected_tool != "read_only_sql":
        raise ValueError("Plan step declares an unsupported tool")
    if not isinstance(request, SQLToolRequestDraft):
        raise ValueError("SQL plan step requires a SQL query")
    sql = replace_column_references(request.sql, field_ids(profile))
    inspection = data_core.inspect_query(handle, sql)
    if inspection.has_wildcard:
        raise ValueError("Wildcard projections are not allowed for approved Tool Actions")
    if inspection.unaliased_outputs:
        raise ValueError(
            "Give every calculated output column a short ASCII snake_case alias, for example "
            'AVG("Unit Price") AS avg_unit_price. Missing or invalid aliases: '
            + "; ".join(inspection.unaliased_outputs)
        )

    known_fields = {field_key(field.name) for field in profile.fields}
    referenced_fields = [
        field for field in inspection.referenced_columns if field_key(field) in known_fields
    ]
    if not step.required_fields and inspection.dataset_count_scope != "whole_dataset":
        # Rewriting the SQL cannot fix a step that approves no fields; the plan must be revised.
        read = ", ".join(sorted(referenced_fields)) or "no dataset field"
        raise PlanRepairError(
            f"SQL plan step {step.step_id} lists no required_fields, but its query reads {read}. "
            "List every dataset field a SQL step reads; only an unfiltered whole-dataset "
            "COUNT(*) may list none"
        )
    # Reading fewer approved fields (for example COUNT(*) with one filter) cannot widen scope.
    if not {field_key(field) for field in referenced_fields} <= approved_fields:
        raise ValueError(
            "SQL source fields must come from the approved plan step: the query reads "
            f"{sorted(referenced_fields)}, the step approves {sorted(step.required_fields)}"
        )
    return BoundToolRequest(
        tool_name="read_only_sql",
        purpose=step.description,
        sql=inspection.normalized_sql,
        required_fields=step.required_fields,
    )


def action_signature(request: BoundToolRequest, working_version: int) -> str:
    computation: dict[str, object]
    if request.tool_name == "read_only_sql":
        computation = {"sql": request.sql}
    else:
        computation = {
            "statistical_request": (
                request.statistical_request.reproducible_parameters()
                if request.statistical_request is not None
                else None
            )
        }
    identity = {
        "tool_name": request.tool_name,
        "computation": computation,
        "working_dataset_version": working_version,
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
