# Tabular Analytics Agent

A local-first, single-agent workspace for analyzing CSV and XLSX datasets through
reproducible tools, deterministic verification, and user-curated dashboards.

The project has implemented **Milestone 5.2 — Dashboard artifact lifecycle**; **M5.3 — Streamlit
workspace** is implemented and undergoing live integration validation. Product and technical decisions
are captured in [Spec.md](./Spec.md), while shared domain language lives in
[CONTEXT.md](./CONTEXT.md).

## Design principles

- The LLM plans and interprets; deterministic tools calculate.
- A conclusion is not a Verified Insight until its evidence passes Verification Gates.
- The Source Dataset is immutable; transformations create versioned Working Datasets.
- Streamlit, LangGraph, and model providers sit behind replaceable seams.
- Core behavior is testable without a UI or live model API.

## Development setup

Python 3.12 is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the quality checks:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest
```

Run the live evaluation suite, which uses the Gemini API quota and paces requests:

```powershell
python -m tabular_analytics_agent.evaluation.runner --runs 3
```

Per-run results and a summary are written to `.eval/<timestamp>/`. Cases live in
`tests/evaluation_cases/` and use synthetic fixtures from `tests/fixtures/`.

Do not add API keys to the repository. Use `.env.example` as a local configuration template and
provide `GOOGLE_API_KEY` through the process environment or, later, Streamlit Secrets for live
Gemini calls. Automated tests use `FakeModelGateway` and do not call an external model.

Start the local MVP after creating `.env` from `.env.example`:

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

The browser flow supports safe CSV/XLSX upload, profiling, conversational goal capture, semantic and
plan approval, verified results, candidate charts, explicit dashboard pinning, and an audit view.

## Current layout

```text
src/tabular_analytics_agent/domain/      Domain contracts and invariants
src/tabular_analytics_agent/application/ UI-neutral session workflow service
src/tabular_analytics_agent/data/        Secure ingestion, profiling, and read-only querying
src/tabular_analytics_agent/evaluation/  Golden evaluation case contracts
src/tabular_analytics_agent/model_gateway/ Provider-neutral structured LLM boundary
src/tabular_analytics_agent/orchestration/ LangGraph workflow and SQLite checkpoints
src/tabular_analytics_agent/statistics/    Deterministic statistics and bounded Tool Actions
src/tabular_analytics_agent/verification/ Deterministic evidence gates
src/tabular_analytics_agent/visualization/ Validated Plotly specs and artifact lifecycle
streamlit_app.py                          Streamlit delivery adapter
tests/                                   Tests through public module interfaces
tests/fixtures/                          Small deterministic datasets
docs/                                    Architecture and engineering decisions
```
