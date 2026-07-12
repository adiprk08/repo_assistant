# ADR-0018: Incremental indexing and webhook re-index

**Status:** Accepted (2026-07-12)

## Context

Re-indexing a repo from scratch on every push is wasteful: it re-scans, re-parses,
re-chunks, and re-upserts every file even when one changed. The embedding cache
(ADR-0003) already removes the *dollar* cost of unchanged content, but not the
*work* — parse/chunk/Qdrant writes still scale with the whole repo. Phase 5 needs
updates whose work scales with the diff, triggered automatically on push, while
keeping the atomic snapshot guarantees of ADR-0009.

## Decision

- **Diff by content hash, not `git diff`.** The scanner already records a
  `sha256` per selected file. An update scans the new working tree and compares
  each file's hash against the previous active snapshot's `files` rows:
  - unchanged: hash present and equal
  - changed/added: hash absent or different → **reprocess**
  - deleted: previous path absent from the new scan → drop
  This is simpler and more robust than parsing `git diff` (it reflects the actual
  indexing filter, needs no second commit in the clone, and treats a rename as
  delete+add — whose re-embed is a cache hit anyway). Cross-commit git diffs and
  true rename tracking are an optimization we deliberately skip.

- **New snapshot per update, copy-forward unchanged (ADR-0009 preserved).** Each
  update builds a fresh snapshot at the new commit and promotes it atomically;
  the old snapshot stays queryable until the pointer flips. Unchanged files'
  `files`/`symbols`/`edges`/`chunks` rows are copied to the new snapshot via SQL
  (new ids, same content), and their Qdrant points are copied server-side from the
  old point ids to the new ones (`copy_points`, retrieve-with-vectors → re-upsert)
  with the payload `commit` patched. Only changed/added files are re-scanned,
  parsed, chunked, and embedded. So parse/chunk/embed scale with the diff; the
  point copy is a cheap Qdrant-side transfer with no re-embedding.

- **Point ids stay snapshot-scoped** (`uuid5(snapshot_id, path, index)`, equal to
  the `chunks.id`). Copying forward re-derives the new ids deterministically and
  maps old→new, so the "one point id per chunk row" invariant holds and citations
  carry the correct commit. (Sharing points across snapshots was rejected — the
  payload `commit`/citation would be ambiguous.)

- **Edges: copy unchanged, recompute changed.** Edges for files that didn't change
  are copied; edges are recomputed from the changed files' symbol contexts. Some
  cross-boundary heuristic edges (changed→unchanged) may be missed; edges are
  opt-in and confidence-scored (ADR-0011), so this is acceptable and noted.

- **No-op fast path.** If the new commit equals the active snapshot's commit, the
  update returns without creating a snapshot.

- **Triggers.** Manual: `ra update <repo>` (inline, like `ra index`) and an
  enqueued `update` job run by the arq worker. Automatic: `POST /webhooks/github`
  verifies the GitHub `X-Hub-Signature-256` HMAC against `github_webhook_secret`
  and, on a `push` to a **registered** repo's default ref, enqueues an update job.
  The webhook is **unauthenticated but signature-gated** (GitHub can't send a
  bearer key), so it is mounted outside the `secured` dependency and does its own
  constant-time HMAC check. Polling is a later trigger; webhooks + manual cover
  the automation now.

## Alternatives considered

- **Mutate the active snapshot in place** (delete changed points, add new). Truly
  diff-proportional in Qdrant too, but breaks atomic promotion — a query could see
  a half-updated snapshot — and muddies commit pinning. Rejected for correctness.
- **`git diff A..B` for the file plan.** Needs both commits (and blobs) in the
  clone and a rename heuristic; content-hash comparison is equivalent for our
  purposes and simpler. Kept as a possible future optimization for huge trees.
- **Full re-index relying only on the embedding cache.** Zero new code and no
  dollar cost, but still O(repo) parse/chunk/Qdrant work per push — fails the
  "touches only the diff" goal for large repos.
- **Webhook behind API-key auth.** GitHub can't present our bearer key; HMAC is
  the standard and lets us verify the payload's integrity too.

## Consequences

- Update work scales with the diff for the expensive stages (parse/chunk/embed);
  unchanged content is copied, never re-embedded. A one-file change re-embeds one
  file's chunks and copies the rest.
- Old snapshots accumulate as harmless orphans (as today); snapshot GC is a
  separate hardening task.
- The webhook needs `github_webhook_secret` set and the GitHub webhook pointed at
  the endpoint; without the secret the endpoint rejects everything (fail-closed —
  the opposite of the rate limiter, because here a bad signature is an attack, not
  an availability blip).
- Requires a `copy_points` capability on the vector index (added to the interface,
  implemented for Qdrant and the in-memory fake).
