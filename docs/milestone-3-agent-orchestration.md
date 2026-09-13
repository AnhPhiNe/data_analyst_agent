# Milestone 3 — Agent Orchestration

## Delivered behavior

The local single agent now has two replaceable boundaries:

- `ModelGateway` validates structured outputs independently of the provider.
- `AgentOrchestrator` owns explicit LangGraph state transitions and durable approvals.

The current execution slice is:

```text
interpret request
  -> semantic review (only when ambiguity is proposed)
  -> create plan
  -> plan approval
  -> for each approved step:
       request read-only SQL
       bind SQL fields to that step
       execute with TabularDataCore
       deterministic verification
  -> complete or stop safely
```

Synthesis, Verified Insights, statistics, and chart proposal remain in Milestones 4–5.

## Safety properties

- Dataset metadata and user text are JSON-encoded and delimiter-escaped inside explicit untrusted
  prompt sections.
- Model output is validated against a Pydantic schema and gets at most one format repair.
- Gemini transport retries are bounded and apply only to transient failures.
- Plans and tool requests cannot reference fields absent from the Data Profile.
- Canonical SQL references must match the current approved step exactly; wildcard projections and
  dynamic selectors, table-as-struct projections, and attempts to read extra fields—including PII
  candidates—are rejected before execution.
- Only the allowlisted `read_only_sql` tool can be requested.
- Repeated Tool Actions are identified from canonical SQL, tool name, and Working Dataset version,
  so changing descriptive metadata cannot bypass the guard.
- Tool count, repair count, query timeout, and active execution time are bounded by an injectable,
  validated `ExecutionBudget`. A query receives only the smaller of its tool timeout and the
  remaining run budget, and terminal states freeze the measured active duration. Time spent waiting
  for a human approval does not consume the active run budget.
- Model calls have a configurable transport timeout and receive only the smaller of that timeout
  and the remaining active-run budget; a blocked provider call therefore returns control safely.
- Every plan step executes in order, and each Tool Action records its originating plan-step ID.
- Checkpoint namespaces are derived only from the validated Analysis Session ID.
- Semantic hypotheses can be rejected and clarified repeatedly; an explicitly empty annotation list
  means the user confirmed that no proposed field meaning should be applied.
- Query evidence must pass deterministic gates before the run completes.
- SQLite checkpoint deserialization uses a restricted msgpack allowlist.

## Local provider configuration

Use `.env.example` as a template without committing real credentials. Provide `GOOGLE_API_KEY`
through the process environment or a mapping such as Streamlit Secrets, and optionally override
`TABULAR_AGENT_MODEL` and `TABULAR_AGENT_MODEL_TIMEOUT_SECONDS`; the defaults are
`gemini-3.5-flash` and 30 seconds. The Gemini adapter uses LangChain native JSON-schema structured
output. No automatic provider fallback is enabled.

Tests inject `FakeModelGateway` and either in-memory or SQLite checkpointers, so CI is deterministic
and does not need credentials or network access.
