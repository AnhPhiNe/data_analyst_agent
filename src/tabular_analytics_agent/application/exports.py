"""In-memory CSV result and verified-insight metadata exports.

Verification Gates already guarantee that each Verified Insight matches its Tool
Action and deterministic result. The export boundary only binds records to the
current session, dataset, Working Dataset version, and semantic annotations,
then selects the evidence the insights reference. It writes no files and never
emits prompts, traces, or provider configuration.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from typing import Any, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from tabular_analytics_agent.data import QueryResult
from tabular_analytics_agent.domain import AnalysisSession, DataProfile, ToolAction, VerifiedInsight
from tabular_analytics_agent.statistics import StatisticalResult
from tabular_analytics_agent.verification import semantic_annotation_fingerprint

EXPORT_SCHEMA_VERSION: Final[str] = "1"
EXPORT_TYPE: Final[str] = "verified_insights"

EvidenceResult = QueryResult | StatisticalResult
_FORMULA_PREFIXES: Final = frozenset({"=", "+", "-", "@"})


class ExportValidationError(ValueError):
    """Raised when a requested export is stale, incomplete, or out of scope."""


class ExportRequest(BaseModel):
    """Typed export input assembled from a workspace and completed agent state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session: AnalysisSession
    profile: DataProfile
    verified_insights: tuple[VerifiedInsight, ...] = ()
    tool_actions: tuple[ToolAction, ...] = ()
    query_results: tuple[QueryResult, ...] = ()
    statistical_results: tuple[StatisticalResult, ...] = ()
    state_session_id: UUID | None = None

    @classmethod
    def from_state(
        cls,
        *,
        session: AnalysisSession,
        profile: DataProfile,
        state: Mapping[str, object],
    ) -> Self:
        """Build a request from the JSON-safe ``AgentState`` stored by the graph."""
        if state.get("session_id") is None:
            raise ExportValidationError("agent state must include session_id")
        try:
            return cls.model_validate(
                {
                    "session": session,
                    "profile": profile,
                    "state_session_id": state["session_id"],
                    **{
                        key: state.get(key) or ()
                        for key in (
                            "verified_insights",
                            "tool_actions",
                            "query_results",
                            "statistical_results",
                        )
                    },
                }
            )
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_input=False)
            )
            raise ExportValidationError(f"agent state is not valid for export: {details}") from exc

    @model_validator(mode="after")
    def bind_to_workspace(self) -> Self:
        if self.session.source_dataset_id != self.profile.dataset.dataset_id:
            raise ExportValidationError("session and profile dataset identities do not match")
        if self.state_session_id is not None and self.state_session_id != self.session.session_id:
            raise ExportValidationError(
                "agent state session_id does not match the workspace session"
            )
        return self


def evidence_results(request: ExportRequest) -> tuple[EvidenceResult, ...]:
    """Return the current results referenced by the request's Verified Insights, in order."""
    version = request.session.working_dataset_version
    fingerprint = semantic_annotation_fingerprint(request.session.semantic_annotations)
    actions = {action.action_id: action for action in request.tool_actions}
    results: dict[str, EvidenceResult] = {
        **{f"query-result:{item.query_id}": item for item in request.query_results},
        **{f"statistical-result:{item.result_id}": item for item in request.statistical_results},
    }
    selected: dict[str, EvidenceResult] = {}
    for insight in request.verified_insights:
        evidence = insight.evidence
        if evidence.dataset_id != request.profile.dataset.dataset_id:
            raise ExportValidationError("Verified Insight evidence belongs to another dataset")
        if evidence.working_dataset_version != version:
            raise ExportValidationError("Verified Insight evidence is stale")
        if evidence.semantic_annotation_fingerprint != fingerprint:
            raise ExportValidationError("Verified Insight evidence has stale semantic annotations")
        for action_id in evidence.tool_action_ids:
            action = actions.get(action_id)
            if action is None or action.output_ref not in results:
                raise ExportValidationError(
                    f"Verified Insight references unavailable evidence for Tool Action {action_id}"
                )
            result = results[action.output_ref]
            if isinstance(result, QueryResult) and result.working_dataset_version != version:
                raise ExportValidationError(f"result {action.output_ref} is stale")
            selected[action.output_ref] = result
    return tuple(selected.values())


def evidence_query_results(request: ExportRequest) -> tuple[QueryResult, ...]:
    """Return the query results that can be offered as CSV downloads."""
    return tuple(item for item in evidence_results(request) if isinstance(item, QueryResult))


def export_verified_insights_json(request: ExportRequest) -> bytes:
    """Return versioned Verified Insight metadata as UTF-8 JSON.

    Query rows stay in the CSV export; the JSON carries query schema, SQL, and
    counts plus full statistical estimates and parameters.
    """
    results = evidence_results(request)
    refs = {_result_reference(result) for result in results}
    dataset = request.profile.dataset
    payload: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_type": EXPORT_TYPE,
        "session": {
            "session_id": str(request.session.session_id),
            "source_dataset_id": str(dataset.dataset_id),
            "working_dataset_version": request.session.working_dataset_version,
            "semantic_annotation_fingerprint": semantic_annotation_fingerprint(
                request.session.semantic_annotations
            ),
        },
        "dataset": {
            "dataset_id": str(dataset.dataset_id),
            "sha256": dataset.sha256,
            "size_bytes": dataset.size_bytes,
        },
        "verified_insights": [item.model_dump(mode="json") for item in request.verified_insights],
        "tool_actions": [
            action.model_dump(mode="json")
            for action in request.tool_actions
            if action.output_ref in refs
        ],
        "results": [_result_metadata(result) for result in results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def export_query_result_csv(
    result: QueryResult,
    *,
    neutralize_formula_injection: bool = True,
) -> bytes:
    """Return a UTF-8 CSV table for one QueryResult, held entirely in memory.

    ``None`` becomes an empty cell. By default, text cells whose first
    non-whitespace character is ``=``, ``+``, ``-``, or ``@`` get a leading
    apostrophe so spreadsheet applications treat them as text.
    """

    def cell(value: object) -> object:
        if value is None:
            return ""
        if (
            neutralize_formula_injection
            and isinstance(value, str)
            and value.lstrip()[:1] in _FORMULA_PREFIXES
        ):
            return "'" + value
        return value

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(cell(column.name) for column in result.columns)
    writer.writerows((cell(value) for value in row) for row in result.rows)
    return output.getvalue().encode("utf-8")


def _result_reference(result: EvidenceResult) -> str:
    if isinstance(result, QueryResult):
        return f"query-result:{result.query_id}"
    return f"statistical-result:{result.result_id}"


def _result_metadata(result: EvidenceResult) -> dict[str, Any]:
    reference = {"result_ref": _result_reference(result)}
    if isinstance(result, QueryResult):
        return {
            **reference,
            "result_type": "query",
            **result.model_dump(mode="json", exclude={"rows"}),
        }
    return {**reference, "result_type": "statistical", **result.model_dump(mode="json")}


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "EXPORT_TYPE",
    "ExportRequest",
    "ExportValidationError",
    "evidence_query_results",
    "evidence_results",
    "export_query_result_csv",
    "export_verified_insights_json",
]
