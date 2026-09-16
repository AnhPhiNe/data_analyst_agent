# Full-data implementation acceptance report

Status: **implementation gates passed; release quality gates incomplete**

This report tracks the v1.7 full-data analysis amendment approved on 2026-09-16. Implementation
evidence is recorded separately from the live Gemini measurements. Both live suites below ran on
the frozen v1.7 worktree; no automatic sampling was enabled.

## Baseline and environment

- Baseline branch: `m5.3-streamlit-workspace`
- Baseline commit at start: `cd669c3`
- Initial worktree: clean
- Python validation: available through the repository `.venv`
- Docker CLI: unavailable on this host (`docker --version` is not installed); Docker build and
  healthcheck remain unverified
- Live Gemini: completed on the frozen source, configuration, fixtures, and expectations. The
  release suite missed its calculation and chart quality thresholds; the final holdout passed.

## Locked-plan checklist

| Area | Required evidence | Status |
|---|---|---|
| Full-data scope | `rows_loaded == population_row_count`, `sampled=false`, `partial=false`, `truncated=false` on successful new results | Passed by independent acceptance regression (20/20) |
| Scope counts | `dataset_row_count`, `population_row_count`, `rows_loaded`, `sample_size`, and `missing_row_count` retain separate meanings and come from execution | Passed by independent 10,001-row and missing-value regressions |
| Presentation bound | `max_query_rows` can limit returned/displayed query rows without changing aggregate/statistical input | Passed: 10,001 rows analyzed while preview is capped at 10 |
| Tool Action audit | New `ToolAction.inputs` carries full-data metadata and no `method=reservoir`/`maximum_rows=10000` | Passed by independent acceptance regression |
| Evidence propagation | Result, catalog, Evidence Trail, verification, JSON/export preserve scope metadata | Passed by independent acceptance regression |
| Supported filter | Test exercises an existing SQL/analysis scope only; no new statistical filter API | Passed: existing `TabularDataCore.read_full` SQL filter scope |
| Missing data | Valid `sample_size` and excluded-row count remain distinct from population/input counts | Passed by independent missing-value regression |
| Timeout | Read/conversion/Python calculation deadline; worker is serializable and truly terminable; parent owns DuckDB/temp cleanup; Windows uses `spawn` | Passed by independent spawned-worker marker/PID cleanup regression |
| Legacy evidence | Sampled or incomplete old evidence is marked legacy/unverified and cannot feed current publish/export without rerun | Passed by independent publication/export regression |
| SQL provenance | Existing SQL provenance protections remain green, including fake `1688` rejection and valid literal labels | Passed: SQL provenance regression 19/19; app E2E fake `1688` has no publishable insight |
| Spec/docs | `Spec.md`, README, architecture, milestone, limitations and this report agree on the contract | Passed; final worktree hash audit is clean |
| Docker | Runtime contains `.streamlit/config.toml`; clean image build and healthcheck | Docker unavailable; source inspected only |
| Offline quality | pytest, Ruff, format check, mypy, coverage and focused 10,001-row/timeout regressions | Exact CI gates pass: pytest 516 passed/2 skipped; coverage 90.94% (90% required); focused acceptance 20/20; Ruff check/format, configured mypy (85 files), compileall and pip check pass. |
| Live release | Release 40 cases × 3, then final holdout 10 cases × 3, with frozen artifacts and no expectation edits | Completed; release 108/120, holdout 30/30. Release calculation and chart gates remain below threshold |

## Evidence log

The following evidence is complete so far:

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --no-cov tests/test_full_data_contract_acceptance.py
20 passed in 22.03s

.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --no-cov tests/test_sql_provenance_v1.py
19 passed

.venv\Scripts\python.exe -m compileall -q src tests
exit 0

.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
516 passed, 2 skipped in 183.34s; total coverage 90.94% (required 90%)

.venv\Scripts\python.exe -m ruff check .
All checks passed

.venv\Scripts\python.exe -m ruff format --check .
100 files already formatted

.venv\Scripts\python.exe -m mypy
Success: no issues found in 85 source files

.venv\Scripts\python.exe -m pip check
No broken requirements found.
```

An earlier broad run before the v1 integration was complete reported 455 passed, 2 skipped, and
17 failures. That set included an actual row-id runtime defect, which was fixed, as well as
historical fixtures that lacked v1 provenance metadata. The final offline suite is green and
reaches the coverage threshold.

The frozen runtime worktree was audited before and after both live suites: all source, test,
configuration, and evaluation-case hashes matched the freeze manifest, with no missing or changed
runtime files. The freeze manifest records aggregate hash
`4bc82d8ab8180d9b998fd02796f751a0530f29a88fbd13ce1f2f2bf101a22012` and is at
`.eval/full-data-freeze-20260916.json`. This report was updated after the live runs; the complete
post-evaluation audit, including that documentation update, is at
`.eval/full-data-final-audit-20260916.json`.

The initial live release attempt under default network restrictions stopped at provider
`ConnectError` with zero provider calls; its output was preserved. The network-enabled retry completed all 120 records with zero
provider errors. Its summary is at
`.eval/release-full-data-20260916-4bc82d8a-retry/summary.json`. It passed 108/120. The gates were:

```text
calculation_accuracy 161/177 = 0.9096 (required >= 0.95)  FAIL
schema_grounding     157/157 = 1.0000                         PASS
unsupported_claim_rate 0/292 = 0.0000 (required <= 0.02)      PASS
tool_execution_success 74/75 = 0.9867                        PASS
chart_validity        56/60 = 0.9333 (required >= 0.95)       FAIL
evidence_completeness 292/292 = 1.0000                         PASS
clarification_recall  30/30 = 1.0000                          PASS
end_to_end_success    108/120 = 0.9000 (required >= 0.85)     PASS
```

The release failures were model query/semantic robustness cases rather than false positives from
the new provenance guard: deduplication returned 1 instead of 666, mixed date parsing missed
formats, one defect-rate answer omitted a value, one coating answer summed raw values instead of a
rate, three bike cases clarified or missed the requested average, and one recycling run exhausted
SQL repair after a `50.0kg` conversion error. No release result showed a provider, timeout,
sampling, or full-data execution failure.

The final holdout summary is at `.eval/final-full-data-20260916-4bc82d8a/summary.json`. It completed
30/30 records with zero provider errors and zero retries. Every gate passed: calculation 66/66,
schema 42/42, unsupported claims 0/115, tool execution 21/21, chart 18/18, evidence 115/115,
clarification 6/6, and end-to-end 30/30. Release plus holdout used 578,570 prompt tokens and
83,453 output tokens; estimated cost is unavailable because no verified current price was supplied
to the runner.

Docker remains unverified because the CLI is unavailable. Browser validation used a separate
fixture session on `localhost:8502`: upload and local ingest/profile completed visibly with 4 rows,
2 columns, and 0 missing cells. Its first model request showed `ConnectError` under the default
network restriction. A separate network-enabled `localhost:8503` server launched, but the browser
file chooser then remained pending tool approval; the upload-to-export flow was therefore not
completed or claimed. That temporary server was stopped; the existing train session was not
modified. The two Windows symlink-related skips remain recorded by the test suite.
