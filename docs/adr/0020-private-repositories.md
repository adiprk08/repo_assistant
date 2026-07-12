# ADR-0020: Private repositories via a GitHub App with encrypted tokens

**Status:** Accepted (2026-07-12)

## Context

So far the assistant only indexes public repos (`git clone` over anonymous HTTPS).
Phase 5 adds private repositories. That needs (a) an auth mechanism GitHub blesses
for server-to-server repo access, (b) a way to clone with that credential, and (c)
safe storage of the credential. Personal access tokens are the easy path but are
coarse (a user's whole account), long-lived, and awkward to rotate/scope.

## Decision

- **GitHub App, not PATs.** A GitHub App is installed on selected repos/orgs and
  yields **installation access tokens** that are least-privilege (only the granted
  repos), short-lived (~1 hour), and revocable by uninstalling — the right shape
  for a service. The app authenticates by signing a short-lived **RS256 JWT** with
  its private key (`github_app_id` + `github_app_private_key`), then exchanges it
  at `POST /app/installations/{id}/access_tokens` for an installation token.

- **Tokens encrypted at rest (Fernet), never logged.** Installation tokens are
  cached in a `github_installations` row **encrypted** with Fernet
  (`token_encryption_key`) — symmetric AES-128-CBC + HMAC, authenticated. The
  plaintext token lives only in memory during a clone. Nothing logs token values;
  the clone URL (which embeds the token) is never logged, only the sanitized URL.
  Fernet now, envelope encryption with a KMS when cloud-deployed.

- **Token cache with refresh.** `github_installations` caches the encrypted token
  and its expiry per `installation_id`; a token is reused until it is within a
  refresh margin of expiry, then re-minted. This avoids a token exchange on every
  clone while keeping tokens fresh.

- **Authenticated clone.** `clone()` gains an optional `token`; for a private repo
  it rewrites the HTTPS URL to `https://x-access-token:<token>@github.com/...`
  (GitHub's documented App-token clone form). Public repos are unchanged (no
  token, anonymous clone).

- **Repo carries its installation.** `repos.visibility` already exists;
  `repos.installation_id` (nullable) links a private repo to the installation whose
  token can read it. Indexing a private repo mints/reads a token for that
  installation and clones with it.

- **All of it is optional and config-gated.** With no `github_app_*` /
  `token_encryption_key` set, the service behaves exactly as before (public only).
  Attempting to index a private repo without the app configured is a clear error,
  not a crash.

## Alternatives considered

- **Personal access tokens (classic/fine-grained).** Simplest, but account-scoped,
  long-lived, and per-user — poor for a shared service and a bigger blast radius if
  leaked. Fine-grained PATs improve scoping but still aren't installable/revocable
  like an App. Rejected as the primary mechanism (could be a fallback).
- **OAuth device/web flow (user token).** Acts as the user, not the app; tokens are
  user-scoped and expire/refresh differently. Good for "act on behalf of a user"
  features, not for background indexing. The App installation model is the fit.
- **Store tokens in plaintext / rely on DB-at-rest encryption only.** Application-
  level Fernet means a DB dump alone doesn't leak usable tokens, and keeps the
  secret boundary in the app where we control logging. Cheap; kept.
- **Vault/KMS now.** Right for production, operationally heavy for this stage;
  Fernet with a single rot-able key is the documented step, KMS the escalation.

## Consequences

- Private repos index through the same pipeline; only acquisition changes (token
  minting + authenticated clone). Incremental updates and webhooks work unchanged.
- New config the operator must set to enable private repos: a GitHub App
  (`github_app_id`, `github_app_private_key`) and a `token_encryption_key`
  (generate with `Fernet.generate_key()`). Losing the encryption key invalidates
  cached tokens (harmless — they re-mint) but is required to decrypt existing rows.
- New dependency: `pyjwt[crypto]` (RS256 signing + Fernet via `cryptography`).
- **Boundary honestly noted:** the interactive *installation* UX (the GitHub
  "Install App" redirect + `installation` webhook that records `installation_id`)
  needs a registered GitHub App to exercise end-to-end. This change ships the
  server-side machinery — JWT, token exchange, encrypted cache, authenticated clone,
  data model — and is unit-tested with a generated key + mocked GitHub API; wiring
  the install redirect and verifying against a live App is the remaining step.
- Still open (later): secret rotation for `token_encryption_key`, revocation on
  `installation.deleted` webhooks, and per-repo token scoping audits.
