# Milestone 5 — Dashboard and Export

Milestone 5 is delivered in small, independently tested commits. This document tracks the shipped
scope and the remaining product slices.

## M5.1 — Validated chart rendering

The `visualization` module accepts a domain `ChartIntent`, the exact `QueryResult` it references, and
the successful verified SQL `ToolAction` that produced that result. Before Plotly is called, the
validator checks:

1. Exact `query-result:<id>` binding across intent, action, and result.
2. A successful read-only SQL Tool Action with passed Verification Gates, matching dataset version,
   and the exact SQL recorded by the Query Result.
3. A non-empty, non-truncated result.
4. Exact field and label grounding against the result schema.
5. Encoding requirements and numeric or temporal type compatibility.
6. MVP readability limits for tables, categories, color groups, and plotted points.
7. A small formatting allowlist with validated value types and ranges.

The renderer supports KPI, table, histogram, bar, line, and scatter specifications. Color grouping is
available for bar, line, and scatter when exactly one y field is selected. Aggregation is deliberately
not performed by the renderer: any aggregation must already exist in the verified SQL result.

## M5.2 — Dashboard artifact lifecycle

`ArtifactStore` publishes only verified chart renders. Each `AnalyticalArtifact` is tied to one
Analysis Session and contains a typed reference with the query ID, dataset ID, and Working Dataset
version. SQLite stores searchable lifecycle metadata while each immutable Plotly specification is
written as versioned JSON with atomic publication.

The application-facing operations are:

- Create a Candidate Artifact from a passed `ChartRenderResult`.
- Refine a candidate into a new immutable render version.
- Pin a candidate to the session Dashboard and unpin it back to the candidate collection.
- List candidates and Dashboard artifacts separately with session isolation.
- Reload artifact metadata and exact render specifications after process restart.

Pinned artifacts cannot be silently refined. The user must unpin them first, so displayed dashboard
content changes only through an explicit lifecycle action. Failed or unverified renders are rejected
before any artifact metadata or JSON specification is published. Publication and pinning also require
the typed source dataset and Working Dataset version to match the current `AnalysisSession`; candidate
and Dashboard lists omit artifacts that became stale after the session advanced or its confirmed
Semantic Annotations changed.

## M5.3 — Streamlit end-to-end workspace

Status: implementation in progress; not yet accepted or committed as a completed milestone.
Live testing has reached plan approval without unnecessary semantic questions. A later tool request
was rejected because the model had to echo approved source fields, and a diagnostic run encountered
provider rate limiting. The model boundary now returns only SQL or statistical parameters; the
orchestrator binds the approved tool, source-field allowlist, statistical operation, and fixed
inference policy. SQL references and statistical parameter fields are still checked against that
approved allowlist, while output aliases remain separate from source columns. Provider failures stop
after gateway retries instead of entering the SQL repair loop. A successful live run through verified
insights, chart publication, and pinning, plus the milestone review, remains required.

The replaceable `application` module now coordinates safe browser uploads, ingestion and profiling,
durable Analysis Session metadata, SQLite LangGraph checkpoints, agent start/resume operations, and
idempotent candidate publication. The Streamlit adapter calls only these public services and domain
contracts; analytical logic remains outside the UI.

The local interface provides:

- CSV/XLSX upload and a profile summary with data-quality and likely-PII warnings.
- Conversational analytical requests with explicit Semantic Annotation and plan approval pauses.
- Verified Insights, unsupported claims, result tables, and bounded model/tool audit metadata.
- Deterministically rendered Candidate Artifacts and user-controlled pin/unpin Dashboard composition.
- Session-scoped checkpoint and artifact recovery across application reruns.

Chart proposals use a structured `ChartIntent` model call containing schema metadata but no raw cell
values. The orchestrator binds the proposal to the exact query result referenced by a Verified
Insight, then the existing deterministic renderer performs all source, type, completeness, and
readability checks. A bad chart proposal is non-fatal and does not discard a completed verified
analysis.

## Remaining slices

- M5.4: CSV, JSON metadata, and self-contained HTML exports.

Heatmap, stacked bar, box plot, and missing-value chart rendering remain outside M5.1 and will be
added only when required by the dashboard vertical slice.

## Backlog from M5.1 review

The following P2 design improvements are intentionally deferred until another chart type or caller
demonstrates the need: consolidate per-chart validation into registered policies and remove the
remaining small source-binding predicate duplication. A typed Render Spec reference can replace the
store-generated relative path when export introduces multiple artifact locations.
These items do not affect current correctness and are not release blockers. Goal-aware validation of
`analytical_purpose` is deferred to the chart-recommendation orchestration step, where the approved
analytical goal is available.
