# Security

How Repo Assistant defends its trust boundaries. Design rationale lives in
[docs/adr/0021](docs/adr/0021-security-pass.md), [docs/adr/0016](docs/adr/0016-api-auth-and-rate-limiting.md),
[docs/adr/0023](docs/adr/0023-web-auth-and-user-accounts.md),
[docs/adr/0020](docs/adr/0020-private-repositories.md),
[docs/adr/0024](docs/adr/0024-untrusted-tree-and-deployment-hardening.md), and
[docs/RISKS.md](docs/RISKS.md).

## Untrusted working tree (the clone boundary)

The *shape* of a cloned repository is untrusted, not just its file contents
([ADR-0024](docs/adr/0024-untrusted-tree-and-deployment-hardening.md)):

- A tracked path is indexed only if it is a **regular file inside the clone**.
  Symlinks — and anything resolving outside the clone root — are refused before
  the read, so `notes.md -> /etc/passwd` cannot pull a host file into the index.
- Git runs with `core.symlinks=false`, `protocol.file.allow=never`, and
  `submodule.recurse=false`, so no link is materialised and no other transport is
  reachable during clone or checkout.
- Every git call is bounded by a wall-clock timeout and the child process is
  killed on expiry; a stalled remote cannot pin a worker.
- Indexing stops accepting files past whole-repo ceilings (20k files / 500MB) in
  addition to the 1MB per-file cap, bounding embedding spend and index growth.

## Untrusted repository content (prompt injection)

Repository text is treated as **data, never instructions**:

- Retrieved chunks are passed as fenced `document` blocks; the system prompt states
  repo content carries no instructions and to refuse over inventing.
- The agent loop's tools are **read-only over the index** — `search_code`,
  `get_symbol`, `read_span`, `graph_neighbors`, `list_dir`. There is no tool that
  writes, executes, reaches the filesystem, or makes network calls, so an injected
  "do X" has nothing to call. (Enforced by test.)
- Citations are **verified post-hoc** against the exact indexed source; anything
  fabricated is dropped.

## Secrets

Credentials must never enter the index or a prompt:

- Secret-named files (`.env`, `*.pem`, `id_rsa`, `.netrc`, …) are excluded by name.
- **File content** is scanned for high-confidence credential patterns (PEM private
  keys, AWS `AKIA…`, GitHub `ghp_…`/`github_pat_…`, `sk-ant-…`, Google `AIza…`,
  Slack, Stripe, GitLab). A match keeps the whole file out of the index.
- App secrets and repo tokens live in the environment / `.env` (never committed).
  GitHub installation tokens are stored **Fernet-encrypted** and never logged
  ([ADR-0020](docs/adr/0020-private-repositories.md)).

## Service

- Every data route requires an authenticated **user**, resolved from a **session
  cookie** or a personal-access-token **API key** (both SHA-256-hashed at rest,
  revocable), and is per-user rate-limited ([ADR-0016](docs/adr/0016-api-auth-and-rate-limiting.md),
  [ADR-0023](docs/adr/0023-web-auth-and-user-accounts.md)).
- **Per-user ownership**: a user sees only repos in their library and their own
  chat sessions; cross-user access is denied as **404** (existence is never
  leaked). Per-repo tenancy is still enforced at the storage layer (queries filter
  `repo_id`).
- **Web sessions** are server-side and revocable — the cookie holds an opaque
  token, only its SHA-256 is stored, and it is `httpOnly` + `SameSite=Lax`, plus
  `Secure` whenever `RA_ENVIRONMENT=prod` (overridable either way). The browser is
  same-origin (Next proxies `/api/*`), so the cookie is first-party.
- **CSRF**: login uses an OAuth `state` double-submit; cookie-authenticated
  unsafe-method requests are Origin-checked (bearer-key callers carry no cookie).
- The GitHub webhook is HMAC-signature-verified, not user-authed.
- Vector-store reads and deletes constrain on the tenant (`repo_id`) at the store
  itself, so an id from another repo cannot be read or removed even by a caller
  that forgets to scope ([ADR-0024](docs/adr/0024-untrusted-tree-and-deployment-hardening.md)).

## Deployment

- The prod compose **requires** `RA_PG_PASSWORD`, `RA_QDRANT_API_KEY`, and
  `RA_REDIS_PASSWORD` and refuses to start without them — there are no default
  datastore credentials. Postgres, Qdrant, and Redis publish no host port; they
  are reachable only on the compose network.
- Containers run as a non-root user; TLS termination is the operator's
  responsibility (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## Known limits

- **Parsing is not time-bounded.** tree-sitter exposes no in-process cancellation,
  so a pathological file is bounded only by the per-file/whole-repo size ceilings.
- **No per-user LLM cost cap.** Rate limiting is request-count only, and it fails
  open if Redis is unreachable. Sandboxing ingestion and a token budget are the
  tracked follow-ups ([ADR-0024](docs/adr/0024-untrusted-tree-and-deployment-hardening.md)).

## Dependencies

`pip-audit` runs in CI and fails the build on a known-vulnerable dependency.

## Reporting a vulnerability

This is a portfolio project; open a GitHub issue describing the concern (avoid
posting exploit details for anything sensitive).
