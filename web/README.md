# Repo Assistant — web UI

A minimal Next.js (App Router) chat UI over the Repo Assistant API: register a
repo, watch indexing progress, and chat with grounded, clickable citations. See
[docs/adr/0017](../docs/adr/0017-web-ui.md) for the design decisions.

## Prerequisites

The API and worker must be running, and you need an API key:

```bash
# from the repo root
docker compose -f infra/docker-compose.yml up -d
uv run alembic upgrade head
uv run ra serve            # API on :8000
uv run ra worker           # ingestion worker (separate terminal)
uv run ra apikey create web   # copy the printed key
```

## Run

```bash
cd web
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE at the API
npm install
npm run dev                        # http://localhost:3000
```

Paste the API key when prompted (stored in `localStorage` only). Register a repo,
wait for indexing, then chat. Citations deep-link to the exact lines on GitHub at
the indexed commit.

## Notes

- SSE (job progress + chat) is consumed via `fetch` + a stream reader, not
  `EventSource`, because the API requires an `Authorization` header that
  `EventSource` cannot send.
- The API's `RA_CORS_ALLOW_ORIGINS` must include this app's origin
  (default `http://localhost:3000`).
