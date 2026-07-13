# ADR-0023: Web authentication and user accounts (GitHub OAuth, per-user ownership)

**Status:** Accepted (2026-07-14)

## Context

Through ADR-0016 the service had *callers* (API keys) but no *users*: every key
saw every repo and every chat session — a single shared, unowned workspace
(ADR-0015 explicitly flagged "sessions are unauthenticated" as open). The product
now needs real accounts: sign in, a private library of repositories, and private
conversations. This is the identity model ADR-0016 named JWT/OAuth as the
escalation path for, and it lands the multi-tenant story ADR-0009 partitioned the
*index* for but never scoped to a *person*.

Two forks shaped the design and were decided with the user:

1. **Auth method → GitHub OAuth.** It fits a GitHub-repo tool, stores no
   passwords, and shares an identity with the GitHub App direction (ADR-0020).
2. **Ownership → per-user, over a shared index.** Each user sees only what they
   add, but the heavy artifacts are not duplicated per user.

## Decision

- **GitHub OAuth login, server-side sessions.** `/auth/github/login` →
  `/auth/github/callback` performs the standard code exchange, upserts a `users`
  row (`github_id` unique), and creates a **server-side** session: an opaque token
  in an `httpOnly` cookie, of which only the **SHA-256** is stored (`web_sessions`),
  mirroring the ADR-0016 "hash credentials at rest" discipline. Server-side (not a
  JWT) so sessions are individually revocable with no signing-key management. Login
  CSRF is handled by an OAuth `state` double-submit (random value mirrored in a
  short-lived cookie and the redirect).

- **Shared index, per-user library.** The index (snapshots/chunks/Qdrant vectors)
  stays deduplicated by repo+commit as before (ADR-0009). A `user_repos` join is
  each user's **library** over it. Registering an already-indexed repo adds a
  membership **instantly** — no re-index — honouring the embedding-cache/"re-index
  cost ~zero" ethos. Chat sessions are inherently personal (`chat_sessions.user_id`).

- **Access control at the route boundary, denials as 404.** `current_user`
  resolves identity from **either** the session cookie **or** a Bearer API key.
  `list` is scoped to the caller's library; `get`/`search`/`chat`/session routes
  require membership; session ownership is enforced. A denial returns **404**, not
  403, so the API never reveals that a repo the caller can't see exists. Retrieval
  internals are untouched — they already scope by `repo_id`; guarding the boundary
  is sufficient and keeps the hot path clean.

- **API keys become personal access tokens.** Keys now carry a `user_id`
  (ADR-0016 keys were unowned). `ra apikey create` binds to the singleton `local`
  user; the same key authenticates the HTTP API *as* that user. MCP and CLI talk to
  the DB directly and are unaffected.

- **Same-origin proxy for the browser.** The web app calls `/api/*` on its own
  origin; Next.js rewrites proxy that to the API server-side. The session cookie is
  therefore **first-party** with `SameSite=Lax`, sidestepping the cross-origin
  `SameSite=None; Secure` friction that would otherwise break cookies in local dev
  and behind a single reverse proxy in prod. Defense-in-depth: an Origin check on
  cookie-authenticated unsafe-method requests (bearer-key callers carry no cookie
  and are skipped).

- **`require_auth` (was `require_api_key`).** Default on. Off ⇒ the API runs open
  as the `local` user, so a dev instance is fully usable without logging in and
  data still has a real owner.

## Alternatives considered

- **Email + password.** Full control but we would own password hashing, reset, and
  verification — more security surface for no gain over GitHub for a GitHub tool.
- **Managed auth (Clerk/Auth0/Supabase).** Fastest to robust, but an external
  dependency + cost, and less to show as first-party engineering in a portfolio.
- **Per-user copies of the index** (owner FK on `repos`, unique per `(owner,url)`).
  Simpler conceptually but re-indexes every public repo per user — wasteful and
  against the dedup ethos. The membership join gives the same "only my repos" UX
  over one shared index.
- **JWT in `localStorage`.** No server session table, but XSS-exfiltratable, not
  revocable without a denylist, and needs signing-key rotation. Server-side cookie
  sessions are revocable and keep the token out of JS.
- **Cross-origin cookies (`SameSite=None; Secure`) instead of a proxy.** Requires
  HTTPS in dev and precise CORS-credentials config; the proxy makes the cookie
  first-party and is simpler and safer.

## Consequences

- The web UI requires GitHub OAuth to be configured (`RA_GITHUB_OAUTH_CLIENT_ID`
  / `_SECRET`, callback `<web_base_url>/api/auth/github/callback`); unset ⇒ login
  is unavailable (503) though API-key/local access still works.
- Data is now owned: existing pre-auth repos/sessions have a null owner and are in
  nobody's library. There is **no legacy-claim step** — a user re-adds a repo URL
  and gets an instant membership over the existing index; old sessions stay hidden.
- `chat_sessions.user_id` and `api_keys.user_id` are **nullable** for those legacy
  rows; new rows always carry an owner.
- **Amends ADR-0016**: API keys are now user-scoped and rate limiting keys off the
  user id; the bearer mechanism and hashing are unchanged. Relates to **ADR-0009**
  (tenancy now has real tenants) and **ADR-0017** (the UI is now cookie-authed via
  a same-origin proxy, not a `localStorage` key).
- **Deferred:** an in-app access-tokens management page (CLI covers minting for
  now); shipping the web app in the prod compose behind one origin; GitHub-App
  identity unification (ADR-0020) so login can also grant private-repo access;
  session sweeping/GC for expired `web_sessions`.
