# Roadmap

> Status: **Phase 5 in progress** (updated 2026-07-12) — Phases 0–4 complete; incremental indexing + webhooks landed. Estimates assume focused part-time development with AI assistance; phases are sequential but each ships something usable.

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

## Phase 3 — Deep code understanding (~3 weeks) — ✅ COMPLETE (2026-07-11)

**Goal:** answer questions no single chunk can answer.

> Sequencing note: structural work (code graph, language tiers, trace/architecture eval sets) is done first because it is cost-free and retrieval-measurable; the LLM-heavy pieces (intent router, agentic loop, hierarchical enrichment) follow, batched to manage Anthropic spend.

- ✅ Code graph: imports/contains/inherits edges (high confidence) + heuristic call/reference edges with confidence scores; traversal API; graph retrieval channel — **channel measured net-negative on its own trace/architecture test and stays opt-in ([ADR-0011](adr/0011-graph-channel-disabled-by-default.md)); the graph's default-path role is agent-tool traversal (task: agent loop)**
- ✅ Eval categories extended: `architecture` and `trace` question sets — 36-question golden set (9 trace / 6 architecture), multi-span cross-file evidence, per-category metrics in harness + reports (docs/EVALUATION.md §5)
- ✅ Language Tier 2: Go, Java, Rust grammars + symbol queries — extension detection, `.scm` query files, and language-aware qualified names (Go method receivers, Rust `impl`/`trait` owners, nested Java members); graph edge extraction is language-agnostic so contains/calls edges flow automatically
- ✅ Intent router (claude-haiku-4-5) + two-tier reasoning: fast path vs. budgeted agentic loop with index tools — **built and measured at parity with single-pass; kept opt-in ([ADR-0012](adr/0012-agentic-loop-opt-in.md)). Router path accuracy 0.74–0.80; agent answers correct+grounded (5.0/4.71, pass 1.0) but no quality gain over single-pass on this benchmark, and budget adherence (0.24–0.63) misses the >0.95 target — follow-ups: harder multi-hop benchmark, budget/routing fixes**
- ✅ Hierarchical enrichment — Stage A (contextual chunk descriptions) built and measured: **no retrieval lift on the benchmark, kept opt-in ([ADR-0013](adr/0013-contextual-descriptions-opt-in.md))**; the summary hierarchy + repo map (Stage B, generation-context) is deferred since the retrieval lever didn't move.

**Exit-criteria outcome (honest):** trace/architecture retrieval targets (nDCG@10 ≥ 0.85, recall@5 ≥ 0.90) are **partially met** on the main set — architecture nDCG 0.86 (met), trace nDCG 0.75 (gap). The deeper finding: on that set the quality levers (graph ADR-0011, agent ADR-0012, descriptions ADR-0013) all measured net-neutral **because the symbol-named questions are already saturated** by hybrid+symbol retrieval. Building the NL-heavy **challenge set** (`evals/challenge/`, docs/EVALUATION.md §5) confirmed this and gave the levers a fair test: single-pass collapses there (nDCG 0.30), and the **agent path beats it (nDCG 0.37, recall@10 +10 pts)** — the first measured win, vindicating both the agent investment and the evaluation discipline. Remaining, carried forward: close the absolute gap on the challenge set, lift agent budget adherence (0.67 < 0.95), and broaden the challenge set to more repos. Phase 3 ships all its capabilities, each rigorously measured.

## Phase 4 — Product surface (~2 weeks) — ✅ COMPLETE (2026-07-12)

**Goal:** a usable product, not a CLI demo.

- ✅ FastAPI service: repos CRUD, ingestion job status (SSE progress), chat completions (SSE streaming), search endpoint — shipped ([ADR-0014](adr/0014-api-service-and-streaming.md))
- ✅ arq workers + staged ingestion jobs (job-row state machine, SSE-observable) — shipped; checkpointed auto-resume + concurrent-multi-repo validation carried to Phase 5 hardening
- ✅ Conversation memory: snapshot-pinned sessions, incremental rolling summaries, follow-up condensation — shipped ([ADR-0015](adr/0015-conversation-memory.md)); a multi-turn eval set to measure condensation is owed
- ✅ API-key auth + rate limiting — shipped ([ADR-0016](adr/0016-api-auth-and-rate-limiting.md)): hashed bearer keys (`ra apikey`), per-key Redis fixed-window limits
- ✅ Minimal Next.js chat UI (`web/`, [ADR-0017](adr/0017-web-ui.md)): API-key gate, repo picker + register, SSE indexing progress, streaming chat with citations deep-linked to the pinned commit on GitHub

**Exit criteria:** full flow (register → watch progress → chat with citations) verified in the browser against a live indexed repo (grounded answer + 3 verified citations). Concurrent multi-repo ingestion + a formal responsiveness test carry into Phase 5 hardening alongside checkpointed auto-resume.

**Deferred to Phase 5 (noted in the ADRs):** a multi-turn eval set to measure follow-up condensation ([ADR-0015](adr/0015-conversation-memory.md)); per-session chunk-ID re-expansion; a first-party in-app file viewer (content endpoint) beyond GitHub deep-links; per-key scopes/quotas + auth-failure metrics.

## Phase 5 — Production hardening (~2–3 weeks)

**Goal:** run it for real, keep it fresh, keep it safe. **In progress.**

- 🟡 Incremental indexing — **core shipped** ([ADR-0018](adr/0018-incremental-indexing.md)): content-hash diff, copy-forward unchanged (rows + Qdrant points), atomic new snapshot; `ra update` + enqueued `update` job + **HMAC-verified GitHub push webhook** (`POST /webhooks/github`). Remaining: summary staleness budgets, polling trigger, snapshot GC, 50k-file scale validation
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
