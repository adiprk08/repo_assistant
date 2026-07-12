# ADR-0014: API service surface and SSE streaming

**Status:** Accepted (2026-07-12)

## Context

Phase 4 turns the measured CLI core into a product: an HTTP surface for repo
registration, ingestion-progress, search, and chat, plus the worker that runs
ingestion off the request path. [ADR-0008](0008-job-queue.md) already fixed the
queue (arq/Redis) and the `jobs` row as the resumable state machine; this record
covers the decisions ADR-0008 left open — the endpoint shape, how progress and
chat reach the client, and how the library's error taxonomy surfaces over HTTP.

The governing constraint is CLAUDE.md's **thin-shell** rule: `api/` and
`workers/` add no business logic. Everything they need already exists in the
library (`indexing.pipeline`, `reasoning.answer_routed`, `retrieval.hybrid_retrieve`,
`cli.runtime`), which was built provider-injectable precisely so the API and
worker could reuse the CLI's composition.

## Decision

- **Composition.** `create_app()` builds one `Runtime` (providers, vector index,
  session factory — the same object the CLI composes) and one `IngestionQueue`,
  held on `app.state` for the process lifetime via the lifespan handler and
  closed at shutdown. Both are injectable so tests supply fakes and drive the
  whole API over Postgres with zero API/infra cost. Routers are one library call
  each; request/response Pydantic models live only at the boundary
  (`api/schemas.py`), pipeline dataclasses are never leaked.

- **Endpoints.** `POST /repos` (register + enqueue, 202), `GET /repos`,
  `GET /repos/{id}` (detail = active snapshot + latest job), `DELETE /repos/{id}`;
  `GET /repos/{id}/job` and `.../job/stream`; `POST /repos/{id}/search`;
  `POST /repos/{id}/chat`; `GET /health`. Registration is idempotent on the
  normalized URL — re-posting queues a fresh (re-)index, cheap thanks to the
  content-hash embedding cache.

- **Two progress vocabularies on the job row.** The pipeline emits fine-grained
  **stages** (`cloning…indexing`) through an `on_stage` callback the worker
  persists; the worker overlays a coarse **state** (`queued → running →
  succeeded | failed`) for liveness. Clients that only need "is it done?" read
  `state`; a progress UI reads `stage` + `progress`.

- **Progress streaming by DB polling, not Redis pub/sub.** `.../job/stream` polls
  the `jobs` row (interval = `job_stream_poll_seconds`) and emits an SSE
  `progress` event on every change, then a terminal `done`. Polling keeps the API
  decoupled from the worker's transport, survives worker restarts, and needs no
  extra fan-out infrastructure. The worker already persists each transition for
  durability, so the read side is free.

- **Chat streaming via an `on_text` callback threaded through the pipeline.**
  `answer_routed`/`generate_answer` take an optional `on_text`; the Anthropic
  adapter implements `generate_stream` (default impl emits one delta, so fakes
  and non-streaming providers satisfy the contract). The chat router bridges
  `on_text` to an `asyncio.Queue` drained by the SSE generator: `token` events as
  text is produced, then a `done` event carrying **verified** citations and
  routing metadata. Citation verification stays post-hoc and unchanged — the
  final message is parsed exactly as in the non-streaming path. On the agent
  path only the final answer is streamed; the exploration turns are never
  surfaced.

- **Error mapping is centralized.** A single exception handler maps the
  `core/errors` taxonomy to status codes (NotFound→404, Validation→422,
  Ingestion→400, Provider→502, else 500); routers raise domain errors and never
  translate inline. Chat resolves the repo *before* opening the stream so a
  missing/unindexed repo is a real HTTP 404, not an SSE error after headers ship.

- **Worker retry policy: `max_tries=1` for now.** A failed ingestion marks the
  job + repo `failed` and re-raises; it is not auto-retried, because a provider
  misconfiguration would otherwise burn embedding/LLM spend fivefold. Checkpointed
  auto-resume (embed picking up at the last committed batch) is the ADR-0008
  design intent and is deferred to Phase 5 hardening.

## Alternatives considered

- **Redis pub/sub for progress.** Lower latency, but couples the API to the
  worker's transport and needs reconnection/replay handling; polling a row we
  already persist is simpler and durable. Revisit if sub-second progress matters.
- **WebSockets for chat.** Bidirectional and unnecessary here — chat is a
  request then a one-way token stream; SSE is simpler, proxy-friendly, and matches
  the job-progress surface. Reconsider for interactive multi-turn tool approval.
- **OpenAI-compatible `/v1/chat/completions` shape.** Attractive for client reuse,
  but our first-class citations + routing metadata don't fit that schema cleanly;
  kept a native shape. A compatibility shim is a Phase 6 candidate.
- **A separate `job_events` table / event log.** More faithful history, but the
  single mutable `jobs` row is enough for live progress; an append-only event log
  is a Phase 5 observability item.

## Consequences

- The API and worker are genuinely thin: the same library path the CLI and eval
  harness exercise now serves HTTP, so retrieval/reasoning changes reach the
  product for free and stay covered by the existing suite.
- Streaming is uniform: one `on_text` seam powers CLI, API, and any future
  client; the streaming and non-streaming answers are byte-identical (same
  verified citations), so quality metrics are unaffected by the transport.
- Progress is eventually-consistent to within one poll interval — acceptable for
  a minutes-long pipeline, and the knob is config.
- Auth and rate limiting are still open (next in Phase 4); every endpoint is
  currently unauthenticated and assumes a trusted caller.
