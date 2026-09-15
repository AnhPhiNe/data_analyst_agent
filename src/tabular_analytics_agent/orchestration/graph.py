"""LangGraph state machine for the local single analytical agent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from tabular_analytics_agent.data import (
    DatasetHandle,
    QueryExecutionError,
    QueryResult,
    TabularDataCore,
    UnsafeQueryError,
)
from tabular_analytics_agent.domain import (
    ActionStatus,
    AnalysisPlan,
    AnalyticalGoal,
    ArtifactType,
    ChartIntent,
    DataProfile,
    ExecutionBudget,
    InsightAssertion,
    InsightOperator,
    PlanStatus,
    PlanStep,
    SemanticAnnotation,
    ToolAction,
    VerificationStatus,
)
from tabular_analytics_agent.model_gateway import (
    ChartIntentDraft,
    GoalInterpretation,
    InsightDraft,
    InsightDraftBatch,
    ModelCallTrace,
    ModelGateway,
    ModelGatewayError,
    ModelProviderError,
    ModelTask,
    PlanDraft,
    RequestedMetricStatus,
    SemanticAnnotationDraft,
    SQLToolRequestDraft,
    StatisticalToolRequestDraft,
    StructuredModelRequest,
    validation_error_detail,
)
from tabular_analytics_agent.orchestration.binding import (
    action_signature,
    bind_tool_request_to_step,
    canonicalize_requested_metric_mappings,
    current_plan_step,
    unavailable_metric_clarification_question,
    unavailable_metric_refusal_reason,
    validate_plan_metric_grounding,
)
from tabular_analytics_agent.orchestration.evidence_catalog import (
    chart_result_metadata,
    chart_source_for_verified_insight,
    evidence_result_for_action,
    insight_evidence_catalog,
    unasserted_evidence_metrics,
    unknown_insight_metrics,
)
from tabular_analytics_agent.orchestration.field_ids import (
    PlanRepairError,
    canonicalize_profile_fields,
    field_key,
    resolve_column,
    result_column_lookup,
)
from tabular_analytics_agent.orchestration.models import (
    AgentRunRequest,
    AgentRunStatus,
    AgentState,
    ApprovalDecision,
    BoundToolRequest,
    RefusalCode,
)
from tabular_analytics_agent.orchestration.prompts import (
    SYSTEM_INSTRUCTION,
    json_for_prompt,
    profile_prompt,
    tool_payload_guidance,
)
from tabular_analytics_agent.orchestration.run_state import (
    append_trace,
    failed_state,
    model_call_timeout_seconds,
    pause_execution_budget,
    remaining_budget_seconds,
    resume_execution_budget,
    retry_or_fail,
    run_budget_error,
    run_budget_exceeded,
    safe_error,
    typed_refusal_state,
    with_failure_trace,
)
from tabular_analytics_agent.statistics import (
    InsufficientSampleError,
    StatisticalAnalysisError,
    StatisticalRequest,
    StatisticalResult,
    StatisticalTool,
)
from tabular_analytics_agent.verification import (
    InsightPublication,
    publish_insight,
    reject_insight_draft,
    verify_query_evidence,
)
from tabular_analytics_agent.visualization import (
    SUPPORTED_FORMATTING_KEYS,
    ChartValidationError,
    make_query_result_reference,
    render_chart,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AgentOrchestrator:
    """Small application-facing wrapper around a checkpointed LangGraph."""

    def __init__(
        self,
        model_gateway: ModelGateway,
        data_core: TabularDataCore,
        *,
        checkpointer: BaseCheckpointSaver[str],
        clock: Callable[[], datetime] | None = None,
        execution_budget: ExecutionBudget | None = None,
        send_sample_values: bool = True,
    ) -> None:
        self._clock = clock or _utc_now
        self._execution_budget = execution_budget or ExecutionBudget()
        self._graph = build_agent_graph(
            model_gateway,
            data_core,
            checkpointer=checkpointer,
            clock=self._clock,
            execution_budget=self._execution_budget,
            send_sample_values=send_sample_values,
        )

    def start(self, request: AgentRunRequest) -> AgentState:
        started_at = self._clock().isoformat()
        initial: AgentState = {
            "session_id": request.session_id,
            "run_id": str(uuid4()),
            "run_started_at": started_at,
            "active_run_seconds": 0.0,
            "active_segment_started_at": started_at,
            "user_request": request.user_request,
            "dataset_handle": request.dataset_handle.model_dump(mode="json"),
            "data_profile": request.data_profile.model_dump(mode="json"),
            "current_step_index": 0,
            "query_results": [],
            "statistical_results": [],
            "tool_actions": [],
            "attempted_action_signatures": [],
            "tool_repair_count": 0,
            "model_traces": [],
            "verified_insights": [],
            "unsupported_claims": [],
            "chart_renders": [],
            "artifact_error": "",
            "requested_metric_mappings": [],
            "refusal_code": "",
            "refusal_reason": "",
            # A new request must not inherit per-run outputs or errors from an earlier request;
            # confirmed semantic annotations intentionally persist within the session.
            "error": "",
            "error_kind": "",
            "clarification_question": "",
            "proposed_annotations": [],
            "query_result": {},
            "statistical_result": {},
            "answered_from_profile": False,
            "plan_repair_count": 0,
            "status": AgentRunStatus.INTERPRETING.value,
        }
        return cast(
            AgentState,
            self._graph.invoke(initial, config=_thread_config(request.session_id)),
        )

    def resume(self, thread_id: str, decision: bool | dict[str, object]) -> AgentState:
        payload: dict[str, object] = (
            {"approved": decision} if isinstance(decision, bool) else decision
        )
        return cast(
            AgentState,
            self._graph.invoke(Command(resume=payload), config=_thread_config(thread_id)),
        )

    def get_state(self, thread_id: str) -> AgentState:
        snapshot = self._graph.get_state(_thread_config(thread_id))
        return cast(AgentState, snapshot.values)

    def run_history(self, thread_id: str) -> tuple[str, ...]:
        """Graph nodes of the latest run, oldest first, read back from the checkpoint history."""
        snapshots = list(self._graph.get_state_history(_thread_config(thread_id)))
        if not snapshots:
            return ()
        run_id = snapshots[0].values.get("run_id")
        nodes = [
            str(snapshot.next[0])
            for snapshot in snapshots
            if snapshot.next
            and snapshot.next[0] != START
            and snapshot.values.get("run_id") == run_id
        ]
        return tuple(reversed(nodes))


def _draft_claim_text(draft: InsightDraft) -> str:
    assertion = draft.assertion
    right = assertion.right_metric or ""
    return f"{assertion.left_metric} {assertion.operator.value} {right}".strip()


def build_agent_graph(
    model_gateway: ModelGateway,
    data_core: TabularDataCore,
    *,
    checkpointer: BaseCheckpointSaver[str],
    clock: Callable[[], datetime] = _utc_now,
    execution_budget: ExecutionBudget | None = None,
    send_sample_values: bool = True,
) -> Any:
    """Build one bounded graph; provider and analytical tools remain injected adapters."""
    budget = execution_budget or ExecutionBudget()

    def interpret_request(state: AgentState) -> AgentState:
        profile = DataProfile.model_validate(state["data_profile"])
        request = StructuredModelRequest(
            task=ModelTask.SEMANTIC_INTERPRETATION,
            prompt=(
                "<untrusted_user_request>"
                f"{json_for_prompt(state['user_request'])}"
                "</untrusted_user_request>\n"
                f"{profile_prompt(profile, include_sample_values=send_sample_values)}\n"
                "Confirmed semantic annotations: "
                f"{json_for_prompt(state.get('semantic_annotations', []))}\n"
                "Interpret the analytical goal using the original field names exactly as supplied. "
                "Default to an empty semantic_annotations list. Never infer or assign a business "
                "definition, unit, currency, geography, or domain meaning from a column name, file "
                "name, or value sample. Do not create an annotation merely to restate "
                "a field name. "
                "Create a semantic annotation only when a genuine ambiguity directly changes the "
                "required calculation and blocks a responsible answer; otherwise continue and let "
                "the later plan record the uncertainty as a caveat. Ask only for information "
                "needed by this request. A request to rank or judge entities without naming the "
                "measure, with a superlative such as best or top in any language, needs "
                "clarification when the Data Profile has two or more numeric fields that could "
                "each define the ranking: return a clarification_question naming those candidate "
                "fields. When the request names the measure it ranks by, do not ask. If "
                "semantic_annotations is non-empty, "
                "clarification_question must be a non-empty question asking the user to choose or "
                "confirm the exact blocking interpretation. Set "
                "answer_from_profile to true only when the request can be answered entirely from "
                "the Data Profile: column names, field kinds, row count, duplicate row count, "
                "missing values, unique "
                "counts, warnings, or whole-column descriptive statistics (count, mean, standard "
                "deviation, minimum, quartiles, median, maximum). Use false for filtered, grouped, "
                "or derived calculations. If the request names a column that does not exist, "
                "return a "
                "clarification_question listing the available columns. "
                "For every metric the user explicitly requests, add one requested_metric_mappings "
                "entry with the user's original requested_label and a status: direct when "
                "source_fields are the exact fields that measure it, derived when source_fields "
                "list every field used and derivation states a reproducible formula, or "
                "unavailable when no field measures it (empty source_fields, optional reason). "
                "Aggregating one field (average, sum, count, minimum, or maximum) is direct; use "
                "derived only for a formula that combines or transforms fields. "
                "Never map a requested metric to a field that measures something else, even when "
                "it is the closest available field. When a metric is unavailable or ambiguous and "
                "the user could resolve it, also return a clarification_question naming that "
                "metric and the closest available fields. Use an unavailable mapping without a "
                "clarification question only for an explicitly unsupported capability. Grouping "
                "dimensions and dataset-level profile facts (row count, duplicate row count, "
                "missing-value counts) are not metrics and need no mapping. Counting rows or "
                "records is a row count, not a metric: a request to count the dataset's records, "
                "under whatever noun the dataset uses for them, needs no mapping and no identifier "
                "field. "
                "Return "
                "null clarification_question in all other cases."
            ),
            response_schema=GoalInterpretation,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt_template_version="semantic-v15",
            timeout_seconds=model_call_timeout_seconds(state, budget, clock()),
        )
        trace: ModelCallTrace | None = None
        try:
            response = model_gateway.generate_structured(request)
            trace = response.trace
            metric_mappings = canonicalize_requested_metric_mappings(
                profile, response.output.requested_metric_mappings
            )
        except ModelGatewayError as exc:
            return with_failure_trace(failed_state(state, exc, clock()), state, exc, trace)
        except (ValidationError, ValueError) as exc:
            return with_failure_trace(failed_state(state, exc, clock()), state, exc, trace)
        if run_budget_exceeded(state, budget, clock()):
            return failed_state(state, run_budget_error(budget), clock())
        interpretation = response.output

        unavailable_metric = any(
            mapping.status is RequestedMetricStatus.UNAVAILABLE for mapping in metric_mappings
        )
        derived_metric_requested = any(
            mapping.status is RequestedMetricStatus.DERIVED for mapping in metric_mappings
        )
        clarification_question = (
            unavailable_metric_clarification_question(metric_mappings, profile)
            if unavailable_metric
            else interpretation.clarification_question or ""
        )
        clarification_requested = bool(
            interpretation.semantic_annotations or clarification_question
        )
        if clarification_requested:
            status = AgentRunStatus.AWAITING_SEMANTIC_REVIEW.value
        elif interpretation.answer_from_profile and not derived_metric_requested:
            # Structural questions are answered from the deterministic Data Profile, not tools.
            status = AgentRunStatus.COMPLETED.value
        else:
            status = AgentRunStatus.PLANNING.value
        update: AgentState = {
            "goal": {
                "text": interpretation.goal_text,
                "family": interpretation.goal_family.value,
            },
            "requested_metric_mappings": [item.model_dump(mode="json") for item in metric_mappings],
            "proposed_annotations": [
                item.model_dump(mode="json") for item in interpretation.semantic_annotations
            ],
            "clarification_question": clarification_question,
            "answered_from_profile": status == AgentRunStatus.COMPLETED,
            "model_traces": append_trace(state, response.trace.model_dump(mode="json")),
            "status": status,
            "refusal_code": "",
            "refusal_reason": "",
            "error": "",
            "error_kind": "",
        }
        if status != AgentRunStatus.PLANNING:
            update.update(pause_execution_budget(state, clock()))
        return update

    def review_semantics(state: AgentState) -> AgentState:
        profile = DataProfile.model_validate(state["data_profile"])
        decision_payload = interrupt(
            {
                "type": "semantic_review",
                "question": "Confirm or edit the proposed field meanings before planning.",
                "clarification_question": state.get("clarification_question"),
                "proposals": state.get("proposed_annotations", []),
            }
        )
        try:
            decision = ApprovalDecision.model_validate(decision_payload)
            if decision.dismissed:
                return {
                    "status": AgentRunStatus.REJECTED.value,
                    "error": "The question was dismissed. Ask a new question to continue.",
                    "proposed_annotations": [],
                    "clarification_question": "",
                    **pause_execution_budget(state, clock()),
                }
            if not decision.approved:
                corrected_request = decision.corrected_request or decision.revision_request
                if corrected_request:
                    # A corrected analytical request must go through interpretation again so an
                    # earlier unavailable/substitute mapping cannot leak into the new plan.
                    return {
                        "user_request": corrected_request,
                        "goal": {},
                        "requested_metric_mappings": [],
                        "status": AgentRunStatus.INTERPRETING.value,
                        "proposed_annotations": [],
                        "clarification_question": "",
                        "answered_from_profile": False,
                        "refusal_code": "",
                        "refusal_reason": "",
                        "error": "",
                        "error_kind": "",
                        **resume_execution_budget(clock()),
                    }
                return {
                    "status": AgentRunStatus.AWAITING_SEMANTIC_REVIEW.value,
                    "proposed_annotations": [],
                    "clarification_question": (
                        decision.revision_request
                        or decision.reason
                        or "Provide corrected field meanings, or confirm that no "
                        "annotation applies."
                    ),
                    **pause_execution_budget(state, clock()),
                }
            selected = (
                decision.annotations
                if decision.annotations is not None
                else tuple(
                    _annotation_draft(item) for item in state.get("proposed_annotations", [])
                )
            )
            annotation_fields = canonicalize_profile_fields(
                profile, tuple(item.field_name for item in selected)
            )
            if len({field.casefold() for field in annotation_fields}) != len(annotation_fields):
                raise ValueError("A field may have only one confirmed Semantic Annotation")
            annotations = [
                SemanticAnnotation(
                    field_name=field_name,
                    meaning=item.meaning,
                    unit=item.unit,
                    role=item.role,
                    confirmed_by_user=True,
                ).model_dump(mode="json")
                for item, field_name in zip(selected, annotation_fields, strict=True)
            ]
            metric_mappings = canonicalize_requested_metric_mappings(
                profile, state.get("requested_metric_mappings", [])
            )
            refusal_reason = unavailable_metric_refusal_reason(
                metric_mappings,
                has_clarification=False,
            )
            if refusal_reason:
                if selected:
                    # Confirmed annotations may resolve the meaning, but the model must
                    # reinterpret the request before the mapping can become direct or derived.
                    return {
                        "semantic_annotations": annotations,
                        "requested_metric_mappings": [],
                        "status": AgentRunStatus.INTERPRETING.value,
                        "proposed_annotations": [],
                        "clarification_question": "",
                        "answered_from_profile": False,
                        "refusal_code": "",
                        "refusal_reason": "",
                        "error": "",
                        "error_kind": "",
                        **resume_execution_budget(clock()),
                    }
                return {
                    "semantic_annotations": annotations,
                    **typed_refusal_state(state, refusal_reason, clock()),
                }
        except (ValidationError, ValueError) as exc:
            return failed_state(state, exc, clock())
        return {
            "semantic_annotations": annotations,
            "status": AgentRunStatus.PLANNING.value,
            **resume_execution_budget(clock()),
        }

    def create_plan(state: AgentState) -> AgentState:
        profile = DataProfile.model_validate(state["data_profile"])
        if run_budget_exceeded(state, budget, clock()):
            return failed_state(state, run_budget_error(budget), clock())
        request = StructuredModelRequest(
            task=ModelTask.PLAN,
            prompt=(
                f"Analytical goal: {json_for_prompt(state['goal'])}\n"
                "Confirmed annotations: "
                f"{json_for_prompt(state.get('semantic_annotations', []))}\n"
                f"{profile_prompt(profile, include_sample_values=send_sample_values)}\n"
                "Requested metric mappings: "
                f"{json_for_prompt(state.get('requested_metric_mappings', []))}\n"
                "Requested plan revision: "
                f"{json_for_prompt(state.get('error', 'none'))}\n"
                "Create the shortest reproducible plan. Every calculation step must use one "
                "allowlisted tool: read_only_sql or statistical_analysis. Statistical analysis "
                "supports descriptive, correlation, confidence_interval, t_test, mann_whitney, "
                "chi_square, anova, kruskal_wallis, linear_regression, and logistic_regression. "
                "When the goal asks whether a difference or relationship is statistically "
                "significant or could be due to chance, include a statistical_analysis step; "
                "SQL aggregates alone cannot answer that. "
                "To count rows, list only the fields that filter or group them in "
                "required_fields; counting rows needs no identifier field. "
                "SQL steps run independently and cannot read an earlier step's result, so a "
                "question that uses one result to choose rows for another, such as the "
                "top-ranked groups, is a single SQL step with a CTE or subquery. "
                "Reference only listed fields. SQL reads rows of the dataset table only; schema "
                "catalogs such as information_schema are unavailable. Set requires_approval to "
                "true only for a step the user should review before it runs; ordinary read-only "
                "calculations use false. Never let a different field stand in for a requested "
                "measure that the dataset does not contain. Every direct or derived requested "
                "metric mapping must have all of its source_fields in the required_fields of its "
                "calculation step. Additional required_fields are allowed for grouping or "
                "filtering dimensions; do not treat those dimensions as substitutions."
            ),
            response_schema=PlanDraft,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt_template_version="plan-v9",
            timeout_seconds=model_call_timeout_seconds(state, budget, clock()),
        )
        trace: ModelCallTrace | None = None
        try:
            response = model_gateway.generate_structured(request)
            trace = response.trace
            step_count = len(response.output.steps)
            if step_count > budget.max_tool_actions:
                # Every step needs at least one Tool Action, so a longer plan can never finish.
                raise PlanRepairError(
                    f"The plan has {step_count} steps but the execution budget allows "
                    f"{budget.max_tool_actions} Tool Actions; combine steps so there are at most "
                    f"{budget.max_tool_actions}"
                )
            goal = AnalyticalGoal.model_validate(state["goal"])
            metric_mappings = canonicalize_requested_metric_mappings(
                profile, state.get("requested_metric_mappings", [])
            )
            canonical_steps = tuple(
                PlanStep(
                    step_id=step.step_id,
                    description=step.description,
                    expected_tool=step.expected_tool,
                    statistical_operation=(
                        step.statistical_operation.value
                        if step.statistical_operation is not None
                        else None
                    ),
                    required_fields=canonicalize_profile_fields(profile, step.required_fields),
                    intended_output=step.intended_output,
                    caveats=step.caveats,
                    requires_approval=step.requires_approval,
                )
                for step in response.output.steps
            )
            validate_plan_metric_grounding(canonical_steps, metric_mappings)
            plan = AnalysisPlan(
                plan_id=uuid4(),
                goal=goal,
                steps=canonical_steps,
                budget=budget,
                status=PlanStatus.PROPOSED,
            )
            if run_budget_exceeded(state, plan.budget, clock()):
                raise run_budget_error(plan.budget)
        except (ModelGatewayError, ValidationError, ValueError) as exc:
            repairs = state.get("plan_repair_count", 0)
            if isinstance(exc, PlanRepairError) and repairs < budget.max_repairs_per_action:
                # A misspelled or missing field list is repaired by replanning with the exact error.
                feedback = (
                    f"{exc}. Use field names exactly as listed in the dataset metadata; if a "
                    "requested measure does not exist, do not substitute another field."
                )
                return with_failure_trace(
                    {
                        "status": AgentRunStatus.PLANNING.value,
                        "error": feedback,
                        "plan_repair_count": repairs + 1,
                    },
                    state,
                    exc,
                    trace,
                )
            return with_failure_trace(failed_state(state, exc, clock()), state, exc, trace)
        update: AgentState = {
            "plan": plan.model_dump(mode="json"),
            "current_step_index": 0,
            "model_traces": append_trace(state, response.trace.model_dump(mode="json")),
            "error": "",
        }
        if any(step.requires_approval for step in plan.steps):
            return {
                **update,
                "status": AgentRunStatus.AWAITING_PLAN_APPROVAL.value,
                **pause_execution_budget(state, clock()),
            }
        # Read-only plans run immediately; only steps flagged for review pause for approval.
        approved_plan = plan.model_copy(update={"status": PlanStatus.APPROVED})
        return {
            **update,
            "plan": approved_plan.model_dump(mode="json"),
            "status": AgentRunStatus.REQUESTING_TOOL.value,
        }

    def approve_plan(state: AgentState) -> AgentState:
        decision_payload = interrupt(
            {
                "type": "plan_approval",
                "question": "Approve this analysis plan before any tool is executed?",
                "plan": state["plan"],
            }
        )
        try:
            decision = ApprovalDecision.model_validate(decision_payload)
        except ValidationError as exc:
            return failed_state(state, exc, clock())
        if not decision.approved:
            if decision.revision_request:
                return {
                    "status": AgentRunStatus.PLANNING.value,
                    "error": decision.revision_request,
                    **resume_execution_budget(clock()),
                }
            return {
                "status": AgentRunStatus.REJECTED.value,
                "error": decision.reason or "Analysis plan was rejected by the user.",
                **pause_execution_budget(state, clock()),
            }
        approved_plan = AnalysisPlan.model_validate(state["plan"]).model_copy(
            update={"status": PlanStatus.APPROVED}
        )
        return {
            "plan": approved_plan.model_dump(mode="json"),
            "status": AgentRunStatus.REQUESTING_TOOL.value,
            **resume_execution_budget(clock()),
        }

    def request_tool(state: AgentState) -> AgentState:
        handle = DatasetHandle.model_validate(state["dataset_handle"])
        profile = DataProfile.model_validate(state["data_profile"])
        plan = AnalysisPlan.model_validate(state["plan"])
        if run_budget_exceeded(state, plan.budget, clock()):
            return failed_state(state, run_budget_error(plan.budget), clock())
        try:
            step = current_plan_step(state, plan)
        except ValueError as exc:
            return failed_state(state, exc, clock())
        request = StructuredModelRequest(
            task=ModelTask.TOOL_REQUEST,
            prompt=(
                "Current approved plan step: "
                f"{json_for_prompt(step.model_dump(mode='json'))}\n"
                f"{profile_prompt(profile, include_sample_values=send_sample_values)}\n"
                "Previous tool error: <untrusted_tool_error>"
                f"{json_for_prompt(state.get('error', 'none'))}"
                "</untrusted_tool_error>\n"
                "Return only the calculation payload for this already-approved step; do not repeat "
                "its tool name, operation, purpose, or allowed-column list. "
                f"{tool_payload_guidance(step, handle)}"
            ),
            response_schema=(
                SQLToolRequestDraft
                if step.expected_tool == "read_only_sql"
                else StatisticalToolRequestDraft
            ),
            system_instruction=SYSTEM_INSTRUCTION,
            prompt_template_version="tool-request-v13",
            timeout_seconds=model_call_timeout_seconds(state, plan.budget, clock()),
        )
        trace: ModelCallTrace | None = None
        try:
            response = model_gateway.generate_structured(request)
            trace = response.trace
            if run_budget_exceeded(state, plan.budget, clock()):
                return failed_state(state, run_budget_error(plan.budget), clock())
            tool_request = bind_tool_request_to_step(
                cast(SQLToolRequestDraft | StatisticalToolRequestDraft, response.output),
                step=step,
                plan=plan,
                profile=profile,
                handle=handle,
                data_core=data_core,
            )
            signature = action_signature(tool_request, handle.working_dataset_version)
            if signature in state.get("attempted_action_signatures", []):
                raise ValueError("Repeated identical Tool Action was rejected")
        except ModelProviderError as exc:
            # Transport retries belong to the gateway, not the SQL repair loop.
            return failed_state(state, exc, clock())
        except PlanRepairError as exc:
            # The approved plan itself is defective, so replan instead of retrying the SQL.
            plan_repairs = state.get("plan_repair_count", 0)
            if plan_repairs >= plan.budget.max_repairs_per_action:
                return with_failure_trace(failed_state(state, exc, clock()), state, exc, trace)
            return with_failure_trace(
                {
                    "status": AgentRunStatus.PLANNING.value,
                    "error": str(exc),
                    "plan_repair_count": plan_repairs + 1,
                    "tool_repair_count": 0,
                },
                state,
                exc,
                trace,
            )
        except (
            ModelGatewayError,
            QueryExecutionError,
            UnsafeQueryError,
            ValidationError,
            ValueError,
        ) as exc:
            return with_failure_trace(retry_or_fail(state, exc, clock()), state, exc, trace)
        return {
            "tool_request": tool_request.model_dump(mode="json"),
            "model_traces": append_trace(state, response.trace.model_dump(mode="json")),
            "status": AgentRunStatus.REQUESTING_TOOL.value,
            "error": "",
        }

    def execute_tool(state: AgentState) -> AgentState:
        handle = DatasetHandle.model_validate(state["dataset_handle"])
        profile = DataProfile.model_validate(state["data_profile"])
        plan = AnalysisPlan.model_validate(state["plan"])
        step = current_plan_step(state, plan)
        tool_request = BoundToolRequest.model_validate(state["tool_request"])
        attempted = [
            *state.get("attempted_action_signatures", []),
            action_signature(tool_request, handle.working_dataset_version),
        ]
        if run_budget_exceeded(state, plan.budget, clock()):
            return failed_state(state, run_budget_error(plan.budget), clock())
        if len(state.get("tool_actions", [])) >= plan.budget.max_tool_actions:
            return failed_state(
                state,
                ValueError(
                    f"Tool Action budget exceeded (max_tool_actions={plan.budget.max_tool_actions})"
                ),
                clock(),
            )
        action_id = uuid4()
        action_inputs = {
            "plan_step_id": step.step_id,
            **tool_request.model_dump(mode="json"),
        }
        remaining_run_seconds = remaining_budget_seconds(state, plan.budget, clock())
        effective_tool_timeout = min(
            float(plan.budget.tool_timeout_seconds),
            remaining_run_seconds,
        )
        if effective_tool_timeout <= 0:
            return failed_state(state, run_budget_error(plan.budget), clock())
        try:
            if tool_request.tool_name == "statistical_analysis":
                statistical_request = StatisticalRequest.model_validate(
                    tool_request.statistical_request
                )
                statistical_output = StatisticalTool(data_core).execute(
                    handle=handle,
                    profile=profile,
                    request=statistical_request,
                    timeout_seconds=effective_tool_timeout,
                )
                result: QueryResult | StatisticalResult = statistical_output.result
                action = statistical_output.action.model_copy(
                    update={
                        "action_id": action_id,
                        "inputs": {
                            **statistical_output.action.inputs,
                            "plan_step_id": step.step_id,
                            "tool_name": tool_request.tool_name,
                            "purpose": tool_request.purpose,
                            "required_fields": list(tool_request.required_fields),
                        },
                        "retry_count": state.get("tool_repair_count", 0),
                    }
                )
                verification = action.verification_results[0]
            else:
                if tool_request.sql is None:
                    raise ValueError("SQL Tool Action requires SQL")
                result = data_core.query(
                    handle,
                    tool_request.sql,
                    timeout_seconds=effective_tool_timeout,
                )
                verification = verify_query_evidence(
                    profile=profile,
                    result=result,
                    source_fields=tool_request.required_fields,
                    current_working_dataset_version=handle.working_dataset_version,
                )
                action = ToolAction(
                    action_id=action_id,
                    tool_name=tool_request.tool_name,
                    schema_version="1",
                    working_dataset_version=handle.working_dataset_version,
                    inputs=action_inputs,
                    status=ActionStatus.SUCCEEDED,
                    output_ref=f"query-result:{result.query_id}",
                    retry_count=state.get("tool_repair_count", 0),
                    duration_ms=result.duration_ms,
                    verification_results=(verification,),
                )
        except (QueryExecutionError, StatisticalAnalysisError, UnsafeQueryError, ValueError) as exc:
            failed_action = ToolAction(
                action_id=action_id,
                tool_name=tool_request.tool_name,
                schema_version="1",
                working_dataset_version=handle.working_dataset_version,
                inputs=action_inputs,
                status=ActionStatus.FAILED,
                error=str(exc),
                retry_count=state.get("tool_repair_count", 0),
            )
            updated: AgentState = {
                "tool_actions": [
                    *state.get("tool_actions", []),
                    failed_action.model_dump(mode="json"),
                ],
                "attempted_action_signatures": attempted,
            }
            if isinstance(exc, InsufficientSampleError):
                # Too little data is a responsible refusal with a typed code, not a crash.
                return {
                    **updated,
                    **typed_refusal_state(
                        state,
                        f"Not enough data for a reliable statistical test: {exc}.",
                        clock(),
                        code=RefusalCode.INSUFFICIENT_SAMPLE,
                    ),
                }
            if isinstance(exc, StatisticalAnalysisError):
                terminal: AgentState = {
                    **updated,
                    "status": AgentRunStatus.FAILED.value,
                    "error": f"Unsupported statistical request: {exc}",
                }
                terminal.update(pause_execution_budget(state, clock()))
                return terminal
            return {**updated, **retry_or_fail({**state, **updated}, exc, clock())}

        if run_budget_exceeded(state, plan.budget, clock()):
            budget_error = run_budget_error(plan.budget)
            failed_action = ToolAction(
                action_id=action_id,
                tool_name=tool_request.tool_name,
                schema_version="1",
                working_dataset_version=handle.working_dataset_version,
                inputs=action_inputs,
                status=ActionStatus.FAILED,
                error=str(budget_error),
                retry_count=state.get("tool_repair_count", 0),
                duration_ms=action.duration_ms,
            )
            terminal_state: AgentState = {
                "tool_actions": [
                    *state.get("tool_actions", []),
                    failed_action.model_dump(mode="json"),
                ],
                "attempted_action_signatures": attempted,
                "status": AgentRunStatus.FAILED.value,
                "error": str(budget_error),
            }
            terminal_state.update(pause_execution_budget(state, clock()))
            return terminal_state

        next_step_index = state.get("current_step_index", 0) + 1
        has_next_step = next_step_index < len(plan.steps)
        if verification.status is VerificationStatus.PASSED:
            status = (
                AgentRunStatus.REQUESTING_TOOL.value
                if has_next_step
                else AgentRunStatus.SYNTHESIZING.value
            )
        elif isinstance(result, QueryResult) and result.row_count == 0 and result.filters:
            # A filter that matches nothing usually used a value that is not in the data.
            empty_filter_error = ValueError(
                f"The query returned no rows for filters: {'; '.join(result.filters)}. Copy "
                "filter values exactly from sample_values in the dataset metadata."
            )
            return {
                "tool_actions": [*state.get("tool_actions", []), action.model_dump(mode="json")],
                "attempted_action_signatures": attempted,
                **retry_or_fail(state, empty_filter_error, clock()),
            }
        else:
            status = AgentRunStatus.FAILED.value
        serialized_result = result.model_dump(mode="json")
        update: AgentState = {
            "tool_actions": [*state.get("tool_actions", []), action.model_dump(mode="json")],
            "attempted_action_signatures": attempted,
            "status": status,
        }
        if isinstance(result, QueryResult):
            update["query_result"] = serialized_result
            update["query_results"] = [*state.get("query_results", []), serialized_result]
        else:
            update["statistical_result"] = serialized_result
            update["statistical_results"] = [
                *state.get("statistical_results", []),
                serialized_result,
            ]
        if status == AgentRunStatus.REQUESTING_TOOL:
            update["current_step_index"] = next_step_index
            update["tool_repair_count"] = 0
            update["error"] = ""
        if status == AgentRunStatus.FAILED:
            failed_checks = "; ".join(
                f"{check.name}: {check.message}"
                for check in verification.checks
                if not check.passed
            )
            update["error"] = (
                f"Deterministic verification rejected the tool evidence: {failed_checks}"
            )
        if status == AgentRunStatus.FAILED:
            update.update(pause_execution_budget(state, clock()))
        return update

    def synthesize_insights(state: AgentState) -> AgentState:
        profile = DataProfile.model_validate(state["data_profile"])
        plan = AnalysisPlan.model_validate(state["plan"])
        if run_budget_exceeded(state, plan.budget, clock()):
            return failed_state(state, run_budget_error(plan.budget), clock())
        request = StructuredModelRequest(
            task=ModelTask.INSIGHT_DRAFT,
            prompt=(
                "Analytical goal: "
                f"{json_for_prompt(state['goal'])}\n"
                "Verified Tool Action evidence catalog: "
                f"{json_for_prompt(insight_evidence_catalog(state, profile))}\n"
                "Select structured insight assertions using only exact metric identifiers from "
                "the catalog. Final claim text is rendered deterministically from evidence. "
                "When the goal compares groups, periods, or values, prefer greater_than, "
                "less_than, or equals between the compared metrics; use reports only for a "
                "single value. The reports, positive, negative, and significance operators take "
                "a null right_metric. Create an assertion for every value or comparison the "
                "analytical goal asks for, not only the first one. Columns listed in "
                "group_by_columns label result rows: assert on the measured values of each "
                "group, never on the labels themselves. For a statistical comparison, also "
                "report the estimate of each compared group. Include material caveats; never "
                "infer causation from association."
            ),
            response_schema=InsightDraftBatch,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt_template_version="insight-v6",
            max_output_tokens=2048,
            timeout_seconds=model_call_timeout_seconds(state, plan.budget, clock()),
        )
        trace: ModelCallTrace | None = None
        try:
            actions = {
                str(action.inputs.get("plan_step_id", "")): action
                for action in (
                    ToolAction.model_validate(value) for value in state.get("tool_actions", [])
                )
                if action.status is ActionStatus.SUCCEEDED
            }
            response = model_gateway.generate_structured(request)
            trace = response.trace
            traces = [response.trace]
            unknown_metrics = unknown_insight_metrics(response.output, actions, state)
            if unknown_metrics and budget.max_repairs_per_action > 0:
                # Models can garble long or non-ASCII identifiers; retry once with the exact error.
                response = model_gateway.generate_structured(
                    replace(
                        request,
                        prompt=(
                            f"{request.prompt}\nYour previous draft used metric identifiers "
                            "that are not in the catalog: "
                            f"{json_for_prompt(unknown_metrics)}. Copy every identifier "
                            "character for character from the catalog values."
                        ),
                    )
                )
                trace = response.trace
                traces.append(response.trace)
            if run_budget_exceeded(state, plan.budget, clock()):
                return failed_state(state, run_budget_error(plan.budget), clock())
            annotations = tuple(
                SemanticAnnotation.model_validate(value)
                for value in state.get("semantic_annotations", [])
            )
            current_version = DatasetHandle.model_validate(
                state["dataset_handle"]
            ).working_dataset_version
            publications: list[InsightPublication] = []
            for draft in response.output.insights:
                # A malformed draft is recorded as unsupported instead of voiding the whole run.
                action = actions.get(draft.plan_step_id)
                if action is None:
                    publications.append(
                        reject_insight_draft(
                            claim=_draft_claim_text(draft),
                            reason="Insight draft references an unknown plan step.",
                        )
                    )
                    continue
                try:
                    assertion = draft.validated_assertion()
                except ValueError as exc:
                    publications.append(
                        reject_insight_draft(
                            claim=_draft_claim_text(draft),
                            reason=validation_error_detail(exc),
                        )
                    )
                    continue
                publications.append(
                    publish_insight(
                        assertion=assertion,
                        evidence_metrics=draft.evidence_metrics,
                        caveats=draft.caveats,
                        profile=profile,
                        action=action,
                        result=evidence_result_for_action(state, action),
                        current_working_dataset_version=current_version,
                        semantic_annotations=annotations,
                    )
                )
            # Deterministically report evidence the model left out, so a correct answer is not
            # stated in part.
            for action, result, metric in unasserted_evidence_metrics(
                state, actions, response.output, profile
            ):
                publications.append(
                    publish_insight(
                        assertion=InsightAssertion(
                            operator=InsightOperator.REPORTS, left_metric=metric
                        ),
                        evidence_metrics=(metric,),
                        caveats=(),
                        profile=profile,
                        action=action,
                        result=result,
                        current_working_dataset_version=current_version,
                        semantic_annotations=annotations,
                    )
                )
        except (ModelGatewayError, ValidationError, ValueError) as exc:
            return with_failure_trace(failed_state(state, exc, clock()), state, exc, trace)
        verified = [
            item.model_dump(mode="json") for item in publications if item.status.value == "verified"
        ]
        unsupported = [
            item.model_dump(mode="json")
            for item in publications
            if item.status.value == "unsupported"
        ]
        should_propose_artifact = bool(verified and state.get("query_results", []))
        traced_state: AgentState = state
        for item in traces:
            traced_state = {
                **traced_state,
                "model_traces": append_trace(traced_state, item.model_dump(mode="json")),
            }
        update: AgentState = {
            "verified_insights": verified,
            "unsupported_claims": unsupported,
            "model_traces": traced_state.get("model_traces", []),
            "status": (
                AgentRunStatus.PROPOSING_ARTIFACT.value
                if should_propose_artifact
                else AgentRunStatus.COMPLETED.value
            ),
            "error": "",
        }
        if not should_propose_artifact:
            update.update(pause_execution_budget(state, clock()))
        return update

    def propose_artifact(state: AgentState) -> AgentState:
        profile = DataProfile.model_validate(state["data_profile"])
        plan = AnalysisPlan.model_validate(state["plan"])
        if run_budget_exceeded(state, plan.budget, clock()):
            return failed_state(state, run_budget_error(plan.budget), clock())

        try:
            result, source_action = chart_source_for_verified_insight(state)
        except ValueError:
            # Insights backed only by statistical results have no verified query table to chart;
            # that is expected, so it is not recorded as a chart error.
            return {
                "chart_renders": [],
                "artifact_error": "",
                "status": AgentRunStatus.COMPLETED.value,
                "error": "",
                **pause_execution_budget(state, clock()),
            }
        annotations = tuple(
            SemanticAnnotation.model_validate(value)
            for value in state.get("semantic_annotations", [])
        )
        result_columns = {
            f"r{index}": column.name for index, column in enumerate(result.columns, start=1)
        }
        request = StructuredModelRequest(
            task=ModelTask.CHART_INTENT,
            prompt=(
                "Analytical goal: "
                f"{json_for_prompt(state['goal'])}\n"
                "Verified query result metadata (schema only; no cell values): "
                f"{json_for_prompt(chart_result_metadata(result, profile))}\n"
                "Allowed result columns for x_field, y_fields, color_field, and label keys, as id "
                "to exact name (write either the id or the exact name): "
                f"{json_for_prompt(result_columns)}. "
                "Never use placeholder names such as x or y.\n"
                "Propose one readable chart using only these supported artifact types: "
                "kpi, table, histogram, bar, line, scatter. A kpi needs exactly one result row "
                "and exactly one y field; for one row with several values, use table with no "
                "encodings. Choose based on the analytical goal, field types, cardinality, and "
                "row count. Use only these result columns, by id or exact name, in encodings and "
                "labels. Set "
                "aggregation to null because aggregation already happened in verified SQL."
            ),
            response_schema=ChartIntentDraft,
            system_instruction=SYSTEM_INSTRUCTION,
            prompt_template_version="chart-intent-v5",
            max_output_tokens=1024,
            timeout_seconds=model_call_timeout_seconds(state, plan.budget, clock()),
        )
        trace: ModelCallTrace | None = None
        try:
            response = model_gateway.generate_structured(request)
            trace = response.trace
            if run_budget_exceeded(state, plan.budget, clock()):
                return failed_state(state, run_budget_error(plan.budget), clock())
            draft = response.output
            artifact_type = ArtifactType(draft.artifact_type)
            # A table always renders every result column, so stray encodings carry no meaning.
            is_table = artifact_type is ArtifactType.TABLE
            columns = result_column_lookup(result)
            intent = ChartIntent(
                artifact_type=artifact_type,
                analytical_purpose=draft.analytical_purpose,
                source_result_ref=make_query_result_reference(result, annotations),
                x_field=None if is_table else resolve_column(columns, draft.x_field),
                y_fields=()
                if is_table
                else tuple(columns.get(field_key(field), field) for field in draft.y_fields),
                color_field=None if is_table else resolve_column(columns, draft.color_field),
                aggregation=draft.aggregation,
                title=draft.title,
                labels={
                    columns.get(field_key(key), key): value for key, value in draft.labels.items()
                },
                # Formatting is cosmetic; keys the renderer does not support are dropped here.
                formatting_intent={
                    key: value
                    for key, value in draft.formatting_intent.items()
                    if key in SUPPORTED_FORMATTING_KEYS
                },
                validation_constraints=draft.validation_constraints,
            )
            try:
                rendered = render_chart(
                    intent, result, source_action, semantic_annotations=annotations
                )
            except ChartValidationError as exc:
                # A verified result can always be shown exactly as a table, so an invalid
                # proposal falls back to one instead of leaving the answer without a chart.
                result_names = {column.name for column in result.columns}
                table_intent = ChartIntent.model_validate(
                    {
                        **intent.model_dump(),
                        "artifact_type": ArtifactType.TABLE,
                        "x_field": None,
                        "y_fields": (),
                        "color_field": None,
                        "labels": {
                            key: value
                            for key, value in intent.labels.items()
                            if key in result_names
                        },
                        "formatting_intent": {},
                        "validation_constraints": (
                            *intent.validation_constraints,
                            "Shown as a table because the proposed "
                            f"{intent.artifact_type.value} chart was invalid: {safe_error(exc)}",
                        ),
                    }
                )
                rendered = render_chart(
                    table_intent, result, source_action, semantic_annotations=annotations
                )
            update: AgentState = {
                "chart_renders": [rendered.model_dump(mode="json")],
                "artifact_error": "",
                "model_traces": append_trace(state, trace.model_dump(mode="json")),
                "status": AgentRunStatus.COMPLETED.value,
                "error": "",
            }
        except (ChartValidationError, ModelGatewayError, ValidationError, ValueError) as exc:
            update = with_failure_trace(
                {
                    "chart_renders": [],
                    "artifact_error": safe_error(exc),
                    "status": AgentRunStatus.COMPLETED.value,
                    "error": "",
                    # A quota or overload failure is not an analysis defect.
                    "error_kind": "provider" if isinstance(exc, ModelProviderError) else "",
                },
                state,
                exc,
                trace,
            )
        update.update(pause_execution_budget(state, clock()))
        return update

    builder = StateGraph(AgentState)
    builder.add_node("interpret_request", interpret_request)
    builder.add_node("review_semantics", review_semantics)
    builder.add_node("create_plan", create_plan)
    builder.add_node("approve_plan", approve_plan)
    builder.add_node("request_tool", request_tool)
    builder.add_node("execute_tool", execute_tool)
    builder.add_node("synthesize_insights", synthesize_insights)
    builder.add_node("propose_artifact", propose_artifact)
    builder.add_edge(START, "interpret_request")
    builder.add_conditional_edges(
        "interpret_request",
        _route_after_interpretation,
        {"review": "review_semantics", "plan": "create_plan", "end": END},
    )
    builder.add_conditional_edges(
        "review_semantics",
        _route_after_review,
        {
            "interpret": "interpret_request",
            "review": "review_semantics",
            "plan": "create_plan",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "create_plan",
        _route_after_plan,
        {"approval": "approve_plan", "tool": "request_tool", "plan": "create_plan", "end": END},
    )
    builder.add_conditional_edges(
        "approve_plan",
        _route_after_approval,
        {"tool": "request_tool", "plan": "create_plan", "end": END},
    )
    builder.add_conditional_edges(
        "request_tool",
        _route_after_tool_request,
        {"execute": "execute_tool", "retry": "request_tool", "plan": "create_plan", "end": END},
    )
    builder.add_conditional_edges(
        "execute_tool",
        _route_after_execution,
        {
            "next": "request_tool",
            "retry": "request_tool",
            "synthesize": "synthesize_insights",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "synthesize_insights",
        _route_after_synthesis,
        {"artifact": "propose_artifact", "end": END},
    )
    builder.add_edge("propose_artifact", END)
    return builder.compile(checkpointer=checkpointer, name="tabular-analytics-agent")


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    if not thread_id.strip():
        raise ValueError("thread_id cannot be empty")
    return {"configurable": {"thread_id": thread_id}}


def _annotation_draft(value: dict[str, Any]) -> SemanticAnnotationDraft:
    return SemanticAnnotationDraft.model_validate(value)


def _route_after_interpretation(state: AgentState) -> Literal["review", "plan", "end"]:
    status = state["status"]
    if status == AgentRunStatus.AWAITING_SEMANTIC_REVIEW:
        return "review"
    if status == AgentRunStatus.PLANNING:
        return "plan"
    return "end"


def _route_after_review(state: AgentState) -> Literal["interpret", "review", "plan", "end"]:
    if state["status"] == AgentRunStatus.INTERPRETING:
        return "interpret"
    if state["status"] == AgentRunStatus.AWAITING_SEMANTIC_REVIEW:
        return "review"
    return "plan" if state["status"] == AgentRunStatus.PLANNING else "end"


def _route_after_plan(state: AgentState) -> Literal["approval", "tool", "plan", "end"]:
    if state["status"] == AgentRunStatus.AWAITING_PLAN_APPROVAL:
        return "approval"
    if state["status"] == AgentRunStatus.REQUESTING_TOOL:
        return "tool"
    if state["status"] == AgentRunStatus.PLANNING:
        return "plan"
    return "end"


def _route_after_approval(state: AgentState) -> Literal["tool", "plan", "end"]:
    if state["status"] == AgentRunStatus.REQUESTING_TOOL:
        return "tool"
    if state["status"] == AgentRunStatus.PLANNING:
        return "plan"
    return "end"


def _route_after_tool_request(state: AgentState) -> Literal["execute", "retry", "plan", "end"]:
    if state["status"] == AgentRunStatus.REQUESTING_TOOL:
        return "execute"
    if state["status"] == AgentRunStatus.RETRYING_TOOL:
        return "retry"
    if state["status"] == AgentRunStatus.PLANNING:
        return "plan"
    return "end"


def _route_after_execution(
    state: AgentState,
) -> Literal["next", "retry", "synthesize", "end"]:
    if state["status"] == AgentRunStatus.REQUESTING_TOOL:
        return "next"
    if state["status"] == AgentRunStatus.RETRYING_TOOL:
        return "retry"
    if state["status"] == AgentRunStatus.SYNTHESIZING:
        return "synthesize"
    return "end"


def _route_after_synthesis(state: AgentState) -> Literal["artifact", "end"]:
    if state["status"] == AgentRunStatus.PROPOSING_ARTIFACT:
        return "artifact"
    return "end"
