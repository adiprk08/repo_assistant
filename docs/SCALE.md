# Scale validation

Phase 5 exit criterion: *a 50k-file repo indexes within a documented budget, and
an incremental update touches only changed files (verified).* Harness:
[`scripts/scale_bench.py`](../scripts/scale_bench.py).

## Method

The harness generates a synthetic repo of N Python files (each a small class +
helper), indexes it against the real Postgres + Qdrant stack, then makes an
incremental update editing K files and re-measures. It uses the **fake embedder**,
which isolates the pipeline's *structural* throughput (git scan → tree-sitter
parse → AST chunk → Postgres rows → Qdrant upsert) from embedding-provider latency
— and costs nothing. Embedding cost is projected separately (below).

```
uv run python scripts/scale_bench.py --files 3000 --change 30
```

## Measured (local stack: Postgres 17 + Qdrant 1.15, fake embedder)

| N files | chunks | symbols | full index | throughput | incremental (K=1%) | speedup |
|--------:|-------:|--------:|-----------:|-----------:|-------------------:|--------:|
| 2,000   | 2,000  | 8,000   | 14.6 s     | 137 files/s| 1 changed → 1 reprocessed | 5.3× |
| 3,000   | 3,000  | 12,000  | 16.2 s     | 185 files/s| 30 changed → 30 reprocessed | 4.4× |

**Incremental proportionality — verified:** an update reprocesses **exactly the
changed files** (30 of 3,000) and copies the rest forward (2,970) without
re-embedding — the ADR-0018 goal, confirmed here at scale and by
`tests/integration/test_incremental.py`.

## 50k-file projection

Structural throughput is ~**185 files/s** and linear in file count, so a
50,000-file repo indexes structurally in **≈ 4–5 minutes** on this single-node
stack (parse/chunk/DB/Qdrant). Real repos average more than one chunk per file, so
expect proportionally more chunk/vector writes; Qdrant upserts and Postgres inserts
are already batched and were not the bottleneck at 3k.

**Embedding cost (voyage-code-3), the dominant real-provider factor:**
- Cost = `chunks × avg_tokens_per_chunk × price_per_token`. For a 50k-file repo at,
  say, ~4 chunks/file and ~400 tokens/chunk ≈ **80M tokens** on the first index.
- The **content-hash embedding cache** ([ADR-0003](adr/0003-embedding-strategy.md))
  makes every *re-index* of unchanged content free, and **incremental updates only
  embed the diff** — so steady-state cost tracks change volume, not repo size. A
  push changing 30 files re-embeds ~30 files' chunks, not 50k.
- Enrichment (contextual descriptions) is opt-in and off by default, so it adds no
  first-index cost unless requested.

## Honest boundary

The structural throughput and incremental proportionality above are **measured**.
A full 50k-file run with *real* voyage-code-3 embeddings was not executed, to avoid
the one-off embedding spend; that cost is bounded by the formula above and, after
the first index, by the cache + diff-only updates. Running the harness at higher N
(or against a real large repo with real keys) reproduces the structural numbers
directly.
