"""Bounded views of verified tool evidence for insight and chart model calls."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from tabular_analytics_agent.data import (
    QueryResult,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    DataProfile,
    ToolAction,
    VerifiedInsight,
)
from tabular_analytics_agent.model_gateway import (
    InsightDraftBatch,
)
from tabular_analytics_agent.orchestration.models import (
    AgentState,
)
from tabular_analytics_agent.orchestration.prompts import bounded_prompt_text, json_for_prompt
from tabular_analytics_agent.statistics import (
    StatisticalResult,
)
from tabular_analytics_agent.verification import (
    available_evidence_values,
)

MAX_MODEL_EVIDENCE_VALUES = 200


MAX_MODEL_QUERY_ROWS = 50


MAX_MODEL_EVIDENCE_CHARS = 50_000


MAX_MODEL_SOURCE_FIELDS = 50


MAX_MODEL_ASSUMPTIONS = 50


MAX_MODEL_CAVEATS = 20
_ROW_METRIC = re.compile(r"^row\[(\d+)\]\.")


def insight_evidence_catalog(state: AgentState, profile: DataProfile) -> list[dict[str, Any]]:
    pii_fields = {field.casefold() for field in profile.pii_candidates}
    candidates: list[tuple[ToolAction, QueryResult | StatisticalResult, tuple[str, ...]]] = []
    for raw_action in state.get("tool_actions", []):
        action = ToolAction.model_validate(raw_action)
        if action.status is not ActionStatus.SUCCEEDED:
            continue
        result = evidence_result_for_action(state, action)
        source_fields = (
            result.source_fields
            if isinstance(result, StatisticalResult)
            else tuple(str(value) for value in action.inputs.get("required_fields", ()))
        )
        if any(field.casefold() in pii_fields for field in source_fields):
            continue
        candidates.append((action, result, source_fields))

    candidates.sort(key=lambda item: not isinstance(item[1], StatisticalResult))
    catalog: list[dict[str, Any]] = []
    remaining_values = MAX_MODEL_EVIDENCE_VALUES
    remaining_chars = MAX_MODEL_EVIDENCE_CHARS
    for action, result, source_fields in candidates:
        if remaining_chars < 500:
            break
        raw_values = available_evidence_values(result)
        omission_reason = None
        if isinstance(result, QueryResult):
            # A long ranking keeps its first rows, in query order, within the value budget.
            row_limit = min(
                MAX_MODEL_QUERY_ROWS,
                max(0, (remaining_values - 1) // max(1, len(result.columns))),
            )
            if result.row_count > row_limit:
                raw_values = tuple(
                    value
                    for value in raw_values
                    if (index := _row_index(value.metric)) is None or index < row_limit
                )
                omission_reason = (
                    f"Only the first {row_limit} of {result.row_count} result rows are "
                    "included, in query order."
                )
        elif len(raw_values) > remaining_values:
            omission_reason = (
                "Evidence omitted because the bounded model-evidence budget was exhausted."
            )
        values = (
            []
            if omission_reason and isinstance(result, StatisticalResult)
            else [value.model_dump(mode="json") for value in raw_values]
        )
        assumptions = (
            [
                {
                    "name": bounded_prompt_text(check.name),
                    "status": check.status.value,
                    "message": bounded_prompt_text(check.message),
                }
                for check in result.assumptions[:MAX_MODEL_ASSUMPTIONS]
            ]
            if isinstance(result, StatisticalResult)
            else []
        )
        caveats = (
            [bounded_prompt_text(value) for value in result.warnings[:MAX_MODEL_CAVEATS]]
            if isinstance(result, StatisticalResult)
            else []
        )
        entry: dict[str, Any] = {
            "plan_step_id": bounded_prompt_text(str(action.inputs.get("plan_step_id", ""))),
            "tool_name": action.tool_name,
            "group_by_columns": (
                list(result.group_by_columns) if isinstance(result, QueryResult) else []
            ),
            "source_fields": [
                bounded_prompt_text(value) for value in source_fields[:MAX_MODEL_SOURCE_FIELDS]
            ],
            "source_fields_omitted_count": max(0, len(source_fields) - MAX_MODEL_SOURCE_FIELDS),
            "values": values,
            "values_omitted_reason": omission_reason,
            "assumptions": assumptions,
            "assumptions_omitted_count": max(
                0,
                len(result.assumptions) - MAX_MODEL_ASSUMPTIONS,
            )
            if isinstance(result, StatisticalResult)
            else 0,
            "caveats": caveats,
            "caveats_omitted_count": max(
                0,
                len(result.warnings) - MAX_MODEL_CAVEATS,
            )
            if isinstance(result, StatisticalResult)
            else 0,
        }
        serialized_size = len(json_for_prompt(entry))
        if serialized_size > remaining_chars and values:
            entry["values"] = []
            entry["values_omitted_reason"] = (
                "Evidence omitted because the bounded model-prompt budget was exhausted."
            )
            serialized_size = len(json_for_prompt(entry))
        if serialized_size > remaining_chars:
            continue
        remaining_values -= len(entry["values"])
        remaining_chars -= serialized_size
        catalog.append(entry)
    return catalog


def _row_index(metric: str) -> int | None:
    match = _ROW_METRIC.match(metric)
    return int(match.group(1)) if match else None


def evidence_result_for_action(
    state: AgentState, action: ToolAction
) -> QueryResult | StatisticalResult:
    output_ref = action.output_ref or ""
    if output_ref.startswith("query-result:"):
        result_id = output_ref.removeprefix("query-result:")
        for value in state.get("query_results", []):
            query_result = QueryResult.model_validate(value)
            if str(query_result.query_id) == result_id:
                return query_result
    if output_ref.startswith("statistical-result:"):
        result_id = output_ref.removeprefix("statistical-result:")
        for value in state.get("statistical_results", []):
            statistical_result = StatisticalResult.model_validate(value)
            if str(statistical_result.result_id) == result_id:
                return statistical_result
    raise ValueError("Tool Action output does not resolve to deterministic evidence")


def unknown_insight_metrics(
    batch: InsightDraftBatch,
    actions: dict[str, ToolAction],
    state: AgentState,
) -> list[str]:
    """Return draft metric identifiers that do not exist in their step's evidence."""
    unknown: set[str] = set()
    for draft in batch.insights:
        action = actions.get(draft.plan_step_id)
        if action is None:
            continue
        known = {
            value.metric
            for value in available_evidence_values(evidence_result_for_action(state, action))
        }
        assertion = draft.assertion
        referenced = {
            metric
            for metric in (assertion.left_metric, assertion.right_metric, *draft.evidence_metrics)
            if metric
        }
        unknown |= referenced - known
    return sorted(unknown)


def chart_source_for_verified_insight(state: AgentState) -> tuple[QueryResult, ToolAction]:
    actions = {
        action.action_id: action
        for action in (
            ToolAction.model_validate(payload) for payload in state.get("tool_actions", [])
        )
        if action.tool_name == "read_only_sql"
        and action.status is ActionStatus.SUCCEEDED
        and action.output_ref
    }
    results = {
        result.query_id: result
        for result in (
            QueryResult.model_validate(payload) for payload in state.get("query_results", [])
        )
    }
    for insight_payload in state.get("verified_insights", []):
        insight = VerifiedInsight.model_validate(insight_payload)
        for action_id in insight.evidence.tool_action_ids:
            action = actions.get(action_id)
            if action is None or action.output_ref is None:
                continue
            reference_prefix = "query-result:"
            if not action.output_ref.startswith(reference_prefix):
                continue
            try:
                query_id = UUID(action.output_ref.removeprefix(reference_prefix))
            except ValueError:
                continue
            result = results.get(query_id)
            if result is not None:
                return result, action
    raise ValueError("No Verified Insight is backed by a chartable Query Result")


def chart_result_metadata(result: QueryResult, profile: DataProfile) -> dict[str, Any]:
    profile_fields = {field.name: field for field in profile.fields}
    return {
        "row_count": result.row_count,
        "truncated": result.truncated,
        "columns": [
            {
                "name": column.name,
                "data_type": column.data_type,
                "semantic_kind": (
                    profile_fields[column.name].kind.value
                    if column.name in profile_fields
                    else "derived"
                ),
                "source_unique_count": (
                    profile_fields[column.name].unique_count
                    if column.name in profile_fields
                    else None
                ),
            }
            for column in result.columns
        ],
    }
