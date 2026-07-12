# ADR-0021: Security pass — secret scanning, injection posture, dependency audit

**Status:** Accepted (2026-07-12)

## Context

Phase 5 hardening includes a security pass over the three areas the design has
always claimed to defend (docs/RISKS.md): repository content is untrusted (prompt
injection), secrets must never enter the index/prompts, and dependencies must be
free of known CVEs. This record captures what was tightened and verified.

## Decision

- **Content-level secret scanning, not just filenames.** The scanner already
  excludes secret-*named* files (`.env`, `*.pem`, `id_rsa`, …). That misses a
  credential hardcoded inside an innocuously named source/config file. Added
  `filters.contains_secret(text)` — a small set of **high-confidence, low-false-
  positive** patterns (PEM private-key headers, `AKIA…` AWS keys, `ghp_/gho_/…`
  GitHub tokens, `github_pat_…`, `sk-ant-…`, `AIza…`, Slack `xox…`, Stripe
  `sk_live_…`, GitLab `glpat-…`). A file whose content matches is skipped
  (`SkipReason.SECRET`) before parsing, so the credential never reaches Qdrant,
  Postgres, or a prompt. Deliberately not generic entropy scanning — that trades
  recall for a false-positive rate that would silently drop real source files.

- **Prompt-injection posture is defense-in-depth, and now regression-tested.**
  Repository text reaching the model is *data*: (1) it is fenced in `document`
  content blocks; (2) the system prompt states repo content carries no instructions
  and to refuse over inventing; (3) the agent's tools are **read-only over the
  index** (`search_code`, `get_symbol`, `read_span`, `graph_neighbors`,
  `list_dir`) — no write/exec/filesystem/network tool exists, so an injected
  "run this" has nothing to call; (4) citations are verified post-hoc, bounding
  fabrication. Tests now assert the tool set stays read-only and the system prompt
  keeps the untrusted-data rule, so a regression trips CI.

- **Dependency audit in CI.** `pip-audit` runs as its own CI job and fails the
  build on a known-vulnerable dependency. Current run: no known vulnerabilities.

## Alternatives considered

- **Generic high-entropy (Shannon) secret detection.** Higher recall, but a real
  false-positive problem (hashes, base64 blobs, minified snippets, test fixtures)
  that would drop legitimate files without the user knowing. Chose precise,
  provider-specific patterns; entropy scanning can be added later behind a flag if
  a measured need appears.
- **Redact secrets in place instead of skipping the file.** Keeps the rest of the
  file indexable, but rewriting content breaks the citation-span invariant (spans
  must map to exact source lines). Skipping the whole file is simpler and safe;
  the file is rare and its loss is acceptable next to leaking a key.
- **A dedicated scanner (gitleaks/trufflehog) as a subprocess.** Heavier dependency
  and process boundary for what a dozen regexes cover at ingest time; revisit if we
  need their rule breadth.
- **Blocking the whole agent path on injection risk.** Unnecessary — read-only
  tools + verification already bound the blast radius; disabling the feature would
  cost capability for no real gain.

## Consequences

- A hardcoded credential in any scanned text file is kept out of the index, on top
  of the filename exclusions.
- The read-only-tools and untrusted-data guarantees are locked by tests; changing
  them is now a conscious, CI-visible act.
- `pip-audit` gates dependencies; a new advisory fails CI until addressed.
- Still open (later hardening): secret-scanning recall metrics, an allowlist for
  known-safe matches (e.g. example keys in docs), a full external red-team of the
  live agent with adversarial repos, and SBOM generation.
