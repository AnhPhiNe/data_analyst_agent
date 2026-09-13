# Tabular Analytics Agent

A local-first, single-agent workspace for analyzing CSV and XLSX datasets through
reproducible tools, deterministic verification, and user-curated dashboards.

The project has completed **Milestone 2 — Deterministic data core**. Product and technical decisions are
captured in [Spec.md](./Spec.md), while shared domain language lives in
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

Do not add API keys to the repository. Copy `.env.example` to `.env` and supply local values
only when model integration is introduced in Milestone 3.

## Current layout

```text
src/tabular_analytics_agent/domain/      Domain contracts and invariants
src/tabular_analytics_agent/data/        Secure ingestion, profiling, and read-only querying
src/tabular_analytics_agent/evaluation/  Golden evaluation case contracts
src/tabular_analytics_agent/verification/ Deterministic evidence gates
tests/                                   Tests through public module interfaces
tests/fixtures/                          Small deterministic datasets
docs/                                    Architecture and engineering decisions
```
