# Foundation Architecture

## Dependency direction

```text
Streamlit adapter
    -> application module
        -> orchestration module
            -> domain module
                <- analytical adapters
                <- persistence adapters
                <- model adapters
```

The `domain` module owns the stable language, contracts, and invariants. It has no dependency
on Streamlit, LangGraph, DuckDB, a model provider, or persistence technology.

## Planned modules and seams

### Domain module

The external interface consists of validated Pydantic contracts. It hides cross-field
invariants such as a passed verification requiring all checks to pass. Tests exercise these
contracts directly.

### Dataset analysis module

`TabularDataCore` exposes one small interface for inspecting, ingesting, profiling, and querying
a session dataset. CSV/XLSX parsing, immutable source copying, DuckDB configuration, SQL policy,
PII heuristics, and result normalization remain implementation details behind it.

Published source copies are made read-only and retain a content hash for tamper detection. Each
Working Dataset stores binding metadata for its dataset ID, source hash, and version; every profile
or query verifies both canonical paths and that metadata before reading data. Profiles apply one
missing-value population consistently and include near-constant and Tukey-IQR outlier indicators.

The module uses local-substitutable dependencies: tests exercise real files and real DuckDB
databases inside temporary directories rather than mocking their behavior.

### Verification module

The verification module accepts validated analytical records and returns deterministic
`VerificationResult` values. Its baseline gate checks dataset identity, Working Dataset version,
schema grounding, and whether a query returned usable evidence. Future statistical and artifact
checks deepen this same interface rather than relying on model confidence.

Insight publication adds deterministic gates for successful Tool Actions, exact result binding,
current dataset version, schema grounding, and structured assertions over exact evidence metrics.
Final claim text is rendered deterministically, so unsupported numeric, directional, comparative,
significance, or causal language cannot be introduced by the model. Publications that fail any gate
remain `UnsupportedClaim` records. A stable hash
of confirmed Semantic Annotations and the Working Dataset version allow previously verified results
to be marked `StaleInsight` when their analytical context changes.

### Statistical analysis module

The statistical module exposes typed requests for descriptive statistics, correlation, confidence
intervals, two-group and multi-group tests, chi-square, and simple linear or logistic regression.
NumPy and SciPy perform every calculation. Results record sample sizes, complete/available-case
missing-data handling, assumptions, p-values, Bonferroni-adjusted alpha, effect sizes, practical
significance, warnings, and exact parameters.

`StatisticalTool` binds this engine to one validated Working Dataset. It extracts only approved
fields through the read-only data core, caps input using deterministic reservoir sampling with a
recorded seed, and returns a verified, replayable Tool Action without exposing storage details to
the statistical engine.

### Visualization module

`visualization` is a deterministic adapter from a structured `ChartIntent` and verified SQL
`QueryResult` to JSON-safe Plotly specifications. It checks the exact Tool Action/result reference,
Working Dataset version, result completeness, field existence and types, encoding shape, formatting
allowlist, and basic readability limits before rendering. It never executes SQL, aggregates values,
or accepts frontend code from the model. Milestone 5.1 supports KPI, table, histogram, bar, line, and
scatter output.

`ArtifactStore` owns the local Candidate/Pinned lifecycle. It records session-scoped metadata in
SQLite and publishes immutable, versioned Plotly JSON specifications through an atomic file replace.
Every artifact carries a typed Query Result reference and passed chart verification. Refinement is
allowed only while an artifact is a candidate; pinning is an explicit user-controlled dashboard
transition and never changes the referenced render version. Publication, pinning, and dashboard reads
are grounded against the current Analysis Session dataset, Working Dataset version, and deterministic
Semantic Annotation fingerprint, so stale or cross-session results cannot surface as current dashboard
content.

### Application module

`LocalAnalysisApplication` is the UI-neutral use-case boundary. It stages untrusted uploads beneath a
generated session namespace, delegates inspection/ingestion/profiling to `TabularDataCore`, persists a
validated workspace atomically, opens the session's SQLite checkpointer for each agent operation, and
publishes completed chart renders idempotently through `ArtifactStore`. It can be integration-tested
with `FakeModelGateway` and later reused by a FastAPI adapter.

### Orchestration module

`AgentOrchestrator` exposes `start`, `resume`, and `get_state` operations for an Analysis Session.
Its explicit LangGraph transitions interpret the goal, optionally pause for Semantic Annotation
confirmation, create a plan, pause for plan approval, request SQL, execute it, and apply the
baseline Verification Gates for every approved step. It binds canonical SQL column references to
the current plan step, rejects wildcard projections and repeated Tool Actions, enforces tool,
repair, query-timeout, and active-run budgets, and stops safely on failed verification. Human
approval wait time is checkpointed but excluded from the active-run budget. Applications can inject
a validated `ExecutionBudget`; each query is capped by both its tool timeout and the remaining run
budget. Model calls likewise have a transport-enforced timeout bounded by the remaining active-run
budget.

The default local checkpointer stores JSON-safe state in session-scoped SQLite. Dynamic LangGraph
interrupts make approvals durable across application reloads. Checkpoint namespaces come only from
the validated Analysis Session ID; Streamlit only renders the current state and sends approval
decisions back through the public orchestration interface. After approved Tool Actions pass
verification, the graph requests structured insight drafts, sends only non-PII evidence values to
the Model Gateway, and publishes each draft as a Verified Insight or Unsupported Claim. Model-bound
evidence is capped at 200 values, statistical summaries are prioritized, and query results above 50
rows are withheld until the agent produces a bounded aggregate. Statistical plan steps declare their
operation up front; the orchestrator derives multiple-testing family size from the approved plan.
After insight synthesis, the graph may request one structured Chart Intent using goal and schema
metadata only. It resolves the exact verified query through Evidence Trail action IDs and invokes the
deterministic visualization adapter. Chart-proposal failure is recorded separately and does not turn a
successfully verified analysis into a failed run.

### Model seam

`ModelGateway.generate_structured` accepts a provider-neutral request and a Pydantic response
schema. Shared behavior validates every response and permits one schema-repair attempt. The
production adapter uses LangChain's native JSON-schema integration for Gemini, with bounded
exponential retry only for transient failures. The deterministic fake consumes queued outputs for
tests. Provider response objects, credentials, and error bodies do not cross this seam.

### Delivery seam

Streamlit is the first delivery adapter. Application operations must remain callable without
Streamlit so FastAPI and React can be introduced later without rewriting the analytical core.
The current adapter implements upload, profile inspection, conversational requests, approval pauses,
verified/unsupported outcomes, candidate charts, dashboard pinning, and audit metadata.

## Testing rule

The public interface of each module is its test surface. Tests assert observable validated
results and error modes, not internal helper calls. External dependencies receive production
and local test adapters only when both are genuinely needed.
