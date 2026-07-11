# Roadmap

> Status: **Phase 3 in progress** (updated 2026-07-11) — Phases 0–2 complete. Estimates assume focused part-time development with AI assistance; phases are sequential but each ships something usable.

## Guiding principles

1. **Vertical slice first** — a thin end-to-end path (ingest → index → answer with citations) before any component is made sophisticated. Everything after Phase 1 is measured improvement over a working baseline.
2. **Evaluation before optimization** — the eval harness lands in Phase 2, before retrieval tuning, so every subsequent change is justified by numbers ([EVALUATION.md](EVALUATION.md)).
3. **Interfaces before implementations** — provider abstractions from day one so later swaps are cheap.

---

## Phase 0 — Foundation (~1 week)

**Goal:** a professional skeleton where every later phase has a home.

- Project scaffolding: `uv` project, `src/repo_assistant/` package layout, typer CLI stub
- Tooling: ruff (lint + format), pyright, pytest + coverage, pre-commit
- CI: GitHub Actions — lint, typecheck, tests on push/PR
- `core/`: pydantic-settings config, structlog setup, error taxonomy, provider interfaces (`LLMClient`, `Embedder`, `Reranker`, `VectorIndex`) with fake implementations for tests
- `infra/docker-compose.yml`: Postgres, Qdrant, Redis
- Alembic wired with the initial schema (repos, snapshots, files, jobs)

**Exit criteria:** CI green; `ra --help` runs; `docker compose up` brings up storage; a fake-provider round-trip test passes.

## Phase 1 — Vertical slice MVP (~2 weeks) — ✅ COMPLETE (2026-07-10)

**Goal:** ask a real question about a real public repo and get a cited answer.

- Ingestion: clone public GitHub repo, scan/filter, language detection
- Parsing + chunking: tree-sitter for **Python and TypeScript/JavaScript**; AST-aware chunker; markdown chunker; fallback chunker
- Indexing: voyage-code-3 embeddings with content-hash cache; Qdrant dense index; Postgres files/symbols/chunks
- Retrieval: dense-only top-k with repo filter
- Reasoning: single-pass grounded generation (claude-opus-4-8) with native citations + post-hoc citation verification
- CLI: `ra index <github-url>`, `ra chat <repo>` (non-streaming; token-streaming over SSE lands with the API service in Phase 4)
- Starter eval set: ~30 hand-written Q&A pairs over 2 benchmark repos (smoke baseline)

**Exit criteria:** a mid-size repo (~2k files) indexes end-to-end in < 15 min; answers carry verified citations; starter-set answer accuracy recorded as the baseline.

## Phase 2 — Retrieval quality + evaluation harness (~2 weeks) — ✅ COMPLETE (2026-07-10)

**Goal:** measurably better retrieval, and the machinery to prove it. **Met:** MRR 0.65→0.86, nDCG 0.67→0.87 (+21/+20 over the dense baseline) via dense+sparse(BM25)+symbol RRF fusion; reranking evaluated and rejected ([ADR-0010](adr/0010-reranking-disabled-by-default.md)); span-level metrics + DB persistence + CI gate in place. Query understanding: identifier extraction shipped (powers the symbol channel); **follow-up condensation deferred to Phase 4** (needs conversation memory); metadata-filter inference dropped as low-value (recall already saturated).

- Eval harness (extends the Phase 1 starter — `ra eval`, LLM judge, citation + negative-handling metrics already exist): add span-level retrieval metrics (recall@k, MRR, nDCG) against labeled evidence, DB-persisted `eval_runs`/`eval_results`, and a CI smoke gate
- Hybrid retrieval: BM25 sparse vectors in Qdrant, server-side hybrid + RRF
- Symbol channel: exact + trigram-fuzzy identifier lookup
- Cross-encoder reranking (voyage rerank-2.5 behind `Reranker`)
- Query understanding: follow-up condensation, identifier extraction, metadata filters
- Chunking tuned against the harness (budget size, breadcrumb format, contextual descriptions on/off)

**Exit criteria:** retrieval recall@10 improves ≥ 15 points over the Phase 1 dense-only baseline on the golden set; eval runs in CI on every PR touching retrieval.

## Phase 3 — Deep code understanding (~3 weeks) — IN PROGRESS (2026-07-10)

**Goal:** answer questions no single chunk can answer.

> Sequencing note: structural work (code graph, language tiers, trace/architecture eval sets) is done first because it is cost-free and retrieval-measurable; the LLM-heavy pieces (intent router, agentic loop, hierarchical enrichment) follow, batched to manage Anthropic spend.

- ✅ Code graph: imports/contains/inherits edges (high confidence) + heuristic call/reference edges with confidence scores; traversal API; graph retrieval channel — **channel measured net-negative on its own trace/architecture test and stays opt-in ([ADR-0011](adr/0011-graph-channel-disabled-by-default.md)); the graph's default-path role is agent-tool traversal (task: agent loop)**
- ✅ Eval categories extended: `architecture` and `trace` question sets — 36-question golden set (9 trace / 6 architecture), multi-span cross-file evidence, per-category metrics in harness + reports (docs/EVALUATION.md §5)
- ✅ Language Tier 2: Go, Java, Rust grammars + symbol queries — extension detection, `.scm` query files, and language-aware qualified names (Go method receivers, Rust `impl`/`trait` owners, nested Java members); graph edge extraction is language-agnostic so contains/calls edges flow automatically
- Hierarchical enrichment: file/dir/repo summaries, repo map, contextual chunk descriptions (tiered by repo size)
- Intent router (claude-haiku-4-5) + two-tier reasoning: fast path vs. budgeted agentic loop with index tools

**Exit criteria:** trace/architecture retrieval reaches **nDCG@10 ≥ 0.85 and recall@5 ≥ 0.90 per category** (baselines from the 2026-07-10 harness run: trace 0.76/0.81, architecture 0.85/0.89 — the agent loop and enrichment are the levers); agent path stays within tool budget on > 95 % of eval queries.

## Phase 4 — Product surface (~2 weeks)

**Goal:** a usable product, not a CLI demo.

- FastAPI service: repos CRUD, ingestion job status (SSE progress), chat completions (SSE streaming), search endpoint
- arq workers + staged, checkpointed ingestion jobs; concurrent multi-repo ingestion
- Conversation memory: sessions, rolling summaries, snapshot binding
- API-key auth + rate limiting
- Minimal Next.js chat UI: repo picker, indexing progress, streaming chat with clickable citations (file viewer)

**Exit criteria:** two repos ingest concurrently while chat stays responsive; full flow (register → watch progress → chat with citations) works in the browser.

## Phase 5 — Production hardening (~2–3 weeks)

**Goal:** run it for real, keep it fresh, keep it safe.

- Incremental indexing: diff-driven updates, summary staleness budgets, manual + polling triggers; GitHub App webhooks
- Private repositories: GitHub App installation flow, encrypted tokens
- Observability: OTel traces, Langfuse LLM telemetry, Prometheus metrics + dashboards
- Security pass: prompt-injection red-team of the agent loop, secret-scanning verification, dependency audit
- Scale validation: index a 50k-file repo within time/cost budget; incremental update touches only changed files (verified)
- Deployment: production compose / single-VM guide; container images in CI

**Exit criteria:** webhook-driven re-index lands in minutes touching only the diff; 50k-file repo indexed within documented budget; dashboards show cost/latency/quality telemetry.

## Phase 6 — Extensions (ongoing)

Candidates, prioritized by demonstrated demand: multi-repo workspaces (cross-repo Q&A) · PR review mode (diff-aware retrieval) · documentation generation mode · commit-history/blame retrieval channel · Language Tier 3 (C/C++, C#, Ruby, PHP) · MCP server exposing retrieval tools to IDE agents · SCIP-based precise symbol resolution replacing heuristics.

---

## Implementation priorities (cross-phase)

1. **Correctness of citations** beats answer eloquence — the verifier and its metrics are never deprioritized.
2. **Eval coverage before features** — a feature without an eval category is not done.
3. **Cost ceilings** — enrichment tiers and model routing keep per-repo indexing and per-query costs inside budgets defined in Phase 2; budgets are config, not code.
4. **Docs current** — every phase closes with an ARCHITECTURE/ADR sweep.
