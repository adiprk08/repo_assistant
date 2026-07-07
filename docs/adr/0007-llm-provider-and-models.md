# ADR-0007: Anthropic API, tiered models, prompt caching, native citations

**Status:** Accepted (2026-07-07)

## Context

The generation layer needs: strong code reasoning, reliable multi-step tool use (agent path), streaming, and — critically for this product — trustworthy citations. Costs must be controlled across three very different workloads: answering, routing/classification, and bulk enrichment.

## Decision

- **Provider:** Anthropic API via the official Python SDK, wrapped in the `LLMClient` interface in `core/` (pipelines never import the SDK directly). Model IDs, effort, and budgets are configuration.
- **Model tiers:**
  - **claude-opus-4-8** — answer generation and the agentic loop (both reasoning paths' final generation). Adaptive thinking (`{"type": "adaptive"}`); effort per route (`high` default; lower for fast-path lookup).
  - **claude-haiku-4-5** — intent router, query condensation, contextual chunk descriptions, file summaries (bulk enrichment where volume dominates).
  - **claude-sonnet-5** — configurable mid-tier for cost-sensitive deployments; not the default.
- **Streaming** for all user-facing generation (SSE end to end).
- **Prompt caching:** stable prefix ordering (frozen system prompt → repo map → conversation) with `cache_control` breakpoints; the repo map is kept byte-stable between index updates precisely so the cache holds. Enrichment passes cache the whole source file while generating per-chunk descriptions (this is what makes contextual retrieval affordable).
- **Native citations:** retrieved chunks are passed as document content blocks with citations enabled; the API returns char-anchored citations we map deterministically to `path:start-end@commit`, then verify against the index post-hoc. (Consequence: structured-output mode is incompatible with citations, so answer formatting is prompt-driven, not schema-driven.)
- **Batch API** for offline bulk enrichment and nightly eval judging (50 % cost).

## Alternatives considered

- **OpenAI / Gemini** — capable alternatives; neither offers the citations feature this design leans on, and Claude's tool-use behavior fits the budgeted agent loop. The `LLMClient` interface keeps this reversible.
- **Local models (Qwen-Coder, Llama)** — attractive for cost/privacy; current quality gap on multi-hop code reasoning is too large for the product's core promise. Revisit for the enrichment tier.

## Consequences

- Vendor coupling is confined to one module; models/pricing shifts are config changes.
- Cost control is architectural: routing (haiku gate), caching (prefix + per-file), batching (enrichment/evals), and tool budgets — each independently measurable in Langfuse telemetry.
