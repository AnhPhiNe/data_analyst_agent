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

- Goal suggestions (Spec Section 6, step 5) are implemented deterministically from the Data
  Profile and offered before the first question; a manual Streamlit review remains.
- The Data Overview (Should tier, FR-11) is implemented in `application/overview.py` and shown
  after ingestion without model calls; a manual Streamlit review on the user's own data remains.
  Its explorer lets the user choose a measure, calculation, group, time period, value filters,
  and a date range for any dataset, with a box plot for a numeric measure by group; field names
  are checked against the eligible fields and filter values are escaped SQL literals.
- Check every Must-tier workflow in Streamlit: upload, profile, clarification, approval, result,
  chart, pin/unpin, open another session, reopen after restart, export, and confirmed deletion.
- Live evaluation is done: 97 committed cases (10 development, 15 first holdout, 2 contaminated,
  and 12 or 10 in each of holdout sets v3 to v8), each run 3 times with provider failures reported
  separately. Holdout expectations were never changed after a run.
- Section 17.4 quality gates were all met on the post-fix holdout v7 (36/36, every check 100%).
  Earlier holdout sets (v3–v6) are development evidence after the fixes derived from them; their
  denominators are recorded above.
- Review semantic correctness and filter use in addition to deterministic number/evidence checks.
  A model-produced mapping makes a decision inspectable; it does not prove the meaning is correct.
- Milestone 6 hardening tests are in `tests/test_hardening.py` and `tests/test_settings.py`
  (see the verification record). The Section 14.4 option that sends no row samples is implemented
  as `TABULAR_AGENT_SEND_SAMPLE_VALUES=false`.
- Docker packaging is added (`Dockerfile` with runtime and check targets, `.dockerignore`, and
  pinned `constraints.txt`), but the image has not been built: Docker is not installed on the
  development machine. The clean-environment check of the documented setup is in the
  verification record.
- Documented limitations (`docs/limitations.md`), the portfolio README, and the architecture and
  agent workflow diagrams (`docs/architecture.md`) are written. Still open: screenshots, a demo
  video, sample traces, and the sales, manufacturing, and workforce case studies.

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
- Holdout v3 first run (2026-09-14, grading version 3, after ASCII field ids): 25/30 runs passed
  (83.3%); 7 of 10 cases passed at least 2 of 3 runs and 6 passed all 3. Check rates: outcome
  90%, calculations 90%, insight coverage 81%, chart 87%; schema grounding, forbidden claims,
  and profile facts 100%. No garbled field names appeared. Two provider errors were retried
  (one rate limit, one invalid request). Failures: a plan approving an extra counting field
  that the SQL did not read (2 runs), insights asserting only part of the requested values
  (2 runs), and one data-quality question answered with SQL whose "duplicate rows" formula was
  wrong (3 instead of the profile's 1) instead of from the Data Profile. The Section 17.4 gates
  for end-to-end success (85%), calculation accuracy (95%), and chart validity (95%) are not
  yet met on this set.
- Holdout v4 first run (2026-09-14, grading version 3, commit 9133194, paced at 30 requests per
  minute): 33/36 runs passed (91.7%); 11 of 12 cases passed all 3 runs, covering the public Iris
  and Titanic tables, a synthetic student table, and a messy orders table. Check rates: outcome,
  chart, schema grounding, forbidden claims, and profile facts 100%; calculations and insight
  coverage 87.5%. One rate-limit error was retried and no provider error remained. All 3 failures
  are one case: asked whether two groups' mean scores differ significantly, the agent compared
  SQL averages without a statistical test. The same pattern appeared once in the v3 development
  rerun. Gates met on this set: end-to-end success, chart validity, schema grounding, and
  unsupported claims; calculation accuracy (95%) is not met.
- Milestone 6 hardening (2026-09-15): resource limits and execution budgets are configurable
  through `TABULAR_AGENT_*` environment variables, and an invalid value stops startup with the
  variable named. New deterministic tests cover XLSX zip-bomb limits, interruption of a
  long-running query, the DuckDB memory limit, the Tool Action and model-call budgets, injection
  text in cell values, destructive SQL proposed by the model, PII sample withholding, and
  resuming a session after a simulated crash during tool execution. 339 tests passed (2 skipped),
  branch-inclusive coverage 90.66%, Ruff, formatting, and mypy passed. No live model run was
  needed because no prompt changed.
- Spec v1.4 work (2026-09-15): `TABULAR_AGENT_SEND_SAMPLE_VALUES=false` withholds frequent field
  values; with the default, profile prompts are byte-identical to the previous commit on four
  fixtures. Holdout v5 (12 cases on unseen coffee-chain and clinic datasets) was committed before
  any live run. Preparing it exposed a profiling defect on two datasets: Vietnamese ISO date
  columns were profiled as text and flagged as possible PII because ISO dates matched the phone
  heuristic; both are fixed before the run. The deterministic Data Overview was added. 353 tests
  passed (2 skipped), branch-inclusive coverage 90.76%, Ruff, formatting, and mypy passed. Known
  overview limit: in tables under 20 rows an integer id column is not flagged as identifier-like,
  so it can appear as a numeric chart.
- Holdout v5 first run (2026-09-15, grading version 3, commit b15c455, 30 requests per minute):
  29/36 runs passed (80.6%); outcome, chart, forbidden claims, and profile facts 100%;
  calculations and schema grounding 88.9%; insight coverage 85.2%. Five rate-limit retries, no
  remaining provider error. Failures: (1) the Vietnamese two-group significance case, 0/3, is a
  grader defect: the agent ran a Welch t-test with the correct group means, but statistical group
  labels are JSON-encoded with ASCII escapes (`group[str:"Có"]`) while the case spells
  `group[str:"Có"]`, so exact metric matching failed; (2) the e-wallet order count, 0/3, returned
  the correct count but read the identifier field `Mã đơn` (COUNT of that field), outside the
  case's allowed fields, a pattern related to the extra counting field seen in holdout v3;
  (3) one ANOVA run asserted only some group means (model variance). Expectations are unchanged.
- Grading version 4 (2026-09-15) compares statistical group labels after decoding their JSON, so
  escaped and plain spellings of the same value match. Re-grading the stored holdout v5 runs
  without any model call gives 32/36 (88.9%): calculations 100%, insight coverage 96.3%, schema
  grounding 88.9%. Only the three Vietnamese t-test runs changed. Both scores are reported; the
  first-run score under grading version 3 remains 29/36.
- Counting rule (plan-v7, tool-request-v12): counting rows lists no identifier field and uses
  COUNT(*). It addresses a failure class seen on two datasets (holdout v3 and v5), so holdout v5
  is development evidence for counting questions from now on; holdout v6 must measure it.
- Holdout v6 first run (2026-09-15, grading version 4, commit 314ef37, 30 requests per minute):
  30/36 runs passed (83.3%); outcome 91.7%, calculations 85.2%, chart 81.0%, insight coverage
  81.5%, schema grounding 96.3%, forbidden claims and profile facts 100%. Two rate-limit retries,
  no remaining provider error. An earlier launch of the same run stopped after one run because
  of a log filter error in the launch command; that run was not viewed or graded, and the suite
  was restarted from the beginning in a new directory. Failures:
  (1) the grouped maintenance-reading count failed planning in all three runs: interpretation
  mapped "readings" directly to the identifier `reading_id`, the plan-v7 counting rule correctly
  left that identifier out, and metric grounding then rejected the plan without a repair. This
  is a conflict introduced by the counting rule, not a model failure alone;
  (2) one anomaly-rate run mapped the rate to `COUNT(reading_id)` and approved that identifier
  (the SQL itself used COUNT(*)), the same mapping cause;
  (3) one April-fines run listed 60 rows instead of summing them;
  (4) one correlation run asserted only significance, not the coefficient (partial insight
  coverage, also seen in holdout v3 and v5).
  Gates met on this set: forbidden claims and profile facts; end-to-end success (85%),
  calculation accuracy (95%), and chart validity (95%) are not met.
- After holdout v6, two root fixes (not per-case patches): (A) counting rows or records needs no
  requested-metric mapping (semantic-v13), removing the interpretation/grounding conflict the
  counting rule introduced; (B) synthesis deterministically reports each statistical result's
  headline estimates (group means, or a lone coefficient) the model omits, addressing partial
  insight coverage seen across holdout v3, v5, and v6. No rule was added for the single row-listing
  failure (seen once). These change model-facing behavior, so holdout v6 is development evidence
  from now on and holdout v7 must produce the post-fix score. 368 tests passed (2 skipped),
  branch-inclusive coverage 90.90%, Ruff, formatting, and mypy passed.
- Holdout v7 first run (2026-09-15, grading version 4, commit 4d17a06, 30 requests per minute):
  36/36 runs passed (100%); every check rate — outcome, calculations, chart, insight coverage,
  schema grounding, forbidden claims, and profile facts — is 100%. Four rate-limit retries, no
  remaining provider error. The two unseen datasets are a Vietnamese admissions table and an
  English farm-harvest table; the cases were written to exercise the two post-v6 fixes. The
  grouped and filtered record counts (fix A) planned and answered without failing on an identifier
  field, and the two-group and multi-group means and the correlation coefficient (fix B) were all
  covered. This is the post-fix score: every Section 17.4 gate — end-to-end success (85%),
  calculation accuracy (95%), chart validity (95%), schema grounding, and unsupported claims — is
  met on this set. It is one run of 12 cases (3 each), not a large sample; model variance can
  still lower a future run, which is why the earlier partial-coverage class was fixed at the root
  rather than tuned away.
- Hard holdout v8, measurement only (2026-09-15, grading version 4, commit c0833bb, 30 requests
  per minute): 24/36 runs passed (66.7%); outcome 91.7%, calculations 66.7%, chart 83.3%, insight
  coverage 62.5%, schema grounding 91.7%, forbidden claims and profile facts 100%. One rate-limit
  retry, no remaining provider error. Per case: all 3 runs passed for text-formatted costs and
  shipping fees, a synonym, the top-3 regions, the unavailable conversion rate, the ambiguous top
  category, and the most-missing column; 2 of 3 for the latest-month comparison; 1 of 3 for
  excluding inconsistently spelled cancelled orders; 0 of 3 for Hanoi revenue with inconsistent city
  spellings, the ambiguous best customer (answered instead of clarified), and the pooled average of
  the two busiest countries. No deterministic defect was found; the agent was not changed, and the
  findings are documented in `docs/limitations.md` Section 10.
- Clean-environment check of the documented setup (2026-09-15, commit e0d8216): a fresh clone
  without `.env` or an existing virtual environment, a new Python 3.12.14 virtual environment, and
  `pip install --constraint constraints.txt --editable ".[dev]"` installed 101 packages from PyPI.
  In that environment `ruff check .`, `ruff format --check .`, and strict `mypy` passed, and
  pytest passed 368 tests (2 skipped) with 90.90% branch-inclusive coverage. This verifies
  criterion 1 on Windows. The first attempt failed for two environmental reasons, both documented:
  the source repository is owned by another Windows account, which blocks `git clone` until the
  path is marked safe for that command, and a deep working folder exceeded the Windows path-length
  limit while installing pyarrow. The Docker image build (criterion 12) is still unverified
  because Docker is not installed on the development machine.
- Fixes after the hard holdout (2026-09-15, approved by the user): (A) inconsistent-spelling
  profile warnings plus `LOWER(TRIM(...))` SQL guidance (tool-request-v13); (B) guidance that plan
  steps cannot read one another's results, so dependent questions use one query with a CTE or
  subquery (plan-v8); (C) long query results keep their first rows in the insight evidence instead
  of none. On the v8 fixtures the new warning flags exactly the city and status fields that caused
  failures, and the development fixture `orders_dirty.csv` gets no warning. Vague ranking requests
  were not changed. 370 tests passed (2 skipped), branch-inclusive coverage 90.95%, Ruff,
  formatting, and strict mypy passed. Holdout v8 is development evidence from now on; holdout v9
  measures the fixes.
