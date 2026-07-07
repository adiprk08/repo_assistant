# ADR-0003: Embeddings — voyage-code-3 primary, pluggable interface

**Status:** Accepted (2026-07-07)

## Context

Dense retrieval quality is bounded by embedding quality, and code is not prose: identifiers, structure, and mixed code/comment content favor code-trained models. Embedding is also the dominant *indexing* cost, so caching and swap-ability matter.

## Decision

- **`Embedder` interface** in `core/` (batch embed, dims, model id); all pipeline code depends on the interface.
- **Default: Voyage AI `voyage-code-3`** — code-specialized training, strong published results on code-retrieval benchmarks (CoIR and repo-level evals), 32k-token input context (comfortably fits our chunks + headers), Matryoshka dimensions and quantized output options to trade recall vs. storage; Anthropic's recommended embeddings partner.
- **Local fallback: BGE-M3** (via sentence-transformers) for offline dev, CI, and provider-outage degradation — also gives the eval harness a second point for comparison.
- **Cache:** embeddings keyed `(model, dims, sha256(chunk_text))` in Postgres — re-indexing unchanged content is free; switching models triggers a clean, observable full re-embed.

## Alternatives considered

- **OpenAI text-embedding-3-large** — strong general-purpose model, but not code-specialized; benchmark results on code retrieval trail code-trained models.
- **Open-source only (BGE-M3 / nomic-embed-code / CodeRankEmbed)** — no per-token cost and private, but measurably weaker at the top end and adds GPU/serving infrastructure; kept as the fallback rather than the default.
- **Cohere embed-v4 / Jina code embeddings** — credible; no decisive advantage over voyage-code-3 for code, smaller ecosystem pull.

## Consequences

- Provider swap is a config change plus a re-embed run — no orchestration changes.
- The eval harness (Phase 2) compares embedders empirically; this ADR is superseded if a challenger wins on recall/cost.
- Dimension choice (default 1024) balances Qdrant memory vs. recall; revisit with quantization at the Large tier.
