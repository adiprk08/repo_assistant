# ADR-0001: Python 3.12 + FastAPI, library-first core

**Status:** Accepted (2026-07-07)

## Context

The system is I/O-bound (git, embedding APIs, LLM APIs, DB) with heavy dependence on the ML/RAG ecosystem: tree-sitter bindings, embedding/rerank clients, eval tooling, vector-store clients. We need one language for pipelines, service, workers, and evals, plus a credible path to a web frontend.

## Decision

- **Python 3.12** across pipelines, API, workers, CLI, and evals.
- **Tooling:** `uv` (env + deps + lock), `ruff` (lint + format), `pyright` (strict-ish typing), `pytest`, pre-commit; GitHub Actions CI.
- **Frameworks:** FastAPI + pydantic v2 (API/schemas), SQLAlchemy 2 async + Alembic (Postgres), httpx (HTTP), typer (CLI).
- **Library-first:** all logic in the importable `repo_assistant` package; API/worker/CLI are thin shells. Provider interfaces (`LLMClient`, `Embedder`, `Reranker`, `VectorIndex`) live in `core/` with fake implementations so every pipeline is unit-testable without infrastructure.
- Frontend (Phase 4) is TypeScript/Next.js — the one place a second language earns its keep.

## Alternatives considered

- **TypeScript/Node end-to-end** — excellent web DX; but the parsing/eval/RAG ecosystem (tree-sitter language coverage, rerankers, eval libraries) is markedly thinner, and data-pipeline ergonomics are worse.
- **Go** — great service performance; unacceptable friction for ML tooling and rapid pipeline iteration.
- **Python + Django** — batteries not needed; FastAPI's async model and pydantic integration fit SSE streaming and typed pipelines better.

## Consequences

- One test harness and one type system across the whole system; GIL is irrelevant (workload is I/O-bound, embedding/LLM compute is remote).
- Async discipline required everywhere (a sync SDK call in a request path blocks the loop) — enforced by review and a ruff rule where possible.
- Two-language cost accepted narrowly for the frontend.
