# Tabular Analytics Agent — Product and Technical Specification

**Status:** Approved
**Target:** Local-first MVP and AI Engineer portfolio project
**Primary interface:** Streamlit
**Last updated:** 2026-09-13

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
5. The agent asks for Semantic Annotations when ambiguity could materially change an answer.
6. The application proposes three to five relevant Analytical Goals.
7. The user selects a suggestion or provides a different goal.
8. The agent displays a concise Analysis Plan.
9. The application requests approval before a plan containing consequential data-cleaning decisions.
10. The graph executes typed Tool Actions within configured budgets.
11. Verification Gates check results and provenance.
12. The agent presents Verified Insights, caveats, result tables, and Candidate Artifacts.
13. The user may ask follow-up questions, revise an analytical assumption, or pin artifacts to the Dashboard.
14. The user exports the analysis or deletes the entire session.

## 7. Functional Requirements

### FR-01 — Session management

- Create, list, open, resume, complete, fail, and delete Analysis Sessions.
- A session status is one of `running`, `waiting`, `failed`, or `completed`.
- A session operates within its own storage namespace.
- Reloading Streamlit must not silently lose a persisted session.
- Deleting a session removes its Source Dataset, Working Dataset, artifacts, metadata, and local traces.

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

- Detect field meanings, units, roles, or date semantics that are materially ambiguous.
- Permit the agent to state a hypothesis but require user confirmation before relying on it.
- Save confirmed meanings as Semantic Annotations for the current session.
- Re-enter clarification if a later request exposes unresolved ambiguity.

### FR-05 — Analytical goals

The MVP supports these goal families:

- Data quality.
- Summary.
- Comparison.
- Trend.
- Relationship.
- Driver Exploration.

Driver Exploration may identify Statistical Associations but must not imply causation.

### FR-06 — Analysis planning

- Convert an Analytical Goal into a user-visible Analysis Plan.
- A plan contains steps, expected Tool Actions, required fields, intended outputs, and known caveats.
- Do not expose private chain-of-thought.
- Request approval before destructive or semantically consequential transformations.
- Permit the user to reject or revise a plan.

### FR-07 — Working Dataset transformations

- Keep the Source Dataset immutable.
- Apply analysis transformations only to a Working Dataset.
- Automatically permit reversible normalization such as trimmed column names and explicit null representations.
- Require confirmation before dropping rows, imputing values, resolving ambiguous types, removing duplicates, or treating outliers.
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

- A concise natural-language conclusion.
- Computed supporting values.
- Referenced fields.
- Filters and analysis scope.
- Source and result row counts.
- Tool Action identifiers and exact reproducible parameters.
- Relevant caveats and uncertainty.
- Verification status.
- An optional linked artifact.

If required evidence is missing or validation fails, the conclusion is an Unsupported Claim and must not be displayed as a Verified Insight.

### FR-11 — Charts and dashboard

- The agent proposes a structured Chart Intent rather than frontend code.
- A deterministic chart tool validates the intent and produces a Plotly-compatible specification.
- Supported initial artifacts are KPI card, table, histogram, box plot, bar chart, line chart, scatter plot, heatmap, stacked bar chart, and missing-value chart.
- Chart selection considers analytical goal, field types, cardinality, sample size, and readability.
- Pie charts are not selected by default.
- Candidate Artifacts appear separately from Pinned Artifacts.
- The user controls which artifacts form the Dashboard.

### FR-12 — Conversational follow-up

- Follow-up requests reuse the current session's profile, annotations, approved transformations, and Evidence Trails.
- The agent may revise the Analysis Plan when the goal changes.
- Earlier results must not be silently reused after a relevant transformation or annotation changes; affected results become stale and require re-verification.

### FR-13 — Export

The MVP exports:

- An interactive or self-contained HTML analytical report where feasible.
- CSV files for result tables.
- Machine-readable metadata for Verified Insights and Evidence Trails.
- Reproducible SQL or typed Tool Action parameters.

PDF and generated notebooks are stretch goals.

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

- Use `gemini-3.5-flash` through an API as the initial production model.
- Configure the exact model identifier through environment-backed settings rather than scattering it through code.

### 11.2 Abstraction and testing

- Define a `ModelGateway` interface for structured generation and tool-selection requests.
- Provide a Gemini implementation.
- Provide a deterministic `FakeModelGateway` for automated tests.
- Do not implement automatic provider fallback in the MVP.
- Permit later hosted benchmarks with Qwen3.8-27B and `gpt-oss-120b` without changing analytical tools or graph contracts.

### 11.3 Model-output rules

- Use structured output for plans, tool requests, Chart Intents, and insight drafts.
- Validate all model output before using it.
- Allow one repair attempt for malformed structured output.
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
- Explain when the requested analysis cannot be supported because of missing fields, inadequate sample size, unresolved semantics, unsupported causal claims, or an exceeded budget.

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

### 17.3 MVP quality gates

- Calculation accuracy: at least 95%.
- Schema grounding: 100% of referenced fields exist.
- Unsupported-claim rate: no more than 2%.
- Tool execution success: at least 95%.
- Chart validity: at least 95%.
- Evidence completeness: 100% of Verified Insights have an Evidence Trail.
- Clarification recall: at least 90% for materially ambiguous cases.
- End-to-end task success: at least 85%.

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
11. Automated evaluation meets every quality gate in Section 17.3.
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
