# ADR-0017: Minimal Next.js web UI

**Status:** Accepted (2026-07-12)

## Context

The last Phase 4 item is a browser UI so the product is more than a CLI: register
a repo, watch it index, and chat with grounded, clickable citations. It must talk
to the existing API (now authenticated, ADR-0016) and consume its two SSE streams
(job progress, chat). This ADR records the front-end decisions; the API is
unchanged except for CORS.

## Decision

- **Separate Next.js app in `web/`** (App Router, TypeScript, React 19), decoupled
  from the Python package — it's a client of the API, not part of the library.
  Deliberately **minimal dependencies**: `next`/`react` only, plain CSS with a
  light/dark theme, no UI kit or state library. The whole app is a handful of
  client components plus one typed API client.

- **SSE over `fetch`, not `EventSource`.** The API requires
  `Authorization: Bearer <key>`, and `EventSource` cannot set request headers.
  Both streams are consumed with `fetch` + a `ReadableStream` reader and a small
  `event:`/`data:` frame parser (`lib/api.ts#streamSse`). This is the standard
  workaround and keeps auth uniform (no key-in-URL, which would leak the secret
  into logs and history).

- **API key lives in the browser only.** Entered at a gate, stored in
  `localStorage`, sent as a bearer header. No cookies, so CORS runs without
  `allow_credentials`. "Sign out" clears it; a 401 from any call bounces back to
  the gate. The key is minted out-of-band with `ra apikey create` (ADR-0016).

- **Citations are GitHub deep-links** to the exact `path#Lstart-Lend` at the
  session's **pinned commit**, with the verified `cited_text` shown inline. The
  index stores chunk text, not whole files, so there is no file-content endpoint
  to power an in-app viewer — and linking to the pinned commit is honest,
  zero-cost, and lands the reader on the real source at the exact lines. A
  first-party file viewer (new content endpoint) is a later enhancement.

- **A fresh chat session per repo selection**, created via `POST /sessions` so the
  conversation is pinned to the repo's current snapshot (ADR-0015). The repo
  sidebar polls the list so indexing status transitions (`pending → ready`) show
  up, and the job-progress component streams stage/progress until the job settles.

- **CORS on the API** (`cors_allow_origins`, default `localhost:3000`) so the
  browser can call it and read streamed bodies; `Retry-After` is exposed for 429s.

## Alternatives considered

- **Server-rendered pages / API routes proxying the backend.** Would let the key
  live server-side (more secure than `localStorage`), but adds a Node tier to run
  and deploy for a single-user portfolio tool. Client-only keeps the UI a static
  bundle against the API. Revisit if multi-user or key-hiding matters.
- **`EventSource` for SSE.** Simpler API, but can't send `Authorization`; the only
  ways around that (key in query string, or a cookie) are worse than a `fetch`
  reader. Rejected.
- **A real in-app file viewer.** Nicer UX, but needs a new file/blob-content API
  and storage or on-demand git fetch — scope beyond "minimal". GitHub deep-links
  cover the need now.
- **Tailwind / a component library.** Faster styling, heavier toolchain and
  dependency surface than this small UI warrants. Plain CSS with variables does
  the job and stays theme-aware.

## Consequences

- End-to-end flow works in the browser: enter key → register/select a repo →
  watch SSE indexing progress → chat with streamed tokens and clickable, verified
  citations (validated live against a real indexed repo).
- The UI is a thin client — all logic stays in the API/library, so it can't drift
  from the measured core, and other clients can reuse the same endpoints.
- Security posture: the key sits in `localStorage` (XSS-exposed, standard for
  first-party dev tools) and is rate-limited server-side; a hardened deployment
  would move to a server-side session (noted above).
- `web/` has its own `npm` toolchain and is out of the Python CI; a
  build/typecheck check for it is a follow-up if the UI grows.
