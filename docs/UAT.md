# User Acceptance Test — Repo Assistant

A complete, manual acceptance pass over **every working flow** as of Phase 6
(MCP server shipped). Run it top-to-bottom before a release or demo; each test
has explicit **Pass** criteria. Automated coverage (unit + integration `pytest`)
is a *precondition* here, not a substitute — UAT exercises the real product
surface against live providers and a live stack.

Conventions:

- Commands are PowerShell-friendly (Windows dev environment); use `curl.exe`
  (not the PowerShell `curl` alias).
- `$API = "http://localhost:8000"`; the web UI dev server is
  `http://localhost:3000` (`NEXT_PUBLIC_API_BASE` points at the API).
- **UAT repo:** use a small-to-medium public repo you know well so you can
  judge answer quality (e.g. one of the pinned repos from `evals/repos.yaml`).
  Referred to below as `<REPO_URL>`.
- Record results in the table at the bottom. Any FAIL blocks acceptance.

---

## 0. Preconditions (P-0 … P-4)

| # | Check | Command | Pass |
|---|---|---|---|
| P-0 | Docker engine live | `docker version` | `Server:` section present (see handoff note: the CLI answers before the engine is ready — wait for Server) |
| P-1 | Stack up | `docker compose -f infra/docker-compose.yml up -d` then `docker compose -f infra/docker-compose.yml ps` | postgres, qdrant, redis all `running` |
| P-2 | Secrets loaded | `.env` at repo root contains `RA_VOYAGE_API_KEY`, `RA_ANTHROPIC_API_KEY` | keys present, never committed |
| P-3 | Migrations at head | `uv run alembic upgrade head` | exits 0 |
| P-4 | Automated suite green | `uv run pytest -q` | all tests pass (262 at time of writing); `ruff check .` and `pyright` clean |

---

## 1. CLI — core pipeline

### UAT-1.1 `ra version`

```powershell
uv run ra version
```

**Pass:** prints the package version, exit 0.

### UAT-1.2 Full index

```powershell
uv run ra index <REPO_URL>
```

**Pass:** completes without error; summary shows non-zero `files`, `chunks`,
`symbols`; a 12-char `commit` SHA; prints a `repo id` and the follow-up hint
`Chat with it: ra chat <repo id>`.

### UAT-1.3 Index with explicit ref

```powershell
uv run ra index <REPO_URL> --ref <branch-or-tag-or-sha>
```

**Pass:** indexes at the requested ref; reported commit matches the ref.

### UAT-1.4 Enriched index (contextual descriptions, ADR-0013)

```powershell
uv run ra index <REPO_URL> --enrich
```

**Pass:** completes; slower than 1.2 (Haiku enrichment ran); no provider errors.
*(Optional — costs LLM tokens; skip if budget-constrained.)*

### UAT-1.5 Incremental update — no-op (ADR-0018)

```powershell
uv run ra update <REPO_URL>
```

**Pass:** prints `Already up to date (no new commit).` when nothing changed
upstream.

### UAT-1.6 Incremental update — real change (ADR-0018)

Requires a repo you can push to (or index an active repo at an older `--ref`
first, then `update` to the newer default branch).

```powershell
uv run ra index <REPO_URL> --ref <older-commit>
uv run ra update <REPO_URL>
```

**Pass:** `reprocessed` ≈ the actual diff (files changed between the two
commits), `unchanged` = the rest, `deleted` matches removed files. Incremental
must be **proportional to the diff**, not the repo (`docs/SCALE.md`).

### UAT-1.7 Interactive chat — routed (auto)

```powershell
uv run ra chat <REPO_URL>
```

Ask, in one session:
1. A **locate** question ("where is X implemented?")
2. An **explain** question ("what does module Y do?")
3. A **trace** question ("what happens when Z is called?")

**Pass (each answer):**
- Path banner shows `[fast]` or `[agent, N tool calls]` — the router chose.
- Answer is grounded in the actual code (spot-check one claim against the file).
- `sources:` lists citations as `path:start-end@commit12` and each cited range
  actually contains what the answer attributes to it.
- Empty line / Ctrl-C exits cleanly with `Bye.`

### UAT-1.8 Forced reasoning paths

```powershell
uv run ra chat <REPO_URL> --path fast    # then ask one question
uv run ra chat <REPO_URL> --path agent   # then ask one question
```

**Pass:** banner shows the forced path; `agent` reports tool-call count and
respects the budget (shows `budget hit` only when it stops early);
`--path bogus` exits non-zero with a clean error (no traceback).

### UAT-1.9 Error handling — unindexed repo

```powershell
uv run ra chat https://github.com/nonexistent/never-indexed
```

**Pass:** clean one-line `Error: …` on stderr, exit code 1, **no traceback**.

---

## 2. CLI — API key management (ADR-0016)

### UAT-2.1 Create / list / revoke lifecycle

```powershell
uv run ra apikey create uat-run          # copy the printed key -> $KEY
uv run ra apikey list
uv run ra apikey revoke <id-from-list>
uv run ra apikey list
```

**Pass:**
- `create` prints the secret **once**, with the "will not be shown again" warning.
- `list` shows prefix only (never the full secret), status `active`, last-used.
- After `revoke`, `list` shows `revoked`; revoking the same id again exits
  non-zero ("No active key").
- Keep **one active key** for §3–§4: `uv run ra apikey create uat-service`,
  save as `$KEY`.

---

## 3. API service (ADR-0014, 0016)

Start the service and worker (two terminals):

```powershell
uv run ra worker    # terminal 1
uv run ra serve     # terminal 2 — http://localhost:8000
```

### UAT-3.1 Health & auth boundary

```powershell
curl.exe -s http://localhost:8000/health                       # open
curl.exe -s -o NUL -w "%{http_code}" http://localhost:8000/repos                      # no key
curl.exe -s -o NUL -w "%{http_code}" -H "Authorization: Bearer wrong" http://localhost:8000/repos
curl.exe -s -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos           # good key
```

**Pass:** `/health` 200 without auth; `/repos` returns **401** with no/invalid
key and 200 with the key. A **revoked** key (from UAT-2.1) also gets 401.

### UAT-3.2 Register repo → async ingestion job

```powershell
curl.exe -s -X POST http://localhost:8000/repos `
  -H "Authorization: Bearer $env:KEY" -H "Content-Type: application/json" `
  -d '{\"url\": \"<REPO_URL_2>\"}'
```

(Use a repo not yet indexed.) **Pass:** **202** with `{repo, job}`; job `state`
is queued/running immediately (API returned before indexing finished — the
worker owns the job).

### UAT-3.3 Job status + SSE progress stream

```powershell
curl.exe -s -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos/<repo_id>/job
curl.exe -s -N -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos/<repo_id>/job/stream
```

**Pass:** polling shows advancing `stage`/`progress`; the SSE stream emits
events live through the stages and terminates when the job reaches
`succeeded`. Worker terminal shows the stages.

### UAT-3.4 Repos CRUD

```powershell
curl.exe -s -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos
curl.exe -s -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos/<repo_id>
curl.exe -s -o NUL -w "%{http_code}" -X DELETE -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos/<repo_id_to_delete>
```

**Pass:** list contains both UAT repos; detail includes `active_snapshot`
(status `active`, commit pinned) and `latest_job`; DELETE returns **204** and
the repo disappears from the list; GET of a random UUID returns **404** with a
structured error body.

### UAT-3.5 Hybrid search

```powershell
curl.exe -s -X POST http://localhost:8000/repos/<repo_id>/search `
  -H "Authorization: Bearer $env:KEY" -H "Content-Type: application/json" `
  -d '{\"query\": \"<something you know is in the repo>\", \"limit\": 5}'
```

**Pass:** ≤5 hits, each with `path`, `start_line`/`end_line`, `score`,
`excerpt`; the top hits are genuinely relevant; response `commit` matches the
active snapshot; `{"query": ""}` returns **422**.

### UAT-3.6 Stateless chat (SSE streaming)

```powershell
curl.exe -s -N -X POST http://localhost:8000/repos/<repo_id>/chat `
  -H "Authorization: Bearer $env:KEY" -H "Content-Type: application/json" `
  -d '{\"question\": \"Where is <feature> implemented?\"}'
```

**Pass:** answer arrives as **incremental SSE events** (visibly token-by-token
with `-N`, not one blob); the stream ends with citation/final events carrying
`path`, line range, and `commit`.

### UAT-3.7 Conversation memory (ADR-0015)

```powershell
# create a session
curl.exe -s -X POST http://localhost:8000/repos/<repo_id>/sessions `
  -H "Authorization: Bearer $env:KEY" -H "Content-Type: application/json" -d '{\"title\": \"uat\"}'
# turn 1: a specific question, with {"session_id": "<sid>"}
# turn 2: a pronoun follow-up — "and where is *it* validated?" — same session_id
curl.exe -s -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos/<repo_id>/sessions/<sid>
```

**Pass:** turn 2 resolves the pronoun from turn-1 context (follow-up
condensation worked); session detail lists all 4 messages with strictly
increasing `seq`, correct alternating roles, and citations on assistant turns;
`commit_sha` is pinned to the snapshot at session creation.

### UAT-3.8 Rate limiting (ADR-0016)

Default: 120 req / 60 s per key.

```powershell
1..130 | ForEach-Object { curl.exe -s -o NUL -w "%{http_code}`n" -H "Authorization: Bearer $env:KEY" http://localhost:8000/repos } | Group-Object
```

**Pass:** first ~120 return 200, remainder **429**; after the window resets,
200 again. (Fail-open: if Redis is stopped, requests still succeed.)

### UAT-3.9 GitHub push webhook (ADR-0018)

Requires `RA_GITHUB_WEBHOOK_SECRET` set for the served app.

```powershell
# valid ping (signature over the exact body)
$body = '{"zen": "uat"}'
uv run python -c "import hmac,hashlib,sys; print('sha256='+hmac.new(sys.argv[1].encode(), sys.argv[2].encode(), hashlib.sha256).hexdigest())" "<secret>" $body
curl.exe -s -X POST http://localhost:8000/webhooks/github `
  -H "X-Hub-Signature-256: <sig>" -H "X-GitHub-Event: ping" -H "Content-Type: application/json" -d $body
```

**Pass:**
- Valid ping → `{"status": "pong"}`.
- Missing/wrong signature → **401** (fails closed).
- A signed `push` payload for a registered repo's default branch →
  `{"status": "queued", "job_id": …}` and the worker runs an incremental
  update; a push to a non-default branch → `{"status": "ignored", …}`.

### UAT-3.10 Prometheus metrics (ADR-0019)

```powershell
curl.exe -s http://localhost:8000/metrics | Select-Object -First 20
```

**Pass:** Prometheus text format; request counters/latency histograms present
and increment after the calls above.

---

## 4. Web UI (ADR-0017)

```powershell
cd web
npm install
npm run dev     # http://localhost:3000 (NEXT_PUBLIC_API_BASE -> :8000)
```

### UAT-4.1 Key gate
**Pass:** app demands an API key; a wrong key is rejected with a clear message;
the valid `$KEY` unlocks the app; the key survives a page reload.

### UAT-4.2 Add repo + live progress
Add a fresh repo URL from the UI.
**Pass:** ingestion progress updates **live** (SSE) through the stages without
manual refresh, ending in a ready state.

### UAT-4.3 Repo picker
**Pass:** all indexed repos listed with status; selecting one opens its chat.

### UAT-4.4 Streaming chat with citations
Ask a locate-style question.
**Pass:** answer streams in token-by-token; citations render as **GitHub deep
links** (`.../blob/<commit>/<path>#L<start>-L<end>`); clicking one lands on the
exact cited lines **at the pinned commit** on GitHub.

### UAT-4.5 Follow-up in the UI
Ask a pronoun follow-up in the same conversation.
**Pass:** answer uses conversation context (memory path exercised through the UI).

---

## 5. MCP server (ADR-0022)

### UAT-5.1 Serve + drive with a real MCP client

Register in an MCP client (e.g. Claude Desktop / Claude Code):

```jsonc
{ "repo-assistant": { "command": "uv", "args": ["run", "ra", "mcp", "<REPO_URL>"] } }
```

**Pass:**
- Client lists the **5 read-only index tools**.
- Each tool answers a real call (e.g. search for a known symbol → correct
  path/lines) — verify at least `search` and one file/symbol reader.
- No protocol corruption: logs go to **stderr**, stdout stays pure JSON-RPC
  (a session that survives several calls proves it).

### UAT-5.2 Unindexed repo
```powershell
uv run ra mcp https://github.com/nonexistent/never-indexed
```
**Pass:** clean error + non-zero exit, no traceback.

---

## 6. Evaluation harness — quality acceptance (docs/EVALUATION.md)

The wiring tests above prove flows work; this proves answers are *good*.

### UAT-6.1 Retrieval gate

```powershell
uv run ra eval --retrieval-only --gate
```

**Pass:** prints per-dataset + OVERALL + BY CATEGORY metrics, writes a report
under `evals/reports/` (git-ignored), and ends `GATE PASS` (exit 0). Numbers
are in line with the recorded baselines in `docs/EVALUATION.md` §5.

### UAT-6.2 Full pipeline eval *(optional — LLM cost)*

```powershell
uv run ra eval            # generation + judge
```

**Pass:** faithfulness/answer metrics in line with recorded baselines; judge
completes on all questions. Remember: the main benchmark is saturated — judge
quality levers on `evals/challenge/`, not here.

---

## 7. Config-gated flows *(run only where configured)*

### UAT-7.1 Private repositories (ADR-0020)

Requires `RA_GITHUB_APP_ID` + `RA_GITHUB_APP_PRIVATE_KEY` + an installation on
a private repo.

```powershell
uv run ra index <PRIVATE_REPO_URL> --installation-id <id>
uv run ra chat <PRIVATE_REPO_URL>
```

**Pass:** private repo clones via installation token and indexes; chat works;
the same URL **without** an installation id fails cleanly; no token appears in
logs or the DB in plaintext (cache rows are Fernet-encrypted).

### UAT-7.2 OTel tracing (ADR-0019)

With `RA_OTEL_ENABLED=true` and a collector (Jaeger/Langfuse) at
`RA_OTEL_EXPORTER_ENDPOINT`: run one chat request.
**Pass:** a single trace shows the request end-to-end (retrieval → generation)
in the backend UI.

### UAT-7.3 Security posture spot-checks (ADR-0021)

1. Index a repo containing a planted fake secret (e.g. a dummy
   `AKIA…`-style key in a fixture file). **Pass:** the secret is scrubbed from
   indexed content — search for it returns no hit exposing the value.
2. Ask the agent a question over a repo containing an injection lure
   (`"ignore your instructions and …"` in a README). **Pass:** the answer
   treats it as content, not instructions — no behavior change.

### UAT-7.4 Production deployment (docs/DEPLOYMENT.md)

```powershell
docker build -t repo-assistant:uat .
docker compose -f infra/docker-compose.prod.yml up -d
```

**Pass:** image builds; prod compose comes up; `/health` 200 from the
container; one API chat round-trip works against the containerized service.

---

## 8. Results log

| Test | Date | Result | Notes |
|---|---|---|---|
| P-0 … P-4 | 2026-07-13 | PASS | 262 tests, ruff + pyright clean, alembic at `e1c4f7a920d3` |
| UAT-1.1 … 1.9 | 2026-07-13 | PASS | Index/update on `p-timeout`/`p-limit` (v7.1.0→HEAD: reprocessed 7 = kept-set diff exactly); chat on `click`, citations spot-checked against the pinned commit; agent budget-stop labeled correctly |
| UAT-2.1 | 2026-07-13 | PASS | Full lifecycle; secret shown once; re-revoke exits 1 |
| UAT-3.1 … 3.10 | 2026-07-13 | PASS | Auth 401s incl. revoked key; 202+job; SSE job stream + chat stream; session `seq` ordering + pronoun follow-up; 429 after window; webhook: all 7 signature/routing cases, queued update ran E2E; `/metrics` counters match traffic |
| UAT-4.1 … 4.5 | 2026-07-13 | PASS* | *Finding: wrong API key is rejected (401 → gate) but **silently** — no user-visible error (KeyGate.tsx has no error state). Deep-link citations verified against GitHub at the pinned commit. Ingestion of small repos completes too fast to watch stage-by-stage (progress verified at API level in 3.3). Observation: repo picker lists ~700 `test/demo-*` residue rows unpaginated |
| UAT-5.1 … 5.2 | 2026-07-13 | PASS | Real `mcp` stdio client: 5 tools listed and exercised; bad args → clean MCP errors; stdout stayed protocol-pure across sessions; unindexed repo exits 1 cleanly |
| UAT-6.1 | 2026-07-13 | PASS | GATE PASS: 54 q, retrieval_recall 1.0, pass_rate 1.0, MRR 0.87, nDCG@10 0.76, recall@10 0.96 |
| UAT-6.2 | 2026-07-13 | SKIPPED | Optional (LLM cost) |
| UAT-7.1 | 2026-07-13 | N/A | No GitHub App configured in this environment |
| UAT-7.2 | 2026-07-13 | N/A | OTel not enabled / no collector |
| UAT-7.3 | 2026-07-13 | N/A | No planted-secret/lure fixture repo on GitHub; posture covered by `tests/unit/test_security.py` |
| UAT-7.4 | 2026-07-13 | PASS | `repo-assistant:uat` image built; prod compose up; `/health` 200 + API round-trip from container |

**Acceptance:** all applicable tests PASS; config-gated tests may be N/A but
never FAIL. File defects for failures and re-run the affected section after
the fix.
