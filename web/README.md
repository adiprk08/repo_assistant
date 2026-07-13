# Repo Assistant — web UI

A minimal Next.js (App Router) chat UI over the Repo Assistant API: sign in with
GitHub, register a repo, watch indexing progress, and chat with grounded,
clickable citations. See [docs/adr/0017](../docs/adr/0017-web-ui.md) (UI) and
[docs/adr/0023](../docs/adr/0023-web-auth-and-user-accounts.md) (auth) for the
design decisions.

## Prerequisites

The API and worker must be running, and GitHub OAuth must be configured so users
can sign in. Register a GitHub OAuth App (Settings → Developer settings → OAuth
Apps) with **Authorization callback URL** `http://localhost:3000/api/auth/github/callback`,
then set its client id/secret in the repo-root `.env`:

```bash
# from the repo root
docker compose -f infra/docker-compose.yml up -d
uv run alembic upgrade head
# in .env: RA_GITHUB_OAUTH_CLIENT_ID=... and RA_GITHUB_OAUTH_CLIENT_SECRET=...
uv run ra serve            # API on :8000
uv run ra worker           # ingestion worker (separate terminal)
```

## Run

```bash
cd web
cp .env.local.example .env.local   # API_ORIGIN — where Next proxies /api/*
npm install
npm run dev                        # http://localhost:3000
```

Click **Sign in with GitHub**. Register a repo, wait for indexing, then chat.
Citations deep-link to the exact lines on GitHub at the indexed commit. Your
library and conversations are private to your account.

## Notes

- Auth is **cookie-based** (docs/adr/0023): the browser only talks to this app's
  own origin, and Next proxies `/api/*` to the API server-side (`API_ORIGIN`), so
  the session cookie is first-party. No API key or `localStorage` token.
- SSE (job progress + chat) is consumed via `fetch` + a stream reader, not
  `EventSource`.
- API keys still exist as personal access tokens for CLI/MCP use
  (`ra apikey create`) — they authenticate the same API under the `local` user.
