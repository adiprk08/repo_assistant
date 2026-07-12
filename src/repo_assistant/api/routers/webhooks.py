"""GitHub push webhook -> incremental re-index (docs/adr/0018).

Unauthenticated but **signature-gated**: GitHub can't present our API key, so this
route is mounted outside the `secured` dependency and instead verifies the
`X-Hub-Signature-256` HMAC against ``github_webhook_secret``. It fails closed —
no secret, or a bad signature, rejects the request.
"""

import hashlib
import hmac

from fastapi import APIRouter, Request

from repo_assistant.core.errors import AuthenticationError
from repo_assistant.core.logging import get_logger
from repo_assistant.ingestion.git import normalize_github_url
from repo_assistant.storage import repositories as repo

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)


def _valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


@router.post("/github")
async def github_webhook(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    body = await request.body()

    secret = settings.github_webhook_secret
    if not secret or not _valid_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
        raise AuthenticationError("Invalid or missing webhook signature.")

    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"status": "pong"}
    if event != "push":
        return {"status": "ignored", "reason": f"event {event!r} is not handled"}

    payload = await request.json()
    ref = payload.get("ref", "")  # e.g. "refs/heads/main"
    repo_url = (payload.get("repository") or {}).get("clone_url") or (
        payload.get("repository") or {}
    ).get("html_url")
    if not repo_url:
        return {"status": "ignored", "reason": "no repository url in payload"}

    try:
        url = normalize_github_url(repo_url)
    except Exception:  # noqa: BLE001 - a payload we can't map to a GitHub repo
        return {"status": "ignored", "reason": "unrecognized repository url"}

    runtime = request.app.state.runtime
    async with runtime.session_factory() as session:
        repo_row = await repo.get_repo_by_url(session, url)
        if repo_row is None:
            return {"status": "ignored", "reason": "repository not registered"}
        # Only re-index a push to the repo's tracked default branch.
        if ref != f"refs/heads/{repo_row.default_ref}":
            return {"status": "ignored", "reason": f"push to {ref} is not the default ref"}
        job = await repo.create_job(session, repo_row.id, job_type="update", params={"url": url})
        await session.commit()
        job_id = job.id

    await request.app.state.queue.enqueue_update(job_id)
    logger.info("webhook queued update", repo=url, job_id=str(job_id))
    return {"status": "queued", "job_id": str(job_id)}
