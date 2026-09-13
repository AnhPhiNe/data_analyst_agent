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

## Remaining slices

- M5.2: Candidate and Pinned Artifact lifecycle.
- M5.3: Streamlit upload, conversation, approval, insight, chart, and dashboard flow.
- M5.4: CSV, JSON metadata, and self-contained HTML exports.

Heatmap, stacked bar, box plot, and missing-value chart rendering remain outside M5.1 and will be
added only when required by the dashboard vertical slice.

## Backlog from M5.1 review

The following P2 design improvements are intentionally deferred until another chart type or caller
demonstrates the need: consolidate per-chart validation into registered policies, introduce a typed
Query Result reference, and remove the remaining small source-binding predicate duplication. They do
not affect M5.1 correctness and are not release blockers. Goal-aware validation of
`analytical_purpose` is deferred to the chart-recommendation orchestration step, where the approved
analytical goal is available.
