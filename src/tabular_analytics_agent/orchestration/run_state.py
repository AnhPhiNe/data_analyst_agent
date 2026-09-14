"""Agent state transitions for failures, refusals, repairs, traces, and the run budget."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tabular_analytics_agent.data import (
    QueryExecutionError,
    UnsafeQueryError,
)
from tabular_analytics_agent.domain import (
    AnalysisPlan,
    ExecutionBudget,
)
from tabular_analytics_agent.model_gateway import (
    ModelCallTrace,
    ModelGatewayError,
    ModelOutputValidationError,
    ModelProviderError,
)
from tabular_analytics_agent.orchestration.models import (
    AgentRunStatus,
    AgentState,
    RefusalCode,
)


def append_trace(state: AgentState, trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [*state.get("model_traces", []), trace]


def with_failure_trace(
    update: AgentState,
    state: AgentState,
    error: Exception,
    trace: ModelCallTrace | None = None,
) -> AgentState:
    """Keep the model call behind a failed or retried step visible in the audit trail."""
    if trace is None and isinstance(error, ModelOutputValidationError):
        trace = error.trace
    if trace is None:
        return update
    failed = trace.model_copy(update={"error": safe_error(error)})
    return {**update, "model_traces": append_trace(state, failed.model_dump(mode="json"))}


def failed_state(state: AgentState, error: Exception, now: datetime) -> AgentState:
    return {
        "status": AgentRunStatus.FAILED.value,
        "error": safe_error(error),
        "error_kind": "provider" if isinstance(error, ModelProviderError) else "analysis",
        **pause_execution_budget(state, now),
    }


def typed_refusal_state(
    state: AgentState,
    reason: str,
    now: datetime,
    *,
    code: RefusalCode = RefusalCode.UNAVAILABLE_METRIC,
) -> AgentState:
    """Persist a typed refusal without reclassifying ordinary failures."""
    if not reason.strip():
        raise ValueError("typed refusal requires a non-empty reason")
    return {
        "status": AgentRunStatus.REFUSED.value,
        "error": "",
        "error_kind": "unsupported_request",
        "refusal_code": code.value,
        "refusal_reason": reason,
        **pause_execution_budget(state, now),
    }


def retry_or_fail(state: AgentState, error: Exception, now: datetime) -> AgentState:
    repairs = state.get("tool_repair_count", 0) + 1
    plan = AnalysisPlan.model_validate(state["plan"])
    if repairs <= plan.budget.max_repairs_per_action:
        return {
            "tool_repair_count": repairs,
            "status": AgentRunStatus.RETRYING_TOOL.value,
            "error": safe_error(error),
        }
    terminal: AgentState = {
        "tool_repair_count": repairs,
        "status": AgentRunStatus.FAILED.value,
        "error": (
            "Tool repair budget exceeded "
            f"(max_repairs_per_action={plan.budget.max_repairs_per_action}): "
            f"{safe_error(error)}"
        ),
    }
    terminal.update(pause_execution_budget(state, now))
    return terminal


def safe_error(error: Exception) -> str:
    if isinstance(error, (ModelGatewayError, QueryExecutionError, UnsafeQueryError, ValueError)):
        return str(error)
    return type(error).__name__


def run_budget_error(budget: ExecutionBudget) -> ValueError:
    return ValueError(
        "Analysis Plan active run-time budget exceeded "
        f"(run_timeout_seconds={budget.run_timeout_seconds})"
    )


def active_run_seconds(state: AgentState, now: datetime) -> float:
    elapsed = state.get("active_run_seconds", 0.0)
    segment_started_at = state.get("active_segment_started_at", "")
    if segment_started_at:
        elapsed += max(0.0, (now - datetime.fromisoformat(segment_started_at)).total_seconds())
    return elapsed


def pause_execution_budget(state: AgentState, now: datetime) -> AgentState:
    return {
        "active_run_seconds": active_run_seconds(state, now),
        "active_segment_started_at": "",
    }


def resume_execution_budget(now: datetime) -> AgentState:
    return {"active_segment_started_at": now.isoformat()}


def remaining_budget_seconds(
    state: AgentState,
    budget: ExecutionBudget,
    now: datetime,
) -> float:
    return max(0.0, budget.run_timeout_seconds - active_run_seconds(state, now))


def model_call_timeout_seconds(
    state: AgentState,
    budget: ExecutionBudget,
    now: datetime,
) -> float:
    return max(
        0.000_001,
        min(
            float(budget.model_call_timeout_seconds),
            remaining_budget_seconds(state, budget, now),
        ),
    )


def run_budget_exceeded(
    state: AgentState,
    budget: ExecutionBudget,
    now: datetime,
) -> bool:
    return active_run_seconds(state, now) >= budget.run_timeout_seconds
