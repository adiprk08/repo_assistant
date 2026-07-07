# ADR-0008: arq + Redis with staged, checkpointed ingestion

**Status:** Accepted (2026-07-07)

## Context

Ingestion is a minutes-long, multi-stage pipeline (clone → scan → parse → chunk → embed → index → enrich) that must survive process restarts, report progress to the UI, run concurrently across repos, and never leave a snapshot half-visible.

## Decision

- **arq** workers over **Redis** for job execution — async-native (matches the FastAPI/httpx stack), minimal, reliable enough for this workload. Redis also serves as the cache and rate-limit store, so it pays for itself twice.
- **Stage-per-task:** each pipeline stage is an idempotent arq task; a `jobs` row in Postgres records the state machine (stage, progress counters, checkpoints, error). Retried stages resume from the checkpoint (e.g. embed picks up at the last committed file batch), guaranteed safe by content-hash upserts.
- **Progress:** API reads the `jobs` row; UI subscribes via SSE.
- **Atomic visibility:** all writes target the new snapshot; the repo's active snapshot pointer flips only when the final stage commits (ADR-0009).

## Alternatives considered

- **Celery** — the default answer, but sync-first, configuration-heavy, and its canvas features exceed our needs.
- **Dramatiq / RQ** — solid, sync-oriented; would force a thread bridge around our async pipeline code.
- **procrastinate (Postgres-backed)** — appealing "one less service", but we want Redis anyway for caching/rate limits, and separating queue traffic from the OLTP database is cleaner.
- **FastAPI BackgroundTasks** — not durable; dies with the process.
- **Temporal** — genuinely great for complex workflows; operational overkill here. Recorded as the escalation path if orchestration complexity grows (e.g. multi-repo scheduled sync fleets).

## Consequences

- One small extra service (Redis) with three uses; worker horizontal scaling is `docker compose scale worker=N`.
- Idempotency is a hard requirement on every stage — enforced by design review and integration tests that kill/resume workers mid-stage.
