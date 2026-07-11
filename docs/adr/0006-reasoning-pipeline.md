# ADR-0006: Two-tier reasoning — routed fast RAG and a budgeted agentic loop

**Status:** Accepted (2026-07-07); implemented Phase 3 (2026-07-11) — router, five index tools, budgeted loop, prompt caching. Measured at **parity** with single-pass on the benchmark, so the agent path is **opt-in**, not the default ([ADR-0012](0012-agentic-loop-opt-in.md)).

## Context

Single-shot RAG (retrieve once → generate) answers lookup/explain questions well but structurally cannot answer multi-hop questions ("trace this request", "how do these modules interact?") — the evidence set isn't knowable from the query alone. Conversely, running a full agentic loop on every query multiplies cost and latency for questions one retrieval pass answers fine.

## Decision

- **Intent router:** claude-haiku-4-5 classifies each (condensed) query — `lookup | explain | architecture | trace | debug | other` — and flags multi-hop likelihood. Router decisions are logged and evaluated (a labeled intent set in the eval harness).
- **Fast path** (single-hop intents): hybrid retrieve → rerank → assemble → one grounded generation. Optimized for latency.
- **Agent path** (multi-hop intents): claude-opus-4-8 in a tool-use loop with **read-only tools over the index** — `search_code`, `read_span`, `get_symbol`, `graph_neighbors`, `list_dir`. Hard budget of **≤ 8 tool calls**, then a forced final answer. Adaptive thinking enabled; effort tuned per route.
- **Snapshot consistency:** tools read indexed data at the session's pinned commit — never the live filesystem — so answers are reproducible and injection surface is minimized.
- Both paths end in the same grounded-generation + citation-verification stage (ADR-0007).

## Alternatives considered

- **Always single-shot** — cheap and fast; demonstrably shallow on trace/architecture categories (the exact capabilities that differentiate this project).
- **Always agentic** — 2–10× cost/latency on the majority of queries that don't need it.
- **Graph-RAG only (pre-expanded neighborhoods, no loop)** — helps one hop, still can't follow evidence adaptively; retained as a *channel*, not the strategy.

## Consequences

- Cost and latency scale with question difficulty; the router is a new failure mode, so its accuracy is a first-class eval metric with a safe default (when uncertain → agent path, correctness over cost).
- The tool budget bounds worst-case spend and forces the model to commit; budget adherence is tracked (ROADMAP Phase 3 exit criterion).
- Tool registry is data-driven — future tools (e.g. `git_blame`) are additive.
