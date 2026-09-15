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

The gap between v7 and v8 is the most useful reading of these numbers: the agent is reliable on
clear questions over clean data and much weaker when values are inconsistent or the question is
vague. Each set has 12 cases run 3 times. That sample is small: a single case failing all runs moves the
score by 8.3 points, and a perfect score is consistent with a lower long-run rate. Later sets were
written by the same author who fixed the earlier failures.

## 3. Data sent to the model provider

Details are in Spec Section 14.5. In short, the model receives field names and kinds, row counts,
up to 10 frequent values of each non-PII field, tool error messages, and verified result values of
at most 50 rows.

- `TABULAR_AGENT_SEND_SAMPLE_VALUES=false` withholds the frequent values but not field metadata or
  result values, and filters on text values then fail more often.
- Free API tiers may allow the provider to use submitted content. Real customer data needs a paid
  tier or an enterprise offering, checked against the provider's terms.
- Keeping all data on the machine requires a local model adapter, which is not implemented.

## 4. Privacy detection is heuristic

- Possible PII is detected from English column names (for example `email`, `phone`, `full_name`)
  and from values that look like email addresses or phone numbers.
- Column names in other languages, such as `Tên khách hàng` or `Địa chỉ`, and personal names or
  addresses in values are not detected. Such fields are sent to the model like any other field.
- An identifier-like warning requires at least 20 non-missing rows, so an id column in a smaller
  table is treated as an ordinary field.

## 5. Data preparation is limited

- Only one rectangular table per session: CSV, or one selected XLSX sheet, up to 100 MB by default.
  There are no joins across files.
- Category values are not normalized. `Hà Nội`, `Hà Nội ` (trailing space), and `hà nội` stay
  different values. The profile flags such fields and the SQL guidance asks for
  `LOWER(TRIM(...))` comparisons, but whether the generated query follows it depends on the model.
  Spellings that differ in other ways, such as `Ha Noi` or `HN`, are not detected.
- Numbers stored as text, such as `1.250.000` or `$4.99`, stay text in the profile. The agent must
  convert them in SQL, which it may not do.
- Dates in ISO form (`2026-03-02`) are detected in any language. Other date formats are converted
  only when the column name is an English date word and every value parses; day-first and
  month-first forms such as `03/04/2026` can be read the wrong way.
- Working Dataset transformations (cleaning, recoding) are a Should-tier feature and are not
  implemented.

## 6. Resource and quota limits

- Queries return at most 10,000 rows; statistical tests use a deterministic reservoir sample of at
  most that many rows. Limits are configurable (Spec Section 25.3).
- A question uses about 4 to 5 model calls. The free tier allows 15 requests per minute per API key;
  several keys rotate, but keys created in the same Google project may share one quota. Using
  several keys must comply with the provider's terms.

## 7. Charts and the Data Overview

- The agent proposes KPI, table, histogram, bar, line, or scatter charts, and the chosen type can
  vary. Answers backed only by a statistical test have no chart. When a proposed chart fails
  validation, the verified result is shown as a table instead, with the reason recorded in the
  chart's constraints; a result too large for a table (over 500 rows) gets no chart.
- A KPI shows every significant digit of the stored value. Whole numbers above 2^53 (about 9
  quadrillion) cannot be drawn exactly, so they get no KPI chart. DuckDB `DECIMAL` results are
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

These parts of the specification are recorded as known gaps instead of being removed from it:

- Person names, addresses, and non-English column names such as `Tên khách hàng` are not
  detected as possible PII (see Section 4; scheduled right after the release suite).
- A dataset with no numeric field, no grouping field, and no missing values or duplicate rows
  gets two goal suggestions instead of three to five (scheduled right after the release
  suite).
- There is no run-level trace ID, no complete record of graph node transitions, no estimated
  cost, and no application command to rerun a saved Tool Action; the saved SQL or statistical
  parameters can be rerun manually (deferred after the MVP).
