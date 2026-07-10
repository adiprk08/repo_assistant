# ADR-0011: Graph retrieval channel disabled by default (measured)

**Status:** Accepted (2026-07-10) — resolves the "pending trace-question validation" clause of [ADR-0005](0005-code-graph.md)

## Context

ADR-0005 built the code graph (`symbols`/`edges` in Postgres, heuristic call
edges with confidence scores) and a graph retrieval channel: resolve query
identifiers to symbols, expand 1 hop to callers/callees, feed the neighbor
chunks into RRF fusion. On the original explain/lookup benchmark the channel was
neutral, so it shipped opt-in pending a fair test on the question shape it was
designed for.

Task 24 built that test: the golden set grew 26 → 36 questions with 10 new
`trace`/`architecture` questions whose evidence is multi-span, mostly cross-file
caller-callee (docs/EVALUATION.md §5), and the harness gained per-category
metrics. The retrieval-only A/B on the categories the channel targets:

| Metric | no graph | +graph |
|---|---|---|
| trace nDCG@10 | **0.76** | 0.66 |
| trace recall@5 / @10 | 0.81 / 0.85 | 0.81 / 0.85 |
| architecture nDCG@10 | **0.85** | 0.80 |
| architecture MRR | **1.00** | 0.92 |
| architecture recall@10 | 0.89 | **1.00** |

## Decision

- **The graph channel stays disabled by default** for chat and eval; opt-in via
  `ra eval --graph` / `hybrid_retrieve(use_graph=True)` so the decision stays
  continuously re-measurable.
- **The graph itself is kept and remains load-bearing.** Its primary consumer
  was always intended to be *targeted* traversal — the `graph_neighbors` agent
  tool in the Phase 3 budgeted agentic loop — not blind channel fusion. Nothing
  in this result argues against the graph as a navigation substrate.

## Why the channel hurt

**Hub-symbol flooding.** Trace questions name exactly the symbols with the
highest graph degree (in click's graph: `invoke` touches 2,376 edges, `cli`
15,088, `main` similar). A 1-hop expansion around a hub returns a large set of
same-confidence (0.6/0.3) neighbors in essentially arbitrary order, and RRF
grants every one of them fused rank credit — displacing the labeled evidence
that dense+sparse+symbol had correctly ranked high (`clk-trace-main-1` nDCG
0.76→0.42, `clk-parser-1` MRR 1.00→0.50, `clk-trace-runner-1` recall@25
1.00→0.50). The channel *did* act as the recall device ADR-0005 predicted — it
rescued the one question the other channels missed outright (`clk-arch-parser-1`
recall@10 0.33→1.00) — but a systematic ranking tax on 9 questions is not worth
one deep-recall rescue in a fused, precision-sensitive default.

## Alternatives considered

- **Ship it on, because architecture recall@10 hit 1.00** — rejected: nDCG/MRR
  regress on both target categories; recall wins that arrive below rank 5 are
  better captured by the agent loop asking for neighbors explicitly.
- **Remove the channel** — rejected: opt-in retention costs nothing and keeps
  the A/B a one-line command, same as reranking (ADR-0010).
- **Tune before deciding** (degree-capped expansion — skip terms whose matched
  symbols exceed N edges; down-weighted RRF contribution; edge-kind filtering) —
  deferred, not rejected: these are the obvious candidate fixes and are recorded
  here for a future pass, but the default flips only when a measured
  configuration beats the baseline on the trace/architecture categories.

## Consequences

- Default retrieval remains dense + sparse + symbol RRF (36-Q baseline: MRR
  0.92 / nDCG@10 0.77; trace nDCG 0.76, architecture nDCG 0.85).
- The trace/architecture categories now exist as first-class eval units with
  per-category reporting, so the Phase 3 agent loop (task 25) and hierarchical
  enrichment (task 26) are measured against the same bar.
- Second worked example (after ADR-0010) of "evaluation before optimization"
  keeping a plausible-sounding channel out of the default path.
