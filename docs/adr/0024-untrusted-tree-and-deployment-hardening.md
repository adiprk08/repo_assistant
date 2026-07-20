# ADR-0024: Untrusted working-tree boundary, resource ceilings, and deployment hardening

**Status:** Accepted (2026-07-20)

## Context

The repository went public, which changes the threat model: the code, the
deployment topology, and the defaults are now all readable by an attacker, and
anyone with a GitHub account can sign in and have the system clone a repository
they control.

A security audit against that model found the core design holding up — per-user
isolation, SQL parameterisation, the read-only agent tool surface, OAuth/session
handling, and the MCP server were all sound — but surfaced one real
trust-boundary break plus a set of missing ceilings and insecure-by-default
deployment knobs. ADR-0021 established the "repository content is untrusted"
posture for *file contents*; it did not cover the **shape of the tree itself**.

## Decision

- **A tracked path is only indexable if it is a regular file inside the clone.**
  `git ls-files` lists tracked *symlinks*, and `Path.read_bytes()` follows them.
  A repository containing `notes.md -> /etc/passwd` therefore caused the scanner
  to read a **host** file and index it, where it became retrievable through chat —
  an arbitrary-file-read escape from the clone. The scanner now refuses any entry
  that is a symlink or resolves outside the clone root (`SkipReason.SYMLINK`),
  *before* the read. This is the boundary; the checks below are depth behind it.

  The existing content secret-scan (ADR-0021) and binary heuristic incidentally
  blunted the worst targets (a co-located `sk-ant-…` key caused `.env` to be
  skipped; NUL bytes caused `/proc/self/environ` to be skipped) — but that is
  coincidence, not a boundary, and it does not hold for secret formats outside
  the pattern set. Refusing the link is the principled fix.

- **Git runs hardened, and never unbounded.** Every invocation carries
  `core.symlinks=false` (git materialises a tracked symlink as inert file text
  rather than a real link), `protocol.file.allow=never`, and
  `submodule.recurse=false`, so a hostile repository cannot get a link
  materialised or drag in content over another transport. Every call also takes a
  wall-clock timeout (300s network, 60s local) and the child process is **killed**
  on expiry — a stalled remote previously pinned a worker indefinitely. The
  timeout is handled inside the runner rather than by wrapping callers in
  `asyncio.timeout()`, because a cancelled task would leave the git child alive.

- **Whole-repository ceilings, not just per-file.** `MAX_FILE_BYTES` bounded any
  single read but not the aggregate, so one enormous repository drove unbounded
  embedding spend and index growth. Indexing now stops accepting files past
  `MAX_REPO_FILES` (20k) or `MAX_REPO_BYTES` (500MB), recording
  `SkipReason.REPO_LIMIT`. Partial indexing is deliberate: a too-large repo
  yields a usable index over what fit rather than a hard failure.

- **Tenancy is enforced at the vector store, not just above it.** `fetch` and
  `delete` addressed Qdrant points purely by id; they were tenant-correct only
  because every caller sourced ids from snapshot-scoped queries. That is an
  application-code invariant of exactly the kind that breaks silently when a new
  retrieval channel is added. Both now constrain on the `repo_id` payload — `fetch`
  drops foreign points, `delete` combines `HasIdCondition` with the tenant filter.

- **Secure cookies follow the environment.** `session_cookie_secure` was a plain
  `False` default, so an operator following DEPLOYMENT.md could serve session
  cookies without `Secure` behind TLS. It is now tri-state: unset derives from
  `environment == "prod"`, and an explicit value still wins in either direction.

- **No default datastore credentials.** The prod compose shipped Postgres with a
  well-known password and no auth on Qdrant or Redis. It now requires
  `RA_PG_PASSWORD`, `RA_QDRANT_API_KEY`, and `RA_REDIS_PASSWORD` from the
  environment and **fails to start** if any is missing (`${VAR:?message}`), with
  Qdrant and Redis auth switched on. None of these services publish a host port;
  the credentials are depth for when that stops being true.

## Consequences

- The symlink refusal is a behaviour change: repositories that legitimately track
  symlinks (a `docs/README.md -> ../README.md` convenience link) lose those
  entries from the index. The link *targets* are normally tracked in their own
  right and still indexed, so the practical loss is a duplicate.
- Existing deployments must add the three datastore credentials to `.env` before
  the prod compose will start. This is intentional — a silent weak default is
  worse than a loud failure.
- `handoff.md` is now git-ignored: it carries local configuration in prose and was
  only kept out of commits by convention.

- **Not addressed, deliberately:**
  - **Parse timeouts.** tree-sitter 0.26 exposes neither `timeout_micros` nor a
    progress callback, so parsing cannot be cancelled in-process; a thread-based
    "timeout" would not stop the work. The per-file and whole-repo ceilings bound
    the input instead. A true bound needs process isolation — see below.
  - **Per-user LLM cost caps.** Rate limiting is request-count only, so a signed-in
    user can still drive meaningful spend within the limit. A token/cost budget is
    a product feature (accounting store + enforcement points), scoped separately;
    the repo ceilings above remove the largest *embedding*-cost vector.
  - The rate limiter still fails open on a Redis outage (ADR-0016) — an
    availability trade-off that a cost budget should sit behind, not replace.

- **The remaining structural change** is to sandbox ingestion (clone + scan +
  parse) in an unprivileged, network-restricted execution boundary with its own
  filesystem and hard resource limits. Every issue above at the ingestion boundary
  — symlink escape, stalls, resource exhaustion, and any future parser CVE — comes
  from one assumption: that a freshly cloned untrusted repo can be read as a
  trusted local filesystem by the worker. Isolating that stage collapses the class.

**Amends ADR-0021** (extends the untrusted-content posture from file *contents* to
tree *shape*), and **ADR-0009** (tenancy is now enforced at the storage boundary,
not only in application code). Relates to **ADR-0016**/**ADR-0023** (cookie
defaults) and **ADR-0018** (clone/fetch timeouts apply to incremental updates too).
