# ADR-0015: Conversation memory — snapshot-pinned sessions, rolling summary, follow-up condensation

**Status:** Accepted (2026-07-12)

## Context

Phase 4 adds multi-turn chat. A conversation needs three things the stateless
chat endpoint lacks: a stable view of the code across turns, bounded context as
history grows, and follow-up questions that retrieve sensibly ("how does *it*
work?" retrieves nothing on its own). ARCHITECTURE §6/§7 already specified the
shape — `chat_sessions`/`chat_messages`, a verbatim window rolled into a summary,
snapshot binding — and follow-up condensation was explicitly **deferred from
Phase 2** pending exactly this memory. This record fixes the mechanics.

## Decision

- **Sessions pin a snapshot at creation.** `POST /repos/{id}/sessions` records the
  repo's *current* active `snapshot_id` + `commit_sha` on the session. Every turn
  in that session is answered against that pinned snapshot, even if the repo
  re-indexes mid-conversation. This keeps citations and answers reproducible and
  consistent within a conversation (ADR-0006 "session's pinned commit", ADR-0009).
  A stateless chat call (no `session_id`) still uses the live active snapshot.

- **Verbatim window + incremental rolling summary.** The last
  `history_window_messages` (config, default 6) turns are kept verbatim; older
  turns are folded into `chat_sessions.summary`. The summary is updated
  **incrementally** — a `summary_covered_messages` counter tracks how many of the
  oldest messages are already summarized, so each turn folds in only what newly
  aged out of the window (one summarizer call per turn once long), never a
  re-read of the whole history. The summary is injected into generation as a
  synthetic leading exchange so role alternation stays valid.

- **Follow-up condensation, on by default for sessions.** Before retrieval/routing,
  a follow-up is rewritten (haiku) into a standalone query using the recent turns
  + summary; retrieval, the intent router, and agent exploration all use that
  **condensed query**, while generation answers the user's **raw** question with
  history for context. Condensation only fires when a session has prior context and
  falls back to the raw question on an empty rewrite — it can never lose intent.
  It is `condense_followups` (default true), but activates only for sessions.

- **Two questions vs. one, cleanly separated.** `answer_routed` gained
  `history` (grounds generation) and `retrieval_query` (drives routing/retrieval/
  exploration, defaults to the raw question). `run_agent` explores with the
  condensed query but generates for the raw one. The stateless single-turn path is
  unchanged — both new params default to "no history / query == question".

- **Persistence contract.** User turns store the raw question; assistant turns
  store the answer text, the **verified** citations (JSONB), and token usage. A
  monotonic `seq` identity column orders messages — `created_at` cannot, because
  Postgres `now()` is transaction-constant and both turns of one exchange share it.
  The pure memory helpers (`reasoning/memory.py`) are storage-agnostic and
  unit-tested with fakes; the DB glue lives in `reasoning/conversation.py`.

## Alternatives considered

- **Follow the live active snapshot each turn.** Simpler, but a re-index mid-chat
  would silently shift the ground truth under the conversation and break earlier
  citations. Pinning is the point of snapshots.
- **Full-history re-summarization each turn.** Correct but O(history) tokens per
  turn; the coverage counter makes it O(1) turns of new work.
- **Truncate history, no summary.** Cheapest, but loses long-range context the user
  set up early — the exact thing memory exists to keep.
- **Summarize with the generation model (opus).** Overkill; the summary and the
  follow-up rewrite are cheap classification-grade tasks — haiku (the router model)
  is the right tier and keeps per-turn cost low.
- **Store chunk IDs per session for cheap re-expansion** (ARCHITECTURE §6, "prefer
  re-expansion over fresh retrieval"). Deferred — persisting per-message citations
  is the foundation; the re-expansion optimization is a later lever, and (like the
  levers before it) should be earned by a measured multi-turn benchmark.

## Consequences

- Multi-turn chat is coherent and reproducible; the transport (SSE) and the
  verified-citation contract are unchanged — streamed session answers are still
  byte-identical to the stateless path.
- **Measurement debt (honest):** follow-up condensation is a retrieval lever, and
  this project's discipline is "no retrieval change ships unmeasured." There is no
  multi-turn eval set yet, so condensation is shipped **on** on the argument that
  without it multi-turn retrieval is *broken*, not merely unoptimized — but a
  multi-turn challenge set (pronoun/ellipsis follow-ups) is owed to justify it and
  tune the window/summary knobs. Tracked as Phase 4/5 eval work.
- Cost grows by one cheap haiku call per turn for condensation, plus one when the
  window overflows for the summary — bounded and config-gated.
- Auth still open: sessions are unauthenticated, so anyone who can reach the API
  can read any session's history until API-key auth lands (next in Phase 4).
