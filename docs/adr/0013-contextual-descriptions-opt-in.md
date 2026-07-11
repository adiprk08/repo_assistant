# ADR-0013: Contextual chunk descriptions opt-in (measured no-lift)

**Status:** Accepted (2026-07-11) — implements the "contextual chunk descriptions" slice of the enrichment work ([ROADMAP](../ROADMAP.md) Phase 3); the file/dir/repo summary hierarchy is deferred.

## Context

Hierarchical enrichment (ROADMAP Phase 3) has two halves: contextual chunk
descriptions (a per-chunk blurb folded into the embedded text, targeting
*retrieval*) and a file/dir/repo summary hierarchy + repo map (targeting
*generation* context). Contextual descriptions are the cheaply-measurable
retrieval lever and the plausible fix for the trace category still below its exit
target (nDCG@10 0.75 vs 0.85), so they were built and measured first as a bounded
MVP before committing to the rest.

`ra index --enrich` asks Haiku for a one-line description of each code chunk (one
call per file, best-effort JSON) and prepends it to `embed_text`; the cited span
is untouched. Enriching + re-indexing click at the pinned benchmark commit (so
only the embedded text differs from baseline) and running a cost-free
retrieval-only A/B:

| Category (click) | Baseline | + descriptions |
|---|---|---|
| trace nDCG@10 / recall@5 | 0.75 / 0.80 | 0.74 / 0.80 |
| architecture nDCG@10 / recall@5 | 0.85 / 0.83 | 0.83 / 0.83 |

No improvement — flat within noise, marginally down on the target categories.

## Decision

- **Contextual descriptions stay opt-in** (`ra index --enrich`), off by default.
  The `describe_file_chunks` / `enrich_chunks` path and the `Chunk.context` field
  are kept so the decision stays re-measurable.
- **Stage B (summary hierarchy + repo map) is not built.** The plan gated it on
  Stage A showing a retrieval lift; it didn't. Summaries target generation
  context, a separate (unmeasured-here) axis — deferred until there is a demand
  or a benchmark that isolates its value.

## Why no lift

The golden questions name concrete symbols (`Parse`, `invoke`, `printHelp`),
which the symbol + BM25 channels already resolve to the right chunk. A contextual
blurb helps when a chunk's purpose is *opaque from its code* and the query is
natural-language — the regime "contextual retrieval" is designed for. This
benchmark doesn't exercise that regime, and prepending prose slightly dilutes the
code-focused embedding, hence the marginal regressions.

## Alternatives considered

- **Roll out enrichment by default** — rejected: no measured benefit, and a
  per-chunk index-time LLM cost on every repo.
- **Remove it** — rejected: the mechanism is sound and cheap to keep opt-in;
  a fairer benchmark could still vindicate it.

## Consequences

- Default indexing is unchanged (no enrichment); retrieval quality and the CI gate
  are unaffected.
- Follow-up to fairly test both this and the agent path (ADR-0012): a
  natural-language-heavy question set over prose-light code.
- Fourth worked example (after ADR-0010, 0011, 0012) of "evaluation before
  optimization" keeping an unproven enhancement out of the default path — the
  bounded Stage-A MVP spent ~$1 to avoid a multi-day Stage-B build.
