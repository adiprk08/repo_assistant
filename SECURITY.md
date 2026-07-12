# Security

How Repo Assistant defends its trust boundaries. Design rationale lives in
[docs/adr/0021](docs/adr/0021-security-pass.md), [docs/adr/0016](docs/adr/0016-api-auth-and-rate-limiting.md),
[docs/adr/0020](docs/adr/0020-private-repositories.md), and [docs/RISKS.md](docs/RISKS.md).

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

- Every data route requires an API key (SHA-256-hashed, revocable) and is
  per-key rate-limited ([ADR-0016](docs/adr/0016-api-auth-and-rate-limiting.md)).
- The GitHub webhook is HMAC-signature-verified, not API-key-authed.
- Per-repo tenancy is enforced at the storage layer (every query filters `repo_id`).

## Dependencies

`pip-audit` runs in CI and fails the build on a known-vulnerable dependency.

## Reporting a vulnerability

This is a portfolio project; open a GitHub issue describing the concern (avoid
posting exploit details for anything sensitive).
