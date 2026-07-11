# ADR-0012: Agentic reasoning path opt-in (measured parity)

**Status:** Accepted (2026-07-11) — refines the "agent path" clause of [ADR-0006](0006-reasoning-pipeline.md); single-pass remains the default answering path. **Update (same day):** on the later NL-heavy challenge set (docs/EVALUATION.md §5) the agent path is the one lever that *beats* single-pass (MRR/nDCG +~25 %, recall@10 +10 pts) — so the "no measured benefit" below is specific to the saturated main set; the gain is real but modest at ~25× cost, so opt-in still stands.

## Context

ADR-0006 specified two-tier reasoning: a Haiku intent router sending single-hop
questions to a fast single-pass path and multi-hop questions to a budgeted Opus
tool-use loop over the index. Task 25 implemented all of it — the router, the
five read-only index tools (`search_code`, `get_symbol`, `read_span`,
`graph_neighbors`, `list_dir`), the budgeted loop, prompt caching, and an
`--agentic` eval mode — then measured it on the 54-question golden set
(docs/EVALUATION.md §5).

The agent path produces correct, well-grounded, richly-cited answers (judged
correctness 5.0, groundedness 4.71, pass 1.0, 13–25 citations tracing the flow).
But single-pass hybrid retrieval already answers the same benchmark questions
just as well (correctness 4.97, pass 1.0). The result is **parity, not
superiority**, at ~25× the per-query cost and latency. Two further findings:
budget adherence misses the Phase 3 exit criterion (in-budget rate 0.24 on click,
0.63 overall, vs the >0.95 target), and the router over-routes to the agent
(agent_path_share 0.89 on click — it flags many `explain` questions as
multi-hop).

## Decision

- **Single-pass stays the default** answering path for eval and the CI gate. The
  agent path is **opt-in**: `ra chat --path agent`, or `ra eval --agentic`.
- **Keep the whole implementation** — router, tools, loop, `--agentic` eval mode,
  and the routing metrics (router accuracy, budget adherence). They are correct,
  tested, and the substrate for the improvements below; the decision stays
  continuously re-measurable, as with reranking and the graph channel.
- **The graph is consumed here as designed** ([ADR-0011](0011-graph-channel-disabled-by-default.md)):
  `graph_neighbors` is a targeted agent tool, not a fused retrieval channel.

## Why parity, not a win

The benchmark's trace/architecture questions are multi-*file* but their evidence
is still surfaced by hybrid retrieval in one pass — so the agent's extra hops
gather the same spans the fast path already finds. The agent's value (adaptively
following evidence the query can't name) needs questions where single-pass
*fails*; the current set doesn't contain them. The retrieval-only proxy scoring
the agent *below* single-pass is a metric artifact: it ranks the agent's
*unordered* gathered set with rank-sensitive metrics, penalizing evidence that
was gathered but not early in exploration order (architecture recall@10 reached
1.0 — the evidence is there).

## Alternatives considered

- **Make the agent the default for multi-hop intents** (ADR-0006's stated lean) —
  rejected for now: no measured quality gain, 25× cost, and a failing
  budget-adherence criterion. "Correctness over cost" doesn't justify cost with no
  correctness delta.
- **Remove the agent path** — rejected: it works and is the intended vehicle for
  harder questions; opt-in retention costs nothing at the default.

## Consequences

- Default retrieval/answering is unchanged (single-pass, validated by the gate).
- Follow-ups to earn the default, tracked for a later pass: (1) a harder
  multi-hop benchmark where single-pass genuinely misses evidence; (2)
  budget-adherence fixes (better stopping or a higher budget — tuning already
  lifted in-budget rate 0.34→0.63); (3) router calibration to stop over-flagging
  `explain` as multi-hop.
- Third worked example (after ADR-0010, ADR-0011) of the "evaluation before
  optimization" rule keeping an unproven capability out of the default path.
