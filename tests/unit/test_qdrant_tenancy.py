"""Tenant isolation is enforced at the vector-store boundary (docs/adr/0024).

`fetch` and `delete` address points by id. Callers currently source those ids from
snapshot-scoped queries, so they are tenant-correct by construction — but that is an
application-code invariant, and the store must not depend on it. These tests pin the
boundary behaviour so a future retrieval channel cannot read or delete across repos.
"""

from typing import Any

from qdrant_client import models

from repo_assistant.indexing.qdrant_index import QdrantVectorIndex


class _Point:
    def __init__(self, pid: str, repo_id: str) -> None:
        self.id = pid
        self.payload: dict[str, Any] = {"repo_id": repo_id, "text": f"body of {pid}"}


class _StubClient:
    """Records calls and returns points from two different tenants."""

    def __init__(self, points: list[_Point]) -> None:
        self._points = points
        self.delete_calls: list[Any] = []

    async def retrieve(self, *, collection_name: str, ids: list[str], with_payload: bool):
        return [p for p in self._points if p.id in set(ids)]

    async def delete(self, *, collection_name: str, points_selector: Any) -> None:
        self.delete_calls.append(points_selector)


def _index(points: list[_Point]) -> tuple[QdrantVectorIndex, _StubClient]:
    client = _StubClient(points)
    return QdrantVectorIndex(client, collection="chunks"), client  # type: ignore[arg-type]


async def test_fetch_drops_points_belonging_to_another_repo() -> None:
    index, _ = _index([_Point("p1", "repo-a"), _Point("p2", "repo-b")])

    results = await index.fetch(repo_id="repo-a", ids=["p1", "p2"])

    assert [r.id for r in results] == ["p1"]


async def test_fetch_returns_nothing_when_every_id_is_foreign() -> None:
    index, _ = _index([_Point("p2", "repo-b")])

    assert await index.fetch(repo_id="repo-a", ids=["p2"]) == []


async def test_delete_is_constrained_to_the_tenant() -> None:
    index, client = _index([_Point("p1", "repo-a")])

    await index.delete(repo_id="repo-a", ids=["p1"])

    (selector,) = client.delete_calls
    conditions = selector.filter.must
    # Both the id set *and* the tenant must be part of the delete selector.
    assert any(isinstance(c, models.HasIdCondition) for c in conditions)
    tenant = next(c for c in conditions if isinstance(c, models.FieldCondition))
    assert tenant.key == "repo_id"
    assert isinstance(tenant.match, models.MatchValue)
    assert tenant.match.value == "repo-a"


async def test_empty_id_list_is_a_no_op() -> None:
    index, client = _index([_Point("p1", "repo-a")])

    assert await index.fetch(repo_id="repo-a", ids=[]) == []
    await index.delete(repo_id="repo-a", ids=[])
    assert client.delete_calls == []
