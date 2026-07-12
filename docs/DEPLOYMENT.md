# Deployment

A single-VM deployment of Repo Assistant: Postgres + Qdrant + Redis, the FastAPI
API, and the arq worker — all via Docker Compose. The image is built and published
to GHCR by CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)); you can
also build it locally.

## Image

`Dockerfile` builds one image that runs either process:

- API: `ra serve --host 0.0.0.0 --port 8000` (the default `CMD`)
- Worker: `ra worker`
- Migrations: `alembic upgrade head`

```bash
docker build -t repo-assistant:local .
```

CI publishes `ghcr.io/adiprk08/repo_assistant:latest` (and a `sha-…` tag) on every
push to `main`.

## Single-VM stack

`infra/docker-compose.prod.yml` runs storage + `migrate` (one-shot) + `api` +
`worker`, wired by service name. Provide secrets in an `.env` file at the repo root:

```dotenv
RA_ANTHROPIC_API_KEY=sk-ant-...
RA_VOYAGE_API_KEY=pa-...
# Optional — private repos (docs/adr/0020) and webhooks (docs/adr/0018):
RA_TOKEN_ENCRYPTION_KEY=...        # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
RA_GITHUB_APP_ID=...
RA_GITHUB_APP_PRIVATE_KEY=...      # PEM contents
RA_GITHUB_WEBHOOK_SECRET=...
```

The compose file overrides the storage DSNs to the in-network service names
(`postgres`, `qdrant`, `redis`), so the same `.env` works for local CLI use
(localhost) and the deployed stack.

```bash
# Pull-based (uses the CI-published image):
docker compose -f infra/docker-compose.prod.yml up -d

# Or build locally and run:
RA_IMAGE=repo-assistant:local docker compose -f infra/docker-compose.prod.yml up -d --build
```

`migrate` runs `alembic upgrade head` and must complete before `api`/`worker`
start (enforced via `depends_on: service_completed_successfully`).

## First run

```bash
# Mint an API key (the UI and API clients need it):
docker compose -f infra/docker-compose.prod.yml exec api ra apikey create web

# Health check:
curl localhost:8000/health

# Scale the worker horizontally:
docker compose -f infra/docker-compose.prod.yml up -d --scale worker=3
```

## Observability

Set `RA_OTEL_ENABLED=true` and `RA_OTEL_EXPORTER_ENDPOINT=http://<collector>:4318`
to export traces (OTLP/HTTP — Jaeger, Tempo, or Langfuse; [ADR-0019](adr/0019-observability.md)).
Prometheus scrapes `GET /metrics` on the API. Logs are JSON (`RA_LOG_FORMAT=json`).

## Notes / boundaries

- This is a **single-VM** topology (one Postgres, one Qdrant, one Redis). Managed
  datastores + multiple API/worker replicas behind a load balancer are the next
  step; the app processes are stateless, so replicating `api`/`worker` is just a
  higher `--scale`.
- The web UI (`web/`) is deployed separately (any static/Next.js host) pointing
  `NEXT_PUBLIC_API_BASE` at the API; it is intentionally not in this compose file.
- TLS termination (a reverse proxy such as Caddy/nginx in front of `api`) is left
  to the operator.
