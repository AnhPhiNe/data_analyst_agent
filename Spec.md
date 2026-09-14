# Tabular Analytics Agent — Product and Technical Specification

**Status:** Approved — v1.3 targeted amendments (see Section 24)
**Target:** Local-first MVP and AI Engineer portfolio project
**Primary interface:** Streamlit
**Last updated:** 2026-09-14

## 1. Product Summary

Tabular Analytics Agent is a local-first application in which a single AI agent helps users inspect, analyze, explain, and visualize tabular datasets. A user uploads a CSV or XLSX file, collaborates with the agent to clarify the analytical goal and ambiguous field meanings, reviews an analysis plan, and receives verified insights and dashboard artifacts backed by reproducible computations.

The product is intended to replace repetitive manual exploratory analysis while retaining human control, traceability, and statistical caution. The language used by the product is defined in [CONTEXT.md](./CONTEXT.md).

## 2. Target Users

### 2.1 Primary users

- Junior data analysts who want to accelerate exploratory and diagnostic analysis.
- Business users with basic data literacy who cannot or do not want to write SQL and Python manually.

### 2.2 Secondary audience

- AI engineering recruiters and interviewers evaluating the project's architecture, agent design, evaluation, and reliability.

## 3. Product Goals

The MVP shall:

1. Accept a broad, explicitly bounded class of CSV and XLSX datasets.
2. Produce a factual Data Profile before attempting deep analysis.
3. Let users express Analytical Goals in natural language.
4. Use a single Orchestrator Agent to plan and select typed analytical tools.
5. Perform numerical work through deterministic tools rather than LLM arithmetic.
6. Present only conclusions that pass deterministic Verification Gates as Verified Insights.
7. Preserve an Evidence Trail for every Verified Insight.
8. Let users select Candidate Artifacts for an interactive Dashboard.
9. Persist and resume local Analysis Sessions.
10. Demonstrate measurable reliability through a versioned evaluation suite.

## 4. Non-goals for the MVP

The MVP will not provide:

- Automatic predictive-model training or AutoML.
- Claims of causal inference.
- External database connections.
- Complex joins across multiple uploaded datasets.
- Streaming or real-time analytics.
- Multi-user collaboration or role-based access control.
- Scheduled reports.
- Internet access for the Orchestrator Agent.
- Arbitrary, unrestricted Python execution.
- Automatic business decisions or operational actions.
- Regulatory compliance certification such as GDPR or HIPAA compliance.

### 4.1 MVP scope tiers

- **Must:** secure CSV/XLSX ingestion; the Data Profile and profile answers; proposing and accepting Analytical Goals in English or Vietnamese; planning with read-only SQL and statistical Tool Actions; Verified Insights with Evidence Trails; Must-tier charts with Candidate and Pinned Artifacts; creating, resuming, and deleting sessions; CSV and JSON export; the evaluation runner and its quality gates.
- **Should:** Working Dataset transformations (FR-07); Should-tier charts; HTML report export; Vietnamese user-interface text; a quota-aware model gateway.
- **Could:** per-task model selection; additional provider adapters; PDF and notebook export.

## 5. Supported Dataset Contract

A Supported Dataset is one logical rectangular table satisfying the following constraints:

- Input format is `.csv` or `.xlsx`.
- Maximum source file size is 100 MB by default.
- The table has a recognizable header row.
- Supported field families are numeric, categorical, boolean, date/time, and short text.
- An XLSX workbook may contain multiple sheets, but the user selects one sheet as the logical dataset for the Analysis Session.
- Images, PDFs, nested JSON, macros, streaming sources, and complex multi-table relationships are outside the MVP.

The maximum size and execution limits must be configurable rather than hard-coded throughout the application.

## 6. Primary User Journey

1. The user creates or resumes an Analysis Session.
2. The user uploads a CSV or XLSX Source Dataset.
3. The application validates the file, stores it immutably, and computes its hash.
4. The application creates a Data Profile and surfaces quality risks and possible PII.
5. The application proposes three to five relevant Analytical Goals.
6. The user selects a suggestion or provides a different goal, in English or Vietnamese.
7. Questions answerable from the Data Profile alone — column names, field types, row count, missing values, unique counts, quality warnings, and whole-column descriptive statistics — are answered directly from the profile without an Analysis Plan or Tool Actions.
8. For other goals, the agent asks for Semantic Annotations when ambiguity could materially change the answer to that goal.
9. The agent displays a concise Analysis Plan.
10. The application pauses for approval only when a plan step requires review, including every consequential data-cleaning or transformation step; read-only plans run immediately and remain inspectable.
11. The graph executes typed Tool Actions within configured budgets.
12. Verification Gates check results and provenance.
13. The agent presents Verified Insights, caveats, result tables, and Candidate Artifacts.
14. The user may ask follow-up questions, revise an analytical assumption, or pin artifacts to the Dashboard.
15. The user exports the analysis or deletes the entire session.

## 7. Functional Requirements

### FR-01 — Session management

- Create, list, open, resume, complete, fail, and delete Analysis Sessions.
- A session status is one of `running`, `waiting`, `failed`, or `completed`.
- A session operates within its own storage namespace.
- Reloading Streamlit must not silently lose a persisted session.
- Deleting a session removes its Source Dataset, Working Dataset, artifacts, metadata, and local traces.
- Deletion requires an explicit confirmation bound to the selected session and never touches another session's namespace. Artifact metadata and session files are removed in sequence rather than in one transaction; a partial failure is reported instead of claimed as success. Deletion is not a forensic secure erase.

### FR-02 — File ingestion

- Validate extension, MIME/file signature, size, encoding, delimiter, and structural readability.
- Store the Source Dataset immutably under a generated internal identifier.
- Never use the user-supplied filename as an unchecked filesystem path.
- Allow sheet selection for XLSX workbooks.
- Do not execute macros or formulas.
- Reject unsupported or suspicious files with an actionable explanation.

### FR-03 — Data profiling

Produce a Data Profile containing at least:

- Row and column counts.
- Inferred field types with confidence or ambiguity flags.
- Missing-value counts and percentages.
- Unique counts and duplicate-row counts.
- Numeric distributions and robust summary statistics.
- Categorical frequencies subject to cardinality limits.
- Date ranges where applicable.
- Constant, near-constant, identifier-like, and high-cardinality field warnings.
- Outlier indicators clearly described as heuristics.
- Possible PII indicators.

The Data Profile is factual evidence and must not be labeled as an insight.

### FR-04 — Semantic clarification

- Interpret the Analytical Goal first, then detect field meanings, units, roles, or date semantics that are materially ambiguous for that goal.
- Default to no clarification; ask only when the ambiguity blocks a responsible answer.
- Permit the agent to state a hypothesis but require user confirmation before relying on it.
- Save confirmed meanings as Semantic Annotations for the current session.
- Re-enter clarification if a later request exposes unresolved ambiguity.
- Map every explicitly requested metric to the dataset as direct, derived, or unavailable (Section 25.4). The agent never lets a different field stand in for a requested metric.
- An unavailable metric pauses for clarification. The user either corrects the request, which is interpreted again from the start, or accepts that the metric is unavailable, which ends the run as a typed refusal rather than a failure.

### FR-05 — Analytical goals

The MVP supports these goal families:

- Data quality.
- Summary.
- Comparison.
- Trend.
- Relationship.
- Driver Exploration.

Driver Exploration may identify Statistical Associations but must not imply causation.

Goals may be written in English or Vietnamese, and Supported Datasets may use Vietnamese headers and values.

### FR-06 — Analysis planning

- Convert an Analytical Goal into a user-visible Analysis Plan.
- A plan contains steps, expected Tool Actions, required fields, intended outputs, and known caveats.
- Do not expose private chain-of-thought.
- Answer questions fully covered by the Data Profile (Section 6, step 7) without creating a plan.
- Pause for approval only when a step requires review; read-only plans run immediately and remain visible to the user.
- Permit the user to reject or revise a plan that paused for approval.

### FR-07 — Working Dataset transformations (Should tier)

- Keep the Source Dataset immutable.
- Apply analysis transformations only to a Working Dataset.
- Automatically permit reversible normalization such as trimmed column names and explicit null representations.
- Require confirmation before dropping rows, imputing values, resolving ambiguous types, removing duplicates, or treating outliers.
- Transformation tools must require approval deterministically; a model-provided `requires_approval` flag is never the only safeguard.
- Record every accepted transformation in the Evidence Trail.

### FR-08 — Analytical execution

- Prefer DuckDB SQL for filtering, aggregation, grouping, sorting, and result-table production.
- Use Pandas where DataFrame transformations are materially clearer or unavailable in the approved SQL subset.
- Use NumPy and SciPy for deterministic numerical and statistical operations.
- Do not rely on the LLM to calculate final analytical values.
- Return structured results with schema, row count, parameters, warnings, and provenance.

### FR-09 — Statistical analysis

The initial statistical toolset supports:

- Descriptive statistics.
- Type-appropriate association and correlation measures.
- Confidence intervals.
- T-test and Mann–Whitney test.
- Chi-square test.
- ANOVA and Kruskal–Wallis test.
- Effect sizes.
- Simple linear and logistic regression for relationship exploration.

Statistical tools must:

- Check applicable assumptions.
- Report sample size and missing-data handling.
- Report effect size where meaningful.
- Warn about multiple testing.
- Distinguish statistical significance from practical significance.
- Never transform association into a causal claim.

### FR-10 — Verified Insights

Every Verified Insight contains:

- A concise natural-language conclusion rendered deterministically from a typed Insight Assertion (Section 25.2), never free-form model text.
- Computed supporting values.
- Referenced fields.
- Filters and analysis scope.
- Source and result row counts.
- Tool Action identifiers and exact reproducible parameters.
- Relevant caveats and uncertainty.
- Verification status.
- An optional linked artifact.

If required evidence is missing or validation fails, the conclusion is an Unsupported Claim and must not be displayed as a Verified Insight. A malformed insight draft becomes an Unsupported Claim without discarding the other drafts of the same run.

Row-level values are described by their SQL `GROUP BY` keys (for example `year = 2024`); ungrouped results fall back to their text columns, and single-row results omit row positions. Claims about filtered query results name the SQL `WHERE`/`HAVING` conditions, which are also recorded as Evidence Trail filters, and statistics that relate two fields name both fields. When no drafted claim passes verification, each complete (untruncated), ungrouped, non-PII query result publishes its row count as a deterministic claim, because a row listing is answered by its table. Row listings may select `rowid + 1 AS row_number` to identify rows of the uploaded file.

### FR-11 — Charts and dashboard

- The agent proposes a structured Chart Intent rather than frontend code.
- A deterministic chart tool validates the intent and produces a Plotly-compatible specification.
- Must-tier artifacts are KPI card, table, histogram, bar chart, line chart, and scatter plot. Box plot, heatmap, stacked bar chart, and missing-value chart are Should-tier.
- A KPI shows exactly one value from a one-row result, with every significant digit rather than a rounded display. Bar charts use a categorical axis, so numeric keys such as months are not drawn as a continuous scale. A table renders every result column, so encodings supplied for a table are ignored.
- Chart selection considers analytical goal, field types, cardinality, sample size, and readability.
- Pie charts are not selected by default.
- Candidate Artifacts appear separately from Pinned Artifacts.
- The user controls which artifacts form the Dashboard.

### FR-12 — Conversational follow-up

- Follow-up requests reuse the current session's profile, annotations, approved transformations, and Evidence Trails.
- The agent may revise the Analysis Plan when the goal changes.
- Earlier results must not be silently reused after a relevant transformation or annotation changes; affected results become stale and require re-verification.

### FR-13 — Export

The MVP exports, in priority order:

- Must: CSV files for result tables.
- Must: machine-readable JSON metadata for Verified Insights and Evidence Trails, including reproducible SQL or typed Tool Action parameters.
- Should: an interactive or self-contained HTML analytical report.

PDF and generated notebooks are stretch goals.

Exports include only results referenced by current Verified Insights, bound to the current session, dataset, Working Dataset version, and Semantic Annotations; evidence correctness remains the responsibility of the Verification Gates. JSON carries query schema, SQL, and counts plus full statistical results, but no result rows, model prompts, traces, or provider settings. CSV text cells and headers that could run as spreadsheet formulas are prefixed with an apostrophe. CSV files start with a UTF-8 byte-order mark so spreadsheet applications read Vietnamese and other non-ASCII text correctly.

## 8. Application Architecture

The MVP uses the following dependency direction:

```text
Streamlit UI
    -> Application Services and Pydantic Contracts
        -> LangGraph Orchestrator
            -> LangChain Model and Tool Adapters
                -> Typed Analytical Tools
                    -> DuckDB / Pandas / NumPy / SciPy / Plotly
                        -> SQLite / Parquet / Session Artifacts
```

### 8.1 Architectural constraints

- Streamlit is a replaceable delivery adapter and must not own analytical business logic.
- Core operations must be callable without Streamlit for tests and a future FastAPI interface.
- Public boundaries use typed Pydantic input/output contracts.
- LangGraph owns workflow state, branching, retries, checkpoints, and human-in-the-loop pauses.
- LangChain supplies model integrations, message abstractions, tool adapters, and structured output support.
- Domain and analytical tools do not import the Streamlit UI.
- Model-provider-specific types must not leak beyond the ModelGateway adapter.

## 9. LangGraph Workflow

The single-agent state machine contains these conceptual states:

```text
PROFILE
  -> CLARIFY
  -> PLAN
  -> AWAIT_APPROVAL
  -> EXECUTE
  -> VERIFY
  -> SYNTHESIZE
  -> PROPOSE_ARTIFACT
  -> PRESENT
```

Conditional transitions may:

- Return to `CLARIFY` when semantics are unresolved.
- Return to `PLAN` when the user revises the goal.
- Return to `EXECUTE` for another approved action.
- Move to `AWAIT_APPROVAL` for consequential transformations.
- Stop safely after an execution, validation, or resource-budget failure.
- Resume from the nearest safe checkpoint after an application restart.

The graph must not be implemented as an opaque, single prebuilt ReAct loop.

## 10. Core Contracts

The implementation should define versioned equivalents of these models:

### AnalysisSession

- `session_id`
- `status`
- `source_dataset_id`
- `working_dataset_version`
- `data_profile_id`
- `semantic_annotations`
- `active_goal`
- `active_plan_id`
- `graph_checkpoint_id`
- `created_at`
- `updated_at`

### DataProfile

- Dataset identity and hash.
- Shape and field profiles.
- Type inferences and ambiguity flags.
- Data-quality findings.
- PII findings.
- Profiling version and timestamp.

### AnalysisPlan

- `plan_id`
- Analytical Goal and goal family.
- Ordered steps.
- Expected Tool Actions.
- Required approvals.
- Execution budget.
- Plan status and version.

### ToolAction

- `action_id`
- Tool name and schema version.
- Structured input.
- Structured output reference.
- Working Dataset version.
- Execution status.
- Timing, retry count, and error information.
- Verification results.

### VerifiedInsight

- `insight_id`
- Claim text.
- Evidence values.
- Scope, filters, fields, and row counts.
- Caveats.
- Tool Action references.
- Evidence Trail reference.
- Verification result.

### ChartIntent

- Artifact type.
- Analytical purpose.
- Source result reference.
- Encodings and aggregation.
- Labels and formatting intent.
- Validation constraints.

### AnalyticalArtifact

- `artifact_id`
- Artifact status: candidate or pinned.
- Type and title.
- Source result and insight references.
- Render specification.
- Created timestamp and version.

## 11. Model Strategy

### 11.1 MVP provider

- Use `gemini-3.5-flash-lite` through the Gemini API as the default model. `gemini-3.5-flash` was frequently overloaded during development, and the Flash-Lite free tier (15 requests per minute, 250,000 tokens per minute, and 500 requests per day when this revision was written) is sufficient for local use and small evaluation runs.
- Configure the exact model identifier through environment-backed settings rather than scattering it through code.
- A different model per task (for example, Flash-Lite for interpretation and Flash for planning) is permitted when evaluation shows a quality benefit.

### 11.2 Abstraction and testing

- Define a `ModelGateway` interface for structured generation and tool-selection requests.
- Provide a Gemini implementation.
- Provide a deterministic `FakeModelGateway` for automated tests.
- Do not implement automatic provider fallback in the MVP.
- Permit later hosted benchmarks with Qwen3.8-27B and `gpt-oss-120b` without changing analytical tools or graph contracts. Free tiers from Groq and Cohere were considered and deferred because their tokens-per-minute or monthly caps are tighter than Gemini Flash-Lite.
- Model calls must respect provider quotas. The Gemini gateway can rotate several configured API keys: it uses one key for up to 15 requests per minute, rests a key for 65 seconds after that budget is used or a rate-limit response arrives, moves to the next key immediately, and wraps to the first key after the last. When every key is resting, the call fails as a provider error that states the wait instead of blocking. Evaluation runs also pace requests. Using several keys must comply with the provider's terms; honoring provider `retry-after` headers remains Should-tier.

### 11.3 Model-output rules

- Use structured output for plans, tool requests, Chart Intents, and insight drafts.
- Validate all model output before using it.
- Allow one repair attempt for malformed structured output, passing validation messages back without echoing rejected input values.
- Escape untrusted model output and dataset content before placing them inside prompt delimiters.
- Record a trace for failed and retried model calls, including the error, as well as for successful calls.
- Never accept model confidence as evidence of analytical correctness.

## 12. Storage Design

The local MVP uses:

- SQLite for session, plan, action, trace, insight, and artifact metadata.
- Immutable source files inside session-scoped storage.
- DuckDB and/or Parquet for Working Dataset versions and result tables.
- JSON for versioned chart specifications and portable evidence metadata.

Writes should use temporary outputs and atomic publication where feasible. Failed or unverified artifacts must not appear as completed dashboard content.

## 13. Verification Gates

Before an insight or artifact is published, deterministic checks must verify:

1. Every referenced field exists in the relevant Working Dataset version.
2. Field types support the requested operation.
3. Filters, grouping, and aggregation are represented in the Tool Action.
4. Source and result row counts are present and plausible.
5. Returned values match the recorded tool output.
6. Statistical assumptions, sample size, and missing-data treatment are reported where applicable.
7. The claim does not exceed what the evidence supports.
8. A chart references the exact verified result it visualizes.
9. The result is not stale relative to current annotations or Working Dataset version.
10. The Evidence Trail is complete and reproducible.

## 14. Security and Resource Controls

### 14.1 Untrusted dataset content

- Treat filenames, sheet names, headers, and cell values as untrusted data rather than instructions.
- Delimit and label data samples sent to the model.
- Never execute instructions discovered inside dataset content.

### 14.2 SQL controls

- Permit a single read-only `SELECT` or `WITH ... SELECT` statement.
- Parse and validate SQL before execution.
- Permit access only to allowlisted session tables and views.
- Reject DDL, DML, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, and filesystem/network functions.
- Enforce timeout, memory, and output-row limits.

### 14.3 Agent budget

Default limits per Analysis Plan are:

- Maximum 12 Tool Actions.
- Maximum two repair attempts for a failed action.
- Tool-specific timeout.
- Total run time budget.
- Detection and rejection of repeated identical actions.

Limits must be configurable and visible in failure messages.

### 14.4 Credentials and privacy

- Load API credentials from environment variables or Streamlit Secrets.
- Never commit `.env` or secrets.
- Redact credentials from errors and traces.
- Detect likely emails, phone numbers, identifiers, addresses, and person names heuristically.
- Mask likely PII in samples sent to the model.
- Provide an option that sends no row samples to the API.
- Do not commit real user data to the repository.

## 15. Failure and Recovery Behavior

- Retry temporary model/API failures with bounded exponential backoff.
- Do not repeatedly retry schema or validation errors.
- Checkpoint graph state before consequential or expensive steps.
- Make Tool Actions idempotent where practical.
- Write tool outputs to temporary locations before verification and publication.
- Mark failed runs clearly without converting partial output into Verified Insights.
- Allow users to resume from a safe checkpoint.
- Explain when the requested analysis cannot be supported because of missing fields, inadequate sample size, unresolved semantics, unsupported causal claims, or an exceeded budget. Too few usable values for a statistical test end the run as a typed `insufficient_sample` refusal that names the affected group or field.
- Show users a plain-language failure message with a next step; keep technical details in a collapsed view and in the audit trail.
- Classify terminal failures as provider errors (quota, overload, timeout) or analysis errors so that evaluation does not count provider outages as agent mistakes.
- A new request in the same Analysis Session must not inherit errors or per-run results from an earlier request; confirmed Semantic Annotations persist.
- An Analysis Plan that references unknown field names is regenerated with the exact error, within the repair budget of Section 14.3. Field names are compared after Unicode NFC normalization and case folding; the agent never guesses a different field. A SQL step that lists no required fields is regenerated the same way.
- An insight draft that references metric identifiers absent from its evidence is regenerated once with those identifiers; drafts that still fail become Unsupported Claims. A query rejected by a Verification Gate reports which gate failed and why.

## 16. Observability and Reproducibility

Every run records:

- Session ID and trace ID.
- Graph node transitions without exposing private chain-of-thought.
- Model identifier and inference settings.
- Prompt-template version.
- Tool name, schema version, structured input, and bounded output summary.
- Working Dataset version and Source Dataset hash.
- Validation outcomes.
- Latency, retries, token usage, and estimated cost.
- Error classification.
- Created insights and artifacts.

Computations must be reproducible by preserving exact SQL or Tool Action parameters and fixed random seeds where sampling is used. A user should be able to rerun a saved Tool Action without another LLM call.

The Audit view shows, for the current request, each Tool Action's exact SQL or statistical parameters, approved fields, verification checks, error, and retry count; each Verified Insight's fields, filters, row counts, and evidence values; rejected claims with their reasons; and model-call metadata including prompt-template versions and errors.

LangSmith may be offered as an optional integration but is not an MVP runtime dependency.

## 17. Evaluation Specification

### 17.1 Dataset tiers

1. **Tiny fixtures:** tables of approximately 5–20 rows with manually verifiable results.
2. **Classic datasets:** Iris, Titanic, and Student Score.
3. **Adversarial tabular datasets:** missing values, duplicates, mixed types, malformed dates, ambiguous headers, high cardinality, prompt-injection text, and outliers.
4. **Portfolio scenarios:** sales analytics, manufacturing quality, and workforce analytics.

### 17.2 Golden case structure

Each evaluation case includes:

- Dataset fixture and content hash.
- User request.
- Required calculations.
- Numeric tolerances.
- Allowed fields and filters.
- Supported conclusions.
- Forbidden claims.
- Valid chart families.
- Expected clarification behavior.

Numerical correctness is graded deterministically. Natural-language quality is evaluated through a documented rubric, not solely by an LLM judge.

Each case declares its expected outcome — answered with Verified Insights, answered from the Data Profile, a clarification question, or a safe refusal — and may list other acceptable outcomes. Clarification behavior is graded through the outcome: the runner approves plans but never confirms a clarification on the user's behalf. The evaluation runner (`python -m tabular_analytics_agent.evaluation.runner`) grades outcome, computed values, values reported in Verified Insights, schema grounding against allowed fields, chart type, and forbidden claims, and it paces model calls to the provider quota.

### 17.3 Evaluation process

Grading version 2 binds expected query values to a metric (or an explicitly allowed alias) and optional group identity; statistical metrics use their exact metric identifiers. Insight coverage requires a persisted assertion operand and evidence from the same successful Tool Action/result, not merely a matching number elsewhere. Profile cases declare concrete expected facts. Failed runs and rejected insight drafts do not count as safe refusals. Refusal credit requires a typed refusal code and a non-empty reason: `unavailable_metric` also requires an unavailable mapping, and `insufficient_sample` comes only from the statistical tool's typed sample-size error. Other failure categories never receive refusal credit, and error messages are never matched as text. Earlier grading reports are not directly comparable with version 2. This deterministic grader does not replace semantic review of metric meaning, filters, or the rendered UI.

1. Smoke stage: at least 10 cases across at least 3 datasets, including Vietnamese headers, dirty data with prompt-injection text, and a refusal case, each run 3 times.
2. Error analysis: read failed runs and their traces, group failures by cause, and fix the most frequent cause first.
3. Every new capability adds at least one case before it is considered done.
4. The release stage expands to all four dataset tiers with at least 40 cases.
5. Holdout cases and their datasets are committed before any live run and never edited afterward. A holdout whose exact question was tried manually during development moves to `tests/evaluation_cases_holdout_contaminated/` and is reported separately from clean holdouts.

### 17.4 MVP quality gates

Rates are computed per case run and exclude runs that failed because of provider errors, which are reported separately.

- Calculation accuracy: at least 95% of expected values in answered cases appear in computed results.
- Schema grounding: 100% of fields used by successful Tool Actions are allowed for the case.
- Unsupported-claim rate: no more than 2% of Verified Insights contain a forbidden claim.
- Tool execution success: at least 95% of runs that execute tools finish without exhausting the repair budget.
- Chart validity: at least 95% of cases that expect a chart render one of the valid chart types.
- Evidence completeness: 100% of Verified Insights have an Evidence Trail.
- Clarification recall: at least 90% for cases that require clarification; unnecessary clarification is reported.
- End-to-end task success: at least 85% of runs pass every check.

Latency and API cost are recorded and reported but are not hard release gates for the first MVP.

## 18. Non-functional Requirements

- **Testability:** Core behavior can be tested without Streamlit or a live model API.
- **Replaceability:** Streamlit and the model provider are adapters, not domain dependencies.
- **Traceability:** Every published conclusion is connected to reproducible evidence.
- **Safety:** All execution paths are allowlisted, bounded, and session-scoped.
- **Recoverability:** Persisted sessions survive UI reloads and recover from safe checkpoints.
- **Maintainability:** Contracts and artifact formats are versioned.
- **Usability:** Errors state what failed, why it matters, and what the user can do next.
- **Portability:** The final MVP can be run from a documented Docker setup.

## 19. Implementation Roadmap

### Milestone 1 — Foundation

- Initialize repository and Python project tooling.
- Establish module boundaries and configuration.
- Define core Pydantic contracts.
- Add tiny fixtures and an evaluation skeleton.
- Add test, lint, type-check, and CI commands.

### Milestone 2 — Deterministic data core

- Implement secure ingestion.
- Implement Source and Working Dataset storage.
- Implement Data Profile generation.
- Add DuckDB read-only execution.
- Add baseline Verification Gates.

### Milestone 3 — Agent orchestration

- Implement ModelGateway and FakeModelGateway.
- Implement Gemini adapter.
- Implement LangGraph state and transitions.
- Add Semantic Annotation, planning, approval, retry, and checkpoint behavior.

### Milestone 4 — Insights and statistics

- Implement statistical tools.
- Implement Evidence Trails and Verified Insights.
- Add unsupported-claim and stale-result handling.

### Milestone 5 — Dashboard and export

- Implement Chart Intent validation and Plotly rendering.
- Implement Candidate and Pinned Artifacts.
- Implement Dashboard composition and MVP exports.

### Milestone 6 — Hardening and evaluation

- Add adversarial datasets and security tests.
- Add budget, timeout, prompt-injection, PII, and crash-recovery tests.
- Run and publish the evaluation report.

### Milestone 7 — Portfolio release

- Complete sales, manufacturing, and workforce case studies.
- Add architecture and workflow diagrams.
- Add Docker packaging and clean-machine verification.
- Add README, screenshots, demo video, sample traces, limitations, and roadmap.

## 20. Conditions for Migrating to React and FastAPI

Migration begins only when:

- The Streamlit MVP passes the agreed evaluation thresholds.
- Agent behavior is stable across all four dataset tiers.
- Session, insight, evidence, and chart contracts no longer change frequently.
- At least three complete demonstration dashboards exist.
- Streamlit is a demonstrated UX or scalability constraint.

## 21. MVP Acceptance Criteria

The MVP is complete when:

1. A user can clone the repository and run the documented setup from a clean environment.
2. The user can upload valid CSV and XLSX datasets and receive a Data Profile.
3. Ambiguous semantics trigger clarification when they affect the result.
4. A natural-language Analytical Goal produces an inspectable Analysis Plan.
5. The Orchestrator Agent executes only approved typed tools within its budget.
6. SQL execution is read-only and session-scoped.
7. Verified Insights include complete Evidence Trails and pass deterministic checks.
8. The user can create, refine, and pin valid dashboard artifacts.
9. A persisted session can be resumed and completely deleted.
10. The application safely refuses unsupported or unverifiable requests.
11. Automated evaluation meets every quality gate in Section 17.4.
12. Docker build, tests, linting, and type checking pass from a clean environment.
13. No secret or real user dataset is committed.
14. Product limitations are explicitly documented.

## 22. Portfolio Demonstration

The primary demonstration should take five to seven minutes and use a manufacturing-quality dataset containing realistic data-quality problems. It should show:

1. Upload and profiling.
2. Detection of data-quality risks.
3. Clarification of an ambiguous field.
4. An approved Analysis Plan.
5. SQL or statistical Tool Actions.
6. A Verified Insight with an Evidence Trail.
7. A rejected or safely qualified Unsupported Claim.
8. A validated chart pinned to the Dashboard.
9. Export of the final analytical result.

The repository should also contain sales and workforce case studies, an architecture diagram, a LangGraph workflow diagram, test and evaluation evidence, sample traces, and an explicit future-production roadmap.

## 23. Approval

Implementation begins only after this specification is reviewed and its status is changed from `Draft for approval` to `Approved`.

## 24. Revision History

### v1.1 — 2026-09-14

Targeted amendments based on live Gemini smoke runs. Decisions from v1.0 remain in force unless changed here.

- Added MVP scope tiers (Section 4.1); Working Dataset transformations moved to the Should tier.
- Journey: profile answers precede planning, clarification follows goal interpretation, and approval is required only for steps that need review (Section 6, FR-04, FR-06).
- Vietnamese goals and datasets are Must-tier (FR-05).
- Verified Insight text is rendered from typed assertions, and malformed drafts become Unsupported Claims (FR-10, Section 25.2).
- Chart tiers and KPI/table encoding rules (FR-11); export priorities (FR-13).
- Default model changed to `gemini-3.5-flash-lite`; model-output escaping and failed-call traces (Section 11).
- Failure messaging, error classification, and per-request state isolation (Section 15).
- Evaluation outcomes, runner, process, and gate denominators (Section 17).
- Added the Tool Catalog, Insight Assertion contract, and Configuration reference (Section 25).

Known gaps at v1.1: session listing, resume, and deletion in the UI; goal suggestions; export; Should-tier charts; extracting SQL filters into Evidence Trails; environment-configurable resource limits and execution budgets; and a quota-aware model gateway.

### v1.2 — 2026-09-14

Synchronizes the specification with the implemented metric grounding, session lifecycle, and export work. No v1.1 decision is reversed.

- Requested-metric mappings and the typed unavailable-metric refusal (FR-04, Section 25.4).
- Confirmed, per-session deletion and its non-transactional limit (FR-01).
- Export selection, binding, and content rules (FR-13).
- Evaluation pacing default corrected to 12 requests per minute (Section 25.3).

Known gaps at v1.2: goal suggestions (Section 6, step 5 — Must-tier, not implemented); live evaluation of the metric-mapping prompt and the Section 17.4 gates; manual Streamlit acceptance; Should-tier charts and HTML report export; extracting SQL filters into Evidence Trails; environment-configurable resource limits and execution budgets; a quota-aware model gateway; Milestone 6 hardening evidence; Docker packaging. Acceptance status is tracked in `docs/mvp-acceptance.md`.

### v1.3 — 2026-09-14

Amendments from the first manual Streamlit acceptance session. A Vietnamese header copied into an unaliased SQL expression was garbled by the model, so a correct result produced no Verified Insight.

- Calculated SQL outputs require ASCII identifier aliases; query results record their filters (Section 25.1).
- Claims name SQL filters and the two related fields of a statistic (FR-10).
- SQL steps without required fields are replanned, insight drafts with unknown metric identifiers are regenerated once, and Verification Gate failures name the failed gate (Section 15).
- Too few usable values for a statistical test become a typed `insufficient_sample` refusal instead of a failure, and evaluation credits it as a refusal (Sections 15 and 17.3).
- Row listings publish a deterministic row-count claim when no drafted claim survives and may number source rows (FR-10); KPIs are not rounded and bar charts use categorical axes (FR-11); CSV exports carry a UTF-8 byte-order mark (FR-13); the Audit view contents are defined (Section 16); contaminated holdouts are reported separately (Section 17.3).
- The Gemini gateway rotates several API keys with a per-key minute budget and a 65-second cooldown, and evaluation pacing scales with the key count (Sections 11.2 and 25.3).

## 25. Implementation Contracts

### 25.1 Tool Catalog

| Tool | Input | Output | Constraints |
|---|---|---|---|
| `read_only_sql` | One DuckDB `SELECT` or `WITH ... SELECT` over the single table named `dataset`; its source columns must equal the approved step's `required_fields`; every calculated output column needs a short ASCII identifier alias such as `avg_salary` | `QueryResult`: columns, rows, row count, truncation flag, normalized SQL, `GROUP BY` output columns, and `WHERE`/`HAVING` filters | No wildcard projections, schema catalogs, external-access functions, or other tables |
| `statistical_analysis` | The approved operation plus the parameters its rule requires (the operation rule table in `statistics.models`) | `StatisticalResult`: estimates, test statistic, p-value, adjusted alpha, effect size, assumption checks, and warnings | Runs on a deterministic reservoir sample of at most the configured query row limit |

Questions fully covered by the Data Profile are answered without a tool (Section 6, step 7).

### 25.2 Insight Assertion

- The model selects a typed assertion instead of writing the claim: `reports`, `equals`, `greater_than`, `less_than`, `positive`, `negative`, `statistically_significant`, or `not_statistically_significant`.
- Metrics are exact identifiers from the evidence catalog: `row[i].column` for query results, and names such as `pearson_r`, `p_value`, `adjusted_alpha`, or `group[...].mean` for statistical results.
- Comparison operators require a right metric; the other operators must not have one. Significance assertions use `p_value` as the left metric and must also select `adjusted_alpha`.
- The claim sentence is rendered deterministically and is published only when every Verification Gate passes.
- New Verified Insights persist their typed assertion so evaluation can distinguish values actually asserted from extra supporting evidence. Legacy insights without a stored assertion remain readable but cannot establish assertion coverage automatically.
- A statistical p-value belongs to the result's identified primary test statistic. P-value and significance claims name that test explicitly. The current correlation tool tests Pearson correlation; its supplementary Spearman coefficient is descriptive and does not share Pearson's p-value.

### 25.4 Requested Metric Mapping

- Goal interpretation returns `requested_metric_mappings`, one entry per explicitly requested metric, each keeping the user's `requested_label`. Structural questions may return an empty list; dimensions such as region or month need no entry.
- `direct` names the exact `source_fields` that measure the metric. `derived` names every source field and an explicit reproducible `derivation`. `unavailable` has no source fields or derivation and may give a `reason`.
- Source fields must exist in the Data Profile after NFC normalization and case folding. A derived metric is never answered from the Data Profile.
- Every direct or derived mapping must have all of its source fields in the `required_fields` of at least one plan step; otherwise the plan fails. A plan cannot be created while any mapping is unavailable.
- An unavailable mapping pauses for clarification. A corrected request clears earlier mappings and is interpreted again. Accepting the unavailable metric ends the run with status `refused`, refusal code `unavailable_metric`, and a non-empty reason; the session is recorded as completed. Other execution errors are never relabeled as refusals; insufficient samples use their own typed refusal code (Section 15).
- These checks make the mapping inspectable and grounded in real fields; they do not prove that the language equivalence or the derivation is semantically correct, so the UI shows the mapping for user review.

### 25.3 Configuration

| Setting | Source | Default |
|---|---|---|
| Gemini API key | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | None; required for live runs |
| Additional keys for rotation | Comma-separated values in any key variable (for example `GOOGLE_API_KEY=key-1,key-2`), `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, …, or `GEMINI_API_KEYS` | None; each key serves 15 requests per minute, then rests 65 seconds |
| Model identifier | `TABULAR_AGENT_MODEL` | `gemini-3.5-flash-lite` |
| Model call timeout | `TABULAR_AGENT_MODEL_TIMEOUT_SECONDS` | 30 seconds (minimum 21) |
| Data directory | `TABULAR_AGENT_DATA_DIR` | `.data` |
| Resource limits (`DataCoreLimits`) | Code defaults; not yet environment-configurable | 100 MB file, 10,000 query rows, 30-second query timeout, 512 MB DuckDB memory, 500 MB uncompressed XLSX, compression ratio 100, 5 profile top values |
| Execution budget (`ExecutionBudget`) | Code defaults; not yet environment-configurable | 12 Tool Actions, 2 repairs per action, 30-second model and tool timeouts, 300-second active run time |
| Evaluation pacing | Evaluation runner `--rpm` option | 12 requests per minute per configured key (below the 15 RPM free-tier limit); a provider error is retried once after 66 seconds |
