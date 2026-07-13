# ADR-0016: API-key authentication and rate limiting

**Status:** Accepted (2026-07-12); **amended by [ADR-0023](0023-web-auth-and-user-accounts.md)** (2026-07-14) — API keys are now user-scoped personal access tokens, `require_api_key` became `require_auth`, and rate limiting keys off the user id. The hashing, bearer transport, and fail-open limiter below are unchanged.

## Context

The Phase 4 API service (ADR-0014) shipped unauthenticated — every endpoint was
open, and ADR-0014/0015 both flagged auth as the remaining gap before the product
surface is usable. The service has no human users yet (no login, no sessions tied
to identities), just callers — the CLI, the coming Next.js UI, and eventually
scripts. It needs a credential that is simple to issue, revoke, and rate-limit,
without standing up a user/identity system.

## Decision

- **Hashed, DB-backed API keys.** A key is a 256-bit random token,
  `ra_<43 url-safe chars>`. Only its **SHA-256** is stored (`api_keys` table),
  alongside a non-secret `key_prefix` for display and `last_used_at`/`revoked_at`
  for audit and revocation. The plaintext is shown **once** at creation and is not
  recoverable.

- **SHA-256, not bcrypt/argon2.** Password hashing is deliberately slow to resist
  brute force of *low-entropy* human secrets. An API key already carries 256 bits
  of entropy, so stretching buys no meaningful security while adding latency to
  **every** request (lookup is by hash). SHA-256 + a unique index is the right
  tool; the DB lookup is effectively constant-time on the indexed hash.

- **Bearer transport.** `Authorization: Bearer <key>`, via FastAPI's `HTTPBearer`
  (so it shows in the OpenAPI schema). `auto_error=False` — we raise our own
  `AuthenticationError` so 401s carry the same JSON envelope as every other error
  and a `WWW-Authenticate: Bearer` header.

- **One `secured` dependency** guards the data routers (repos, sessions, search,
  chat) via `include_router(dependencies=[Depends(secured)])`; `/health` stays
  open for liveness probes. `secured` authenticates, then charges the rate-limit
  budget keyed by the authenticated key id.

- **Redis fixed-window rate limiting, fail-open.** One `INCR` + first-hit `EXPIRE`
  per key per window (`rate_limit_requests` / `rate_limit_window_seconds`, default
  120/60s). Shared across API replicas via Redis (already in the stack). If Redis
  is unreachable the limiter **fails open** (logs, allows) — a rate limiter must
  never be the component that takes the whole API down. Over-budget returns 429
  with `Retry-After`. The limiter sits behind an interface with `Redis`, `Noop`
  (dev / rate_limit_enabled=false), and `InMemory` (tests) implementations.

- **Bootstrap via the CLI, never an open endpoint.** `ra apikey create|list|revoke`
  mints and manages keys against the DB directly; there is deliberately no
  self-serve key-creation route (that would be an unauthenticated privilege
  escalation). The first key is created out-of-band by whoever runs the service.

- **Config-gated.** `require_api_key` and `rate_limit_enabled` default **on**;
  a local/dev instance can flip them off. Tests exercise the real secured surface
  (the client authenticates with a minted key) plus dedicated 401/429 cases.

## Alternatives considered

- **JWT / OAuth2.** Right when there are users, third-party clients, or delegated
  scopes — none of which exist yet. Self-issued API keys are simpler, instantly
  revocable (no token-expiry/refresh dance), and sufficient for a first-party
  service. JWTs remain the escalation path if a real identity model arrives.
- **Static key(s) in config / env.** Zero infrastructure, but unrevocable without
  a redeploy, unauditable, and awkward to rotate or scope per client. The DB table
  costs little and buys revocation + `last_used` visibility.
- **bcrypt/argon2 for the key hash.** Rejected above — stretching a high-entropy
  token is latency for no security.
- **Sliding-window / token-bucket rate limiting.** Smoother at window edges, but
  more state and code. Fixed-window is the standard first cut; the interface makes
  swapping it later a one-file change.
- **Fail-closed rate limiting.** Safer against abuse during a Redis outage, but it
  converts a cache/limiter blip into a total outage — worse for a service whose
  primary risk is availability, not abuse. Chose fail-open with a warning log.

## Consequences

- Every data endpoint now requires a key; the CLI and UI must send one, and the
  bootstrap step (`ra apikey create`) is part of standing the service up.
- Keys are revocable and auditable (`last_used_at`), and rotating is create-new +
  revoke-old. There is no self-serve issuance — intentional.
- Rate limiting is best-effort and per-key: it protects against runaway callers,
  not against a distributed flood (that is a Phase 5 / edge concern), and it
  degrades to "allow" if Redis is down.
- Still open (Phase 5 hardening): per-key scopes/quotas, key expiry, and audit of
  auth failures as a first-class metric.
