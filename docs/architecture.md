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

This future module will expose operations that advance an Analysis Session. LangGraph will be
the initial adapter at this seam, but its internal state representation must not leak into the
domain contracts or Streamlit UI.

### Model seam

The model seam becomes real in Milestone 3 with two adapters: Gemini for production use and a
deterministic fake for tests. Provider-specific response objects do not cross the seam.

### Delivery seam

Streamlit is the first delivery adapter. Application operations must remain callable without
Streamlit so FastAPI and React can be introduced later without rewriting the analytical core.

## Testing rule

The public interface of each module is its test surface. Tests assert observable validated
results and error modes, not internal helper calls. External dependencies receive production
and local test adapters only when both are genuinely needed.
