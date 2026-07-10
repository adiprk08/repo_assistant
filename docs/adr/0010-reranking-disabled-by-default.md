# ADR-0010: Reranking disabled by default (measured)

**Status:** Accepted (2026-07-10) — supersedes the cross-encoder-reranking clause of [ADR-0004](0004-vector-store-and-hybrid-retrieval.md)

## Context

ADR-0004 specified a cross-encoder reranker (Voyage rerank-2.5) over the fused candidate set, on the standard assumption that reranking improves precision. Phase 2 implemented it behind the `Reranker` interface and measured it against the dense+symbol RRF baseline on the 26-question span-labeled benchmark (docs/EVALUATION.md §5).

The result contradicted the assumption: reranking **lowered** ranking quality.

| Metric (overall) | dense+symbol (RRF) | + rerank-2.5 |
|---|---|---|
| MRR | **0.82** | 0.67 |
| nDCG@10 | **0.84** | 0.71 |

Per-repo MRR fell across the board (yocto-queue 1.00→0.81, click 0.69→0.56).

## Decision

- **Reranking is disabled by default.** The RRF-fused dense+symbol order is used directly for chat and the default eval.
- The `Reranker` interface, the `VoyageReranker` adapter, and the `hybrid_retrieve(use_rerank=...)` path are **kept** — reranking is opt-in (`ra eval --rerank`) so the decision stays continuously re-measurable.
- **Context assembly** (overlap dedup + per-file cap) introduced alongside reranking is **kept**: it improves citation quality independent of ranking.

## Why the reranker hurt

A general-purpose cross-encoder scores natural-language relevance. For identifier-bearing code questions ("how does `enqueue` work?"), it ranks chunks that *verbally describe* the behavior above the actual definition, demoting the exact symbol match that the symbol channel + RRF correctly placed first. The exact-match signal is stronger than semantic similarity for this workload.

## Alternatives considered

- **Keep reranking on** — rejected: it regresses the primary metric on all benchmark repos.
- **Remove reranking entirely** — rejected: the mechanism is sound and may help with (a) a code-specialized reranker, or (b) larger/noisier candidate pools than this benchmark exercises. Keeping it opt-in preserves the option at no default cost.

## Consequences

- Default retrieval is dense + symbol + RRF (MRR 0.82 / nDCG 0.84).
- Revisit if a code-trained reranker becomes available or the benchmark grows to scales where RRF alone under-ranks; the ablation flag makes re-evaluation a one-line command.
- This is a worked example of the project's "evaluation before optimization" rule overturning a design assumption.
