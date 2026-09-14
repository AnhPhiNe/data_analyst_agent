# MVP acceptance checklist

This checklist tracks acceptance against Spec v1.1. Implementation, deterministic tests, and live
acceptance are separate claims. A passing unit suite is not a passing live-agent evaluation.

## Current implementation batch

| Spec requirement | Implementation work | Acceptance evidence required |
|---|---|---|
| FR-04/05: clarify materially ambiguous goals | Structured requested-metric mappings, source-field checks, and a clarification gate | Synonyms and derived metrics accepted; missing metrics cannot proceed to planning; corrected requests are reinterpreted |
| FR-10 and Section 21.10: unsupported requests | Explicit refusal metadata distinct from execution failure | Known unsupported request stops safely; provider errors and malformed drafts never earn refusal credit |
| FR-01: session lifecycle | List, open, and explicitly delete persisted sessions from Streamlit | Restart restores checkpoint and dashboard; switching clears prior UI state; deletion is isolated to the selected session |
| FR-13: CSV and JSON export | In-memory result downloads and reproducible verified-insight metadata | Unicode and CSV quoting; spreadsheet-formula safety; only insight-referenced results, bound to session/dataset/version/semantic annotations; no prompts or credentials |

These items are in integration review until the verification record below is completed.

### Current contracts and limitations

- Generation output must include `requested_metric_mappings`; an explicit empty list is permitted
  for structural requests. Each mapping is direct, derived, or unavailable. Source fields are
  checked against the profile and mapped inputs must appear in a plan step. The model still judges
  language equivalence and the requested derivation; these checks do not prove either is correct.
- An unavailable metric pauses for clarification. A corrected request is interpreted again;
  accepting that the metric is unavailable produces the typed `unavailable_metric` refusal.
  Too few usable values for a statistical test produce a typed `insufficient_sample` refusal
  from a dedicated error type; other execution errors are never relabeled as refusals.
- Session switching clears transient conversation and approval widgets. Deletion requires a fresh
  confirmation bound to the selected session. It is permanent and limited to the selected namespace;
  it is not a forensic secure erase or a transaction spanning both SQLite and filesystem storage.
- UI downloads currently cover query tables referenced by Verified Insights and JSON evidence
  metadata, including statistical results. CSV formula-like text/header cells are apostrophe-prefixed;
  numeric values remain numeric. JSON does not include model prompts, traces, or provider settings.
- Legacy insights without assertions remain readable, but do not establish assertion coverage in
  evaluation. Legacy sessions and new generated outputs have different compatibility requirements.

## Release work still required

- Implement the Must-tier goal-suggestion flow (Spec Section 6, step 5). As of Spec v1.2 no
  suggestion code exists; the user must type every goal.
- Check every Must-tier workflow in Streamlit: upload, profile, clarification, approval, result,
  chart, pin/unpin, open another session, reopen after restart, export, and confirmed deletion.
- Run live smoke evaluation with grading version 2 or later: at least 10 cases, 3 datasets,
  and 3 runs per case. Keep provider failures separate from analysis failures.
- Expand to at least 40 release cases across all four dataset tiers in Spec Section 17.1.
  Reserve unseen cases for holdout testing; do not change their expectations to fit model output.
- Publish all Section 17.4 quality gates with their actual denominators. The current aggregate
  case-check pass rate alone does not establish per-value accuracy, tool success, or all other gates.
- Review semantic correctness and filter use in addition to deterministic number/evidence checks.
  A model-produced mapping makes a decision inspectable; it does not prove the meaning is correct.
- Complete bounded-resource, prompt-injection, PII, and crash-recovery evidence for Milestone 6.
- Provide Docker packaging and verify setup/build/test from a clean environment.
- Complete sales, manufacturing, and workforce demonstrations; diagrams, screenshots, demo video,
  sample traces, documented limitations, and the portfolio README.

HTML report export, extra chart families, and quota-aware gateway work remain Should-tier.
Additional providers, PDF, and notebook exports remain Could-tier. They do not take priority over
the outstanding Must-tier flows and reliability gates.

## Verification record

- Baseline `44cca19`: 233 deterministic tests passed, overall branch-inclusive coverage 90.92%,
  Ruff and mypy passed. No live Gemini or Streamlit acceptance was claimed for that checkpoint.
- Metric mapping, session lifecycle, export, and UI batch (2026-09-14): 300 deterministic tests
  passed (2 skipped), branch-inclusive coverage 90.14%, Ruff, formatting, and mypy passed. The
  semantic-v8 interpretation prompt has not been evaluated live, and no manual Streamlit
  acceptance has been performed yet; no release acceptance is claimed.
- Holdout set (2026-09-14): 12 cases on 3 unseen datasets (Vietnamese workforce, English sales
  with a derived metric, dirty sensor data with prompt-injection text) in
  `tests/evaluation_cases_holdout/`. Expectations were computed independently with pandas and
  SciPy and committed before any live run; they must not be edited after model output is seen.
- Holdout contamination (2026-09-14): two cases whose exact questions were tried manually in
  Streamlit (average salary by department, revenue per employee) moved unchanged to
  `tests/evaluation_cases_holdout_contaminated/` and are reported separately. The other workforce
  cases share that dataset, which the user has viewed, so their results carry that caveat.
- Holdout v2 (2026-09-14): 5 cases on an unseen 30-column, single-sheet XLSX shipments table
  (synonym field, date-range filter, derived ratio, unavailable metric beside a similar field, and
  missing-value profile), computed with pandas and committed before any live run.
- First live holdout run (2026-09-14, 8 rotated keys, no provider errors): clean holdout 38/45
  runs passed (84.4%; 12 of 15 cases passed all 3 runs); contaminated holdout 4/6. Clean-run
  check rates: outcome 91%, calculations 83%, insight coverage 80%, chart 81%; schema grounding,
  forbidden claims, and profile facts 100%. Failures traced to garbled field names during goal
  interpretation, t-test field mismatches without detail, a translated filter value, a repeated
  unaliased expression, text month values rejected by line charts, and an ambiguous
  month-to-month question answered with differences (left unchanged to avoid tuning to one case).
- The fixes above were derived from these holdout failures, so later runs on these holdout sets
  are development evidence only. An unbiased post-fix score needs a new holdout set committed
  before its first run.
