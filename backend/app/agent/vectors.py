"""The view-member index in Qdrant.

One point per (project, view, cube, member), mirroring the shape the oasys
non-hybrid flow uses: a member is only meaningful inside the view that exposes
it, so the view is part of its identity and a member shared by two views is two
points.

Two deliberate differences from that reference:

* every point carries `project_id`, with a keyword index on it, because one
  collection holds several projects here and the reference is single-tenant;
* re-indexing deletes this project's points rather than dropping the
  collection, which would take the other projects with it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.agent.embed import VECTOR_SIZE
from app.core.config import settings

log = logging.getLogger(__name__)

#: Same namespace as the reference indexer, so ids are derived the same way.
_NAMESPACE = uuid.NAMESPACE_DNS

_client: AsyncQdrantClient | None = None
_ready = False


class QdrantUnavailable(RuntimeError):
    """Qdrant could not be reached. Surfaced to the user as a 503."""


def client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=30,
        )
    return _client


async def close() -> None:
    global _client, _ready
    if _client is not None:
        await _client.close()
        _client = None
        _ready = False


def point_id(project_id: str, kind: str, view: str, cube: str, member: str) -> str:
    """Deterministic, so re-indexing upserts in place instead of duplicating."""
    return str(uuid.uuid5(_NAMESPACE, f"{project_id}.{kind}.{view}.{cube}.{member}"))


async def ensure_collection() -> None:
    """Create the collection and its project index once per process."""
    global _ready
    if _ready:
        return
    try:
        if not await client().collection_exists(settings.qdrant_collection):
            await client().create_collection(
                collection_name=settings.qdrant_collection,
                # Unnamed single dense vector, as in the reference collections.
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            log.info("created Qdrant collection %s", settings.qdrant_collection)
        # Without this the per-project filter is a full scan. Creating it twice
        # is harmless, so it is not conditional on having just created the
        # collection -- an older collection gets the index too.
        await client().create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="project_id",
            field_schema=PayloadSchemaType.KEYWORD,
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001 - every failure means "not reachable"
        raise QdrantUnavailable(str(exc)) from exc
    _ready = True


def _project_filter(project_id: str, **extra: Any) -> Filter:
    must: list[Any] = [
        FieldCondition(key="project_id", match=MatchValue(value=project_id))
    ]
    if view := extra.get("view"):
        must.append(FieldCondition(key="domain_view", match=MatchValue(value=view)))
    if kinds := extra.get("kinds"):
        must.append(FieldCondition(key="type", match=MatchAny(any=list(kinds))))
    return Filter(must=must)


async def replace_project(project_id: str, points: list[PointStruct]) -> int:
    """Swap this project's points for a new set.

    Deleting first is what removes members that a view no longer includes, and
    views that no longer exist. Scoped by filter so no other project is touched.
    """
    await ensure_collection()
    try:
        await client().delete(
            collection_name=settings.qdrant_collection,
            points_selector=_project_filter(project_id),
            wait=True,
        )
        if points:
            await client().upsert(
                collection_name=settings.qdrant_collection, points=points, wait=True
            )
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailable(str(exc)) from exc
    return len(points)


async def search(
    project_id: str,
    vector: list[float],
    *,
    limit: int = 10,
    view: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict[str, Any]]:
    await ensure_collection()
    try:
        found = await client().query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            query_filter=_project_filter(project_id, view=view, kinds=kinds),
            limit=limit,
            with_payload=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailable(str(exc)) from exc
    return [
        {"score": p.score, **(p.payload or {})} for p in found.points
    ]


async def count(project_id: str) -> int:
    await ensure_collection()
    try:
        result = await client().count(
            collection_name=settings.qdrant_collection,
            count_filter=_project_filter(project_id),
            exact=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailable(str(exc)) from exc
    return result.count


async def indexed_state(project_id: str) -> dict[str, dict[str, Any]]:
    """Every indexed point for this project: id -> its stored payload facts.

    Payload only, no vectors -- this is asked on every page load and the vectors
    are the expensive part to move.
    """
    await ensure_collection()
    state: dict[str, dict[str, Any]] = {}
    offset = None
    try:
        while True:
            points, offset = await client().scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=_project_filter(project_id),
                with_payload=["id", "hash", "indexed_at", "domain_view", "name"],
                with_vectors=False,
                limit=256,
                offset=offset,
            )
            for point in points:
                state[str(point.id)] = point.payload or {}
            if offset is None:
                break
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailable(str(exc)) from exc
    return state


async def delete_project(project_id: str) -> None:
    """Qdrant does not cascade with the Postgres row, so this is called by hand."""
    await ensure_collection()
    try:
        await client().delete(
            collection_name=settings.qdrant_collection,
            points_selector=_project_filter(project_id),
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise QdrantUnavailable(str(exc)) from exc
