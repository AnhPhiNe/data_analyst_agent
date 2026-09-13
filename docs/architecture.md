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
decisions back through the public orchestration interface.

### Model seam

`ModelGateway.generate_structured` accepts a provider-neutral request and a Pydantic response
schema. Shared behavior validates every response and permits one schema-repair attempt. The
production adapter uses LangChain's native JSON-schema integration for Gemini, with bounded
exponential retry only for transient failures. The deterministic fake consumes queued outputs for
tests. Provider response objects, credentials, and error bodies do not cross this seam.

### Delivery seam

Streamlit is the first delivery adapter. Application operations must remain callable without
Streamlit so FastAPI and React can be introduced later without rewriting the analytical core.

## Testing rule

The public interface of each module is its test surface. Tests assert observable validated
results and error modes, not internal helper calls. External dependencies receive production
and local test adapters only when both are genuinely needed.
