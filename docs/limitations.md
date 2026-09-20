# Product limitations

This document satisfies Spec Section 21, criterion 14. It states what the MVP does not guarantee,
with the evidence behind each statement. Read it before relying on a result.

## 1. Correct numbers are verified; correct questions are not

Verification Gates prove that every number in a Verified Insight comes from a successful,
read-only Tool Action on the current dataset, and that the claim text is rendered from that
evidence. They do not prove that the calculation answers the user's question.

- A syntactically valid but semantically wrong formula still produces verified numbers. During
  evaluation, one run listed matching rows instead of summing them, and an earlier run computed
  duplicate rows with a wrong formula.
- A requested-metric mapping makes the chosen fields inspectable. It does not prove that the
  language equivalence or derivation is right, which is why the plan and mapping are shown to the
  user.

## 2. Model behavior varies between runs

The planner and insight drafts come from `gemini-3.5-flash-lite`, whose sampling cannot be fixed.
The same question can receive a different plan, SQL, chart type, or set of reported values.

| Holdout set (first run) | Passed | Notes |
|---|---|---|
| v4 | 33/36 (91.7%) | classic and messy tables |
| v5 | 29/36 (80.6%) | 32/36 after the grader fix for Vietnamese group labels |
| v6 | 30/36 (83.3%) | failures included a regression later fixed |
| v7 | 36/36 (100%) | clear questions and clean data, written to test the latest fixes |
| v8 (hard) | 24/36 (66.7%) | messy values, vague and multi-step questions; see Section 10 |
| v9 | 20/36 (55.6%) | measured the post-v8 fixes; 6 failures were chart-only; see Section 10 |
| Release suite, earlier code | 103/118 (87.3%) | 40 cases on unseen datasets; see Section 12 |
| Final holdout, earlier code | 29/29 (100%) | 10 cases on two more unseen datasets; see Section 12 |
| Release suite, this code | 108/120 (90.0%) | the same 40 cases after the full-data round; see Section 13 |
| Final holdout, this code | 30/30 (100%) | the same 10 cases after the full-data round; see Section 13 |

The gap between v7 and v8 is the most useful reading of these numbers: the agent is reliable on
clear questions over clean data and much weaker when values are inconsistent or the question is
vague. Each set has 12 cases run 3 times. That sample is small: a single case failing all runs moves the
score by 8.3 points, and a perfect score is consistent with a lower long-run rate. Later sets were
written by the same author who fixed the earlier failures. The release suite has 40 cases
whose questions were written outside the repository; 2 of its 120 runs were provider errors.

## 3. Data sent to the model provider

Details are in Spec Section 14.5. In short, the model receives field names and kinds, row counts,
the most frequent values of each non-PII field (5 by default), tool error messages, and verified
result values of at most 50 rows.

- `TABULAR_AGENT_SEND_SAMPLE_VALUES=false` withholds the frequent values but not field metadata or
  result values, and filters on text values then fail more often.
- Free API tiers may allow the provider to use submitted content. Real customer data needs a paid
  tier or an enterprise offering, checked against the provider's terms.
- Keeping all data on the machine requires a local model adapter, which is not implemented.

## 4. Privacy detection is heuristic

- Possible PII is detected from personal-data column names in English and Vietnamese,
  compared after removing diacritics (for example `full_name`, `Họ và tên`, `Địa chỉ`,
  `SĐT`, `CCCD`), and from values that look like email addresses or phone numbers. The word
  list is short and generic: a column called only `Tên` or `name`, or one named in another
  language, is not detected.
- Personal names or addresses inside other columns are not detected, and such fields are
  sent to the model like any other field.
- Dates written with dashes in a non-ISO order, such as `03-04-2025`, look like phone numbers
  and flag their column as possible PII, which withholds its values from the model. The
  release suite's recycling dataset shows this; no release case reads that column.
- Possible PII fields send no sample values and are not used to draft claims, so a question
  whose answer names a person, such as the customer who spent the most, gets no Verified
  Insight.
- An identifier-like warning requires at least 20 non-missing rows, so an id column in a smaller
  table is treated as an ordinary field.
- Values are checked for email and phone shapes in the first 50 non-missing rows of a column, so
  personal data that appears only in later rows of an otherwise ordinary column is not detected.

## 5. Data preparation is limited

- Only one rectangular table per session: CSV, or one selected XLSX sheet, up to 100 MB by default.
  There are no joins across files.
- Category values are not normalized. `Hà Nội`, `Hà Nội ` (trailing space), and `hà nội` stay
  different values in the data. The profile flags such fields. A statistical test groups the
  flagged field's spellings together and labels each group with its most frequent spelling, so a
  comparison is not split one group per spelling. SQL is different: the guidance asks the model for
  `LOWER(TRIM(...))` comparisons, but whether the generated query follows it depends on the model.
  Spellings that differ in other ways, such as `Ha Noi` or `HN`, are not detected anywhere.
- Numbers stored as text, such as `1.250.000` or `$4.99`, stay text in the profile. The agent must
  convert them in SQL, which it may not do.
- Dates in ISO form (`2026-03-02`) are detected in any language. Other date formats are converted
  only when the column name is an English date word and every value parses; day-first and
  month-first forms such as `03/04/2026` can be read the wrong way.
- Working Dataset transformations (cleaning, recoding) are a Should-tier feature and are not
  implemented.

### Rows dropped by a SQL aggregate are not counted for you

A statistical result reports how many observations it used and how many rows it excluded for
missing values. A SQL aggregate does not. `AVG(rating)` over a column with empty cells silently
ignores them, exactly as SQL defines, and the published claim states the average with no mention of
how many rows were left out. The number is correct for the rows that had a value; what is missing
is the disclosure.

This is a gap in the full-data contract, which promises that excluded observations are stated. The
contract is met on the statistical path and not on the SQL path, because `QueryResult` has no field
to carry the count. Closing it means threading a per-column excluded count from execution through
verification, claims, and exports, which was judged too broad a change to make while closing the
MVP. Until then, read a SQL average or sum together with the field's missing count in the Data
Profile.

## 6. Resource and quota limits

- Query results returned to the UI or model contain at most 10,000 rows; this is a presentation limit.
  Statistical calculations use every row in the approved analysis scope and record the dataset,
  population, loaded, valid, and missing-row counts. Automatic sampling is not used by the MVP. If
  full-data reading or calculation exceeds the timeout or resource limits, the action fails and does
  not fall back to a smaller sample or publish a partial result. Limits are configurable (Spec
  Section 25.3).
- Historical evidence created while the implementation used reservoir sampling is legacy evidence.
  It is labeled with its original sampling metadata and must be recomputed before current publication
  or export; it is not silently treated as a full-data result.
- A question uses 3 to 5 model calls; the release suite averaged 3.5 per run. Failed Tool Actions
  count toward the Tool Action budget, so a long plan whose steps need repairs can exhaust it.
- The free tier allows 15 requests per minute per API key;
  several keys rotate, but keys created in the same Google project may share one quota. Using
  several keys must comply with the provider's terms.

## 7. Charts and the Data Overview

- The agent proposes KPI, table, histogram, bar, line, or scatter charts, and the chosen type can
  vary. Answers backed only by a statistical test have no chart. When a proposed chart fails
  validation, the verified result is shown as a table instead, with the reason recorded in the
  chart's constraints; a result too large for a table (over 500 rows) gets no chart.
- A KPI and a claim show every digit of a whole number and at most 15 significant digits of a
  decimal, so floating-point noise such as `3.1000000000000005` shows as `3.1`. Whole numbers
  above 2^53 (about 9 quadrillion) cannot be drawn exactly, so they get no KPI chart. DuckDB `DECIMAL` results are
  converted to floating point, so a value with more than about 15 significant digits loses
  precision; numbers read from CSV are floating point from the start.
- The Data Overview and explorer are descriptive. They run no significance test, show at most 20
  groups, filter on at most 3 fields, and never produce Verified Insights.

## 8. Deployment model

- Streamlit runs as a single-user local application with no authentication or multi-user access
  control.
- Session files, checkpoints, and artifacts are stored unencrypted on local disk.
- Deleting a session removes its files and artifact records in sequence, not in one transaction,
  and is not a forensic secure erase.
- A file that is inspected but never ingested leaves a session folder on disk that the application
  does not list and cannot delete; remove it from the data directory by hand.

## 9. Evaluation caveats

- Almost all evaluation datasets are synthetic, generated for this project; two public tables (Iris
  and Titanic) were also used.
- Expected values are computed independently with pandas and SciPy, but grading matches values
  deterministically. A correct result under an unexpected grouping column name can be graded as
  missing.
- Evaluation runs are paced for the free tier and use the author's API keys; results depend on the
  model version available on the run date.
- The tool execution gate counts a planned run that ends in failure against the gate whichever step
  failed, because runs do not record which repair budget ran out. Evidence completeness checks that
  each Verified Insight names its Tool Actions; the published-insight contract already requires a
  full Evidence Trail, so this gate mainly guards stored or hand-edited state.
- Grading is lenient about output names. A grouped or single-row expected value is matched by its
  group and value, so a different column holding the same value also counts. Re-checking the stored
  release runs with a strict name check found 151 of 176 expected values instead of 155, so
  calculation accuracy reads 85.8% instead of 88.1%; 2 of 118 runs, both of the powder-coating
  defect-rate case, matched only through a differently named column. The official grades stand as
  measured.
- A case's allowed filters and supported conclusions are recorded but never graded, so a correct
  number reached with the wrong filter, or a conclusion outside the case's list, is not caught
  automatically. Filters and claim wording are reviewed by hand instead.
- Insight coverage is now partly guaranteed by the agent itself: after the final fix round, a small
  grouped or single-row result reports its remaining values deterministically, so that gate measures
  the model's own coverage less than it did before.

## 10. What the hard holdout exposed

Holdout v8 was written to measure limits, and its failures were recorded without changing the
agent. It passed 24 of 36 runs: calculations 66.7%, insight coverage 62.5%, chart validity 83.3%,
schema grounding 91.7%, outcome 91.7%, and forbidden claims and profile facts 100%.

**A verified number can still be the wrong answer to the business question.**

- Asked in Vietnamese without diacritics for revenue in Hanoi, where the city was typed as
  `Hà Nội`, `Hà Nội ` (trailing space), and `hà nội`, every run filtered on the exact value
  `Hà Nội` and reported about 57% of the true total. The claim was accurate for that filter and
  named it, but it did not answer the question.
- Asked for order value excluding cancelled orders, where the status was spelled `cancelled`,
  `Cancelled `, and `CANCELLED`, two of three runs lowercased the value but did not trim it and
  included the orders with a trailing space.

**Vague requests are not recognized consistently.** "Which category is our top performer?" was
answered with a clarification question in all runs, but "Khách hàng tốt nhất của chúng tôi là ai?"
("Who is our best customer?") was answered in all runs by assuming revenue. The two measures pick
different customers in that dataset.

**Multi-step questions are fragile.** Plan steps do not pass results to one another; a later step
must repeat the earlier logic in its own query. Asked for the average order total of the two
countries with the most orders, all runs reported per-country averages instead of one pooled
average, and one run's second step ranked countries by average value rather than order count, so it
reported the wrong countries.

**Large results give thin answers.** Model-bound evidence is capped at 50 rows. A 60-row customer
ranking produced only the claim that the result has 60 rows, and the ranking itself was visible
only in the result table.

**Coverage of query results still varies.** Headline statistical estimates are reported
deterministically, but query values are chosen by the model. In one run comparing the two latest
months, only one month's revenue was asserted.

What held up on the same messy data: costs stored as `1.250.000` and fees stored as `$4.99` were
converted correctly in every run, a synonym ("basket size") was mapped to `items_per_order`, a
near-miss metric (conversion rate without traffic data) was correctly treated as unavailable, the
most-missing column was answered from the profile, and instruction text inside a cell had no effect
on any claim.

### Changes made after holdout v8

Three general fixes followed, so holdout v8 is now development evidence:

- Inconsistent spellings that differ only by case or surrounding spaces are flagged in the profile,
  and SQL guidance compares such fields with `LOWER(TRIM(...))`.
- Guidance states that plan steps cannot read one another's results, so a question that uses one
  result to choose rows for another should be a single query with a CTE or subquery.
- A long query result now keeps its first rows (at most 50, in query order) in the model's evidence
  instead of withholding every row.

Vague ranking requests such as "best customer" were deliberately left unchanged, because a rule
that asks for clarification on every superlative would also block clear questions such as "highest
revenue region". Holdout v9 measures what the fixes changed.

### What holdout v9 showed

Holdout v9 passed 20 of 36 runs. Its calculation and insight coverage rates were both 87.9%, but
the headline score is low because of failures the fixes did not target. It uses different
datasets from v8, so the two scores are not directly comparable.

- **Inconsistent spellings are now handled.** Every run that filtered on a field spelled with
  different case or trailing spaces used `LOWER(TRIM(...))` and computed the right value.
- **Dependent questions use one query, but "pooled" is still misread.** Every run wrote a CTE or
  subquery. Asked for one churn rate across three industries "counted together", all runs still
  reported a rate per industry.
- **Table-qualified field ids failed.** Field ids such as `c5` were rewritten to real names only
  when unqualified, so one run's `d.c5` inside a CTE join failed and the repair budget ran out.
  This has since been fixed: ids qualified by the `dataset` table or its alias are rewritten.
- **Charts remain the weakest step.** For one-row ranking answers, the agent drew a KPI where the
  cases (written by the project author) allowed only bar or table charts, and twice produced an
  invalid KPI intent. Two chart intents garbled the Vietnamese column name `Kênh đặt` instead of
  using its id.
- **Vague requests are still answered.** "Which branch is performing best?" was answered by
  assuming revenue in two runs and failed in the third.
- The long-result fix was not exercised: both ranking questions returned a single row.

### Changes made before the release suite

- An invalid chart proposal now falls back to the verified result table.
- A ranking request that names no measure, such as "which region is best", asks which field defines
  the ranking when two or more numeric fields could. Checked only on development fixtures: 4 of 4
  such runs asked and 4 of 4 controls that named the measure were answered. An English request
  was clarified as an unavailable "performance" metric rather than by listing the candidate fields,
  so the question it asks is less helpful than the Vietnamese one. The release suite measures this
  on unseen data.
- Still observed: a model can alias a grouping column despite the guidance, for example
  `"Tháng" AS thong`. The values stay correct, but the claim then shows `thong = 1` instead of the
  column name, and grading cannot match the group.

## 11. Specified but not implemented

- Person names and addresses inside the values of columns whose names do not mark personal
  data are not detected as possible PII (see Section 4).

## 12. What the release suite showed

This section records the release suite's **first** run, on an earlier state of the code. Section 13
records what the code in this repository scores on the same suite. The release suite (40 cases on
nine datasets not used in development, three runs per case, grading version 6) was run at commit
`096ff45` on 2026-09-15. Its expectations were not
changed afterwards. 118 runs were graded; 2 runs ended in provider errors after retries (9 retries
in total) and are reported separately. 103 of 118 runs passed (87.3%; 95% Wilson interval about
80–92%).

| Section 17.4 gate (threshold) | Result | Met |
|---|---|---|
| Calculation accuracy (≥95%) | 155/176 (88.1%) | no |
| Schema grounding (100%) | 156/156 | yes |
| Unsupported-claim rate (≤2%) | 0/225 | yes |
| Tool execution success (≥95%) | 72/76 (94.7%) | no |
| Chart validity (≥95%) | 54/59 (91.5%) | no |
| Evidence completeness (100%) | 225/225 | yes |
| Clarification recall (≥90%) | 30/30 | yes |
| End-to-end success (≥85%) | 103/118 (87.3%) | yes |

One clarification was unnecessary. All five chart misses come from runs that failed or asked for
clarification, not from invalid charts. A run averaged 3.5 model calls, about 4,600 tokens, and
6.3 seconds; the suite used 474,232 prompt tokens and 66,517 output tokens.

**Failures (15 runs).**

- Numbers stored with a unit, such as `12.5 kg`: all 3 runs summed only the values without a unit
  and reported wrong weights per material.
- Dates written in several formats: asked for one month's revenue, all 3 runs filtered only ISO
  dates. Two published a Verified Insight of about 63% of the true total, and one failed on cast
  errors. The claim was accurate for its filter, which is why the filter must stay visible.
- Removing duplicate rows across every column: all 3 runs failed. The plan approved only the
  status field, the SQL policy forbids `SELECT DISTINCT *`, and the repair budget ran out.
- Incomplete answers from correct results: counting a normalized material, all 3 runs asserted
  one or two of the eight result rows; asked which batch had the fewest defects, one run reported
  the defect rate but not the batch.
- An abbreviated measure, `luot_thue tb` (average rentals), was mapped as unavailable, causing the
  one unnecessary clarification.
- A requested two-shift test was answered with per-shift averages from SQL instead.

**Manual review of every stored run.** All 129 stored runs, including the runs later retried after
provider errors, were read, and all 226 claims and 31 clarification questions were scanned. Every
passing run was numerically and semantically correct, and no unsupported claim was published. The
review found these presentation problems:

- 24 of 31 clarification questions used the generic unavailable-metric text, in English even for
  Vietnamese questions, without naming fields that could answer. They include 11 of 18 runs of
  ambiguous ranking questions, where the model's own mapping reason already named candidates.
- 33 claims embedded SQL predicates, such as `where NOT "thoi_gian_phan_hoi_phut" IS NULL`.
- 21 claims showed generated aliases, such as `Count 1 for island = Biscoe`.
- 17 claims showed floating-point noise, such as `3.1000000000000005`.
- Test names were lowercased inside sentences, such as `aNOVA F statistic` and
  `welch t statistic`.
- 10 answers backed only by a statistical test showed the technical notice "No chart candidate was
  published".
- 94 claims name groups by raw field names, such as `phuong_phap = A`.
- One claim stated that a total weight is positive, which is true but uninformative.
- 8 runs listed every row in a SQL step before a statistical test; this spends a Tool Action
  without changing the result.
- A KPI answered "which batch" questions with the value alone, without the batch.

### Changes made after the release suite

The presentation problems above were fixed without changing a prompt template or grading rule, so
the stored release runs keep their grades:

- A clarification for an unavailable metric names the dataset's numeric fields, leaving out
  possible PII and identifier-like fields.
- Claim text no longer contains SQL conditions. The dashboard shows them beneath each claim, and
  the Audit view and JSON export keep them in the Evidence Trail.
- Generated aliases read as their function (`count_1` as Count), claims and KPIs show at most 15
  significant digits, and test names such as ANOVA, Welch, and Pearson keep their capitals.
- An answer backed only by a statistical test shows a neutral note instead of a chart error.

Still open: the unavailable-metric question is in English, groups are named by raw field names,
and the model can still assert an uninformative direction or list rows before a test.

### Final fix round

Only one failure class above was seen on two or more datasets: a correct small result stated in
part (the release materials and batch questions, and the month comparison in holdout v8). Once the
model asserts a value from a complete grouped or single-row result of at most 20 values, the other
values are now reported too. Group labels, `row_number`, results the model did not answer from,
and results computed from possible PII are left out. A new small holdout measures this round.

A review for case-specific code found no dataset names or values in the source, but some prompt
examples echoed evaluation questions: `top performer` from holdout v8, `readings` and `tickets`
from holdouts v6 and v3, a profile question from the first holdout, and `hiệu quả nhất`, which also
appears in a release question written later. The rules are unchanged, but the examples are now
neutral (semantic-v15, plan-v9). The release suite's clarification result for that question may
have been helped by the overlap.

These failures were each seen on one dataset and are not fixed, because fixing them would tune the
agent to the release suite:

- numbers stored with a unit, such as `12.5 kg`, are summed without the unit-bearing values;
- dates written in several formats in one column are filtered by one format only;
- removing duplicate rows across every column fails, because the SQL policy forbids
  `SELECT DISTINCT *`;
- an abbreviated measure name, such as `tb` for average, can be treated as unavailable;
- a requested test can be answered with averages instead.

### Fixes from the full project review

A review of every module, the grader, the tests, the documentation, and the packaging followed the
release run. No prompt template or grading rule changed in this round:

- Streamlit explains a rejected upload, a failed profiling, pin, publication, or run instead of
  showing a traceback. A reproduction test covers the rejected upload.
- A candidate chart can be shown as another supported chart type over the same verified result,
  re-rendered from the checkpoint history without a model call. This completes the "create, refine,
  and pin" acceptance criterion.
- A CSV whose content begins like a known binary format, such as an XLSX renamed to `.csv`, is
  refused by name.
- A statistical parameter error, such as a group order the data does not contain, is now repaired by
  the model; data that cannot support the analysis still stops the run.
- Evaluation token totals include a retried provider error's tokens, and the Kruskal–Wallis
  statistic has a claim label, with a test that every statistic the engine reports has one.
- The explorer states when no rows match instead of showing zero, and viewing a stored result no
  longer rewrites the session file or reorders the session list.
- Dead code (the stale-insight model, an unused SQL helper) was removed, and identifier parsing, the
  `row_number` convention, and repeated literal thresholds now have one definition each.

### What the final holdout showed

The 10-case final holdout was committed before any run and measured once: 29 of 29 graded runs
passed, and every Section 17.4 gate was met. One run hit a provider rate limit and is reported
separately. The round it was written to measure worked on data the agent had never seen:

- All twelve runs of the four grouped questions stated every group, not a subset.
- Both ranking questions named the winning sport and the winning date, not only the value.
- All three runs of the ambiguous question asked which measure to rank by, and all three runs of the
  unavailable-metric question listed the dataset's numeric fields.

Read this score for what it is. Thirty runs is a small sample, clarification recall rests on six
runs, and the two datasets are clean by design, because the round being measured was about stating a
correct answer in full rather than about messy data. The release suite's 118 runs on nine datasets,
including deliberately dirty ones, remains the more demanding measurement, and its unmet gates stand
as recorded above.

One observation, not fixed: two runs of the completion-rate question also reported each genre's
median, minimum, maximum, and standard deviation, twenty claims where four were asked for. The
numbers were correct; the answer was simply longer than the question. Under the agreed stop rule no
further code change was made after this measurement.

## 13. What this code measures

Sections 10 and 12 describe earlier states of the code. This section describes what is in the
repository now, measured after the full-data round removed automatic sampling, gave statistics their
own full read path, added real execution deadlines, and recorded SQL provenance per output.

Both suites were re-run at that state with their expectations unchanged. The release suite passed
108 of 120 runs (90.0%; 95% Wilson interval about 83–94%) with no provider errors, and the final
holdout passed 30 of 30 with every gate met.

| Section 17.4 gate (threshold) | Release | Met | Final holdout | Met |
|---|---|---|---|---|
| Calculation accuracy (≥95%) | 161/177 (91.0%) | no | 66/66 | yes |
| Schema grounding (100%) | 157/157 | yes | 42/42 | yes |
| Unsupported-claim rate (≤2%) | 0/292 | yes | 0/115 | yes |
| Tool execution success (≥95%) | 74/75 (98.7%) | yes | 21/21 | yes |
| Chart validity (≥95%) | 56/60 (93.3%) | no | 18/18 | yes |
| Evidence completeness (100%) | 292/292 | yes | 115/115 | yes |
| Clarification recall (≥90%) | 30/30 | yes | 6/6 | yes |
| End-to-end success (≥85%) | 108/120 (90.0%) | yes | 30/30 | yes |

Two gates remain unmet on the release suite, both within four points of their threshold. Tool
execution success, which the earlier code missed, is now met. The two suites together used 578,570
prompt tokens and 83,453 output tokens; no cost is stated because no verified price was supplied to
the runner.

**The twelve remaining release failures.** Every one was read by hand, and each is confined to a
single dataset:

- Counting rows after removing exact duplicates returned 1 instead of 666, in all 3 runs: the claim
  asserted the result's row count rather than the value inside that row.
- A month-by-month revenue question over mixed date formats reported one grand total instead of a
  figure per month, in all 3 runs.
- A clearly worded ranking question stopped at `awaiting_semantic_review` in all 3 runs instead of
  answering, so the evaluation, which has nobody to confirm the proposal, recorded no answer.
- One run named the winning batch without its defect rate; one run reported a raw defect count
  instead of the rate, and for the wrong group; one run exhausted its SQL repair budget on a
  `50.0kg` conversion error.

Under the rule this project uses — fix only a failure class seen on two or more datasets — none
of these qualifies, so none was fixed. Fixing them individually would tune the code to these cases.

**A later round was measured and rejected.** After the measurement above, a semantic-correctness
round added a typed metric contract and an AST verifier for metric meaning. Offline it was green
(615 tests, 90.70% coverage, strict mypy). Measured live it scored 92 of 120 with four of eight
gates, and the two cases it was written to fix still failed all 3 runs. The main cause was
verification that was too strict rather than model error: the rate check required a bare aggregate
on each side of the division, so correct SQL such as `CAST(SUM(a) AS DOUBLE) / CAST(SUM(b) AS
DOUBLE)` was rejected although it executed and returned the right numbers. Because a rejection
consumed the repair budget, each one failed a whole run and took its chart with it, which is why
chart validity fell without the renderer changing. That round is preserved on its own branch and is
not part of this code. The lesson is recorded here: a rule that can only reject needs a corpus of
correct inputs, in the shapes models actually produce, that it must let through.

**What manual testing found afterwards.** A hand session on an 840-row Vietnamese dataset
written for the purpose, with deliberately inconsistent branch spellings, missing values, a
personal-data column, and three measures that rank the branches differently, confirmed the parts
this round was about: the profile reported both missing counts exactly, flagged the inconsistent
spellings, the identifier column, and the personal-data column; a grouped total returned one row
per branch with every figure correct; and an unranked "which is best" question asked which of the
three measures was meant instead of choosing one. Three findings came out of it:

- A statistical test grouped by a field with inconsistent spellings refused the analysis, reporting
  that a group had two usable values when it had one hundred and twenty-nine. Each spelling had
  become its own group. This is fixed, with regression tests; the fix is described in Section 5.
- A SQL average did not say how many rows it skipped for missing values, as described in Section 5.
  Recorded, not fixed.
- A grouped comparison printed each group's assumption caveat with the group name still in its
  stored form, so `Quận 1` read as `str:"Quận 1"`. Every non-ASCII group label was affected,
  in any language. This is fixed, with regression tests.
- A comparison of two named groups is refused when the field holds more than two values, because
  the two-group tests require the whole field to have exactly two. `group_order` already names the
  two groups to compare but is only used to order them, so "is A different from B" cannot be
  answered for a field with more categories. Recorded, not fixed: a wider question over every
  group, which runs ANOVA or Kruskal-Wallis, is answered correctly.
- After the clarification question above was answered with one measure, the analysis still computed
  all of them: fourteen claims where four were asked for. The numbers were right and the question
  was answered, but the answer was wider than the question. This is the same behaviour the final
  holdout recorded on a different dataset, so it is not specific to one set of data. It is recorded
  rather than fixed, because narrowing it means changing a prompt, and no unobserved set remains to
  measure the change on.

**The final holdout is no longer an unobserved set.** It was measured once as intended, then run
again by each later round, at least three times in total. Its 30/30 is reported here as a
confirmation and must not be used to decide a release. A future release decision needs a new set.
