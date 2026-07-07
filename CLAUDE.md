# Repo Assistant — project guide

RAG-powered GitHub Repository Assistant. Production-quality portfolio project; Claude acts as the architect and makes decisions independently.

## Ground rules

- **Docs are the source of truth and must stay current.** Any architectural or implementation decision change requires updating `docs/` in the same change set (ARCHITECTURE.md, ROADMAP.md, and the relevant ADR — supersede ADRs rather than editing history).
- Address the user as **Adi**.
- Prefer robust architecture over minimum-viable when it meaningfully improves long-term quality; justify trade-offs in ADRs.

## Orientation

- `docs/ARCHITECTURE.md` — system design, pipelines, data model
- `docs/ROADMAP.md` — current phase and exit criteria (check this before starting work)
- `docs/adr/` — decision records with rationale
- `docs/EVALUATION.md`, `docs/RISKS.md`

## Conventions (from Phase 0 onward)

- Python 3.12, `uv` for env/deps, `ruff` (lint+format), `pyright` (types), `pytest`
- Core logic lives in `src/repo_assistant/` as an importable library; API (`api/`), workers (`workers/`), and CLI (`cli/`) are thin shells over it
- Async-first (FastAPI, SQLAlchemy 2 async, httpx, arq)
- All external providers (LLM, embeddings, reranker, vector store) sit behind interfaces in `core/` — never call vendor SDKs directly from pipeline code
- Secrets via environment / `.env` (never committed); repo content is treated as untrusted input everywhere
