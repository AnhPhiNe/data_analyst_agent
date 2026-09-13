# Milestone 4 — Insights and Statistics

Milestone 4 adds deterministic statistical Tool Actions and a verified insight-publication path to
the checkpointed single-agent workflow.

## Supported statistics

- Descriptive statistics for one or more numeric fields.
- Mean confidence intervals using the Student t distribution.
- Pearson and Spearman association measures.
- Welch t-test and Mann–Whitney test for two groups.
- Chi-square association with Cramér's V.
- One-way ANOVA with eta squared and Kruskal–Wallis with epsilon squared.
- Simple linear regression with slope and R squared.
- Single-predictor logistic regression with an explicit positive class and odds ratio.

Every result records the operation, exact parameters, source fields, sample and group sizes,
missing-data handling, applicable assumption checks, estimates, p-value, adjusted alpha, effect
size, statistical significance, practical significance, and non-causal caveats. Multiple-testing
family size is derived from the approved plan rather than trusted from model output. Linear and
logistic regression record unsupported diagnostics as `NOT_CHECKED` instead of silently assuming
them. The orchestration policy fixes inferential alpha at 0.05, and requests using a one-sided
alternative are rejected for operations whose implementation is two-sided only.
Two-group requests record an explicit ordered pair of group identities, so directional hypotheses
and signed effect sizes do not depend on source-row or reservoir-sample order. Group labels use a
type-tagged encoding to keep values such as numeric `1` distinct from text `"1"`.

## Bounded execution

`StatisticalTool` validates dataset identity and field types before extraction. It selects only the
requested fields through `TabularDataCore` and uses a reservoir sample capped by the data-core row
limit. Sampling is reproducible because the method, maximum rows, and random seed are recorded in
the Tool Action. Statistical calculations operate on this bounded in-memory frame.

## Insight publication

After every approved plan step succeeds, the graph synthesizes structured insight drafts from an
evidence catalog. Catalog entries expose exact metric names and deterministic values; Tool Actions
that reference a likely PII field are excluded from the model prompt.

The catalog is capped at 200 values, prioritizes statistical summaries, and omits row-level query
results above 50 rows. The agent must request a smaller aggregate before those values can be used for
synthesis.

`publish_insight` independently verifies:

1. The referenced Tool Action succeeded and passed its gates.
2. Dataset identity and Working Dataset version are current.
3. Every source field exists in the Data Profile.
4. Every named evidence metric exists in deterministic output.
5. The Tool Action output reference identifies the exact result being cited.
6. A structured report, comparison, direction, or significance assertion is true for the selected
   evidence; the final claim text is then rendered deterministically with human-readable metric
   labels.

Passed drafts become `VerifiedInsight` records with a complete Evidence Trail. Failed drafts become
`UnsupportedClaim` records and never appear as verified. Evidence records preserve dataset and
semantic versions, row counts, filters, missing-data treatment, exact Tool Action inputs, computed
values, and caveats. `invalidate_stale_insight` converts prior insights to `StaleInsight` when the
Working Dataset version or confirmed Semantic Annotations change.

## Test strategy

Tests use manually checkable frames and real session-scoped DuckDB files. They cover all supported
statistical operations, invalid or degenerate data, seeded sampling, type and identity validation,
evidence publication, structured overclaim rejection, stale invalidation, PII exclusion,
and end-to-end SQL and statistical orchestration. Model calls remain deterministic through
`FakeModelGateway`; automated tests require no API key or network access.
