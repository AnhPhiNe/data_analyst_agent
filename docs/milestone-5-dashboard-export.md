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
and Dashboard lists omit artifacts that became stale after the session advanced.

## Remaining slices

- M5.3: Streamlit upload, conversation, approval, insight, chart, and dashboard flow.
- M5.4: CSV, JSON metadata, and self-contained HTML exports.

Heatmap, stacked bar, box plot, and missing-value chart rendering remain outside M5.1 and will be
added only when required by the dashboard vertical slice.

## Backlog from M5.1 review

The following P2 design improvements are intentionally deferred until another chart type or caller
demonstrates the need: consolidate per-chart validation into registered policies and remove the
remaining small source-binding predicate duplication. A typed Render Spec reference can replace the
store-generated relative path when export introduces multiple artifact locations. Semantic-annotation
freshness will join the source reference when M5.3 connects artifacts to live session annotations.
These items do not affect current correctness and are not release blockers. Goal-aware validation of
`analytical_purpose` is deferred to the chart-recommendation orchestration step, where the approved
analytical goal is available.
