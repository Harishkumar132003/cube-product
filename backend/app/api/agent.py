"""Retrieval over a project's views.

Cubes are generated, views are chosen, and a view is the only public surface --
so a view's members are what a question has to be matched against. This indexes
them and searches them; turning a match into a Cube query is a later step.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.agent import flow as flow_mod
from app.agent import index as index_mod
from app.agent import vectors
from app.agent.embed import embed
from app.core.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["agent"])


class SearchRequest(BaseModel):
    question: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    # Narrow to one view, or to measures only, the way the reference pipeline
    # does once an LLM has chosen them.
    view: str | None = None
    kinds: list[str] | None = None


def _require_configured() -> None:
    if not settings.qdrant_configured:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Retrieval needs CUBEGEN_OPENAI_API_KEY and CUBEGEN_QDRANT_URL",
        )


def _unavailable(exc: vectors.QdrantUnavailable) -> HTTPException:
    """Qdrant being down is an environment problem, not a bad request."""
    log.warning("qdrant unavailable: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"Could not reach Qdrant at {settings.qdrant_url}. "
            "Start it with `docker start qdrant` and try again."
        ),
    )


@router.get("/{project_id}/views/index")
async def index_status(project_id: str) -> dict[str, Any]:
    """Whether the index matches the views as they stand right now.

    Compared by fingerprint rather than by timestamp: editing a view, renaming a
    measure or regenerating the model all change what a member would embed to,
    and none of them reliably move a clock the index can see.
    """
    if not settings.qdrant_configured:
        return {"configured": False, "reachable": False, "count": 0, "stale": False}

    try:
        state = await vectors.indexed_state(project_id)
    except vectors.QdrantUnavailable:
        return {
            "configured": True,
            "reachable": False,
            "count": 0,
            "stale": False,
            "collection": settings.qdrant_collection,
        }

    wanted = await index_mod.expected(project_id)
    indexed = {pid: (payload.get("hash") or "") for pid, payload in state.items()}

    added = [p for p in wanted if p not in indexed]
    removed = [p for p in indexed if p not in wanted]
    changed = [p for p, h in wanted.items() if p in indexed and indexed[p] != h]

    def label(point_id: str) -> str:
        payload = state.get(point_id, {})
        view, name = payload.get("domain_view"), payload.get("name")
        return f"{view}.{name}" if view and name else point_id

    timestamps = [p.get("indexed_at") for p in state.values() if p.get("indexed_at")]

    return {
        "configured": True,
        "reachable": True,
        "count": len(indexed),
        "expected": len(wanted),
        "stale": bool(added or removed or changed),
        "added": len(added),
        "removed": [label(p) for p in removed][:10],
        "changed": [label(p) for p in changed][:10],
        "indexed_at": max(timestamps) if timestamps else None,
        "collection": settings.qdrant_collection,
    }


@router.post("/{project_id}/views/index")
async def rebuild_index(project_id: str) -> dict[str, Any]:
    """Re-index every view in this project.

    A full rebuild rather than a diff: the member list of a view changes on
    every edit, and at this size embedding them all costs less than working out
    which ones moved.
    """
    _require_configured()
    points, report = await index_mod.build(project_id)
    try:
        written = await vectors.replace_project(project_id, points)
    except vectors.QdrantUnavailable as exc:
        raise _unavailable(exc) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        log.exception("indexing failed for project %s", project_id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Indexing failed: {exc}"
        ) from exc

    return {
        "indexed": written,
        "collection": settings.qdrant_collection,
        **report,
    }


@router.post("/{project_id}/views/search")
async def search_views(project_id: str, payload: SearchRequest) -> dict[str, Any]:
    """Rank a project's view members against a question."""
    _require_configured()
    try:
        vector = (await embed([payload.question]))[0]
    except Exception as exc:  # noqa: BLE001
        log.exception("embedding the question failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Could not embed the question: {exc}"
        ) from exc

    try:
        hits = await vectors.search(
            project_id,
            vector,
            limit=payload.limit,
            view=payload.view,
            kinds=payload.kinds,
        )
    except vectors.QdrantUnavailable as exc:
        raise _unavailable(exc) from exc

    # Grouped by view, because a member is only queryable through the view that
    # exposes it -- a flat list of members would hide which ones can be asked
    # for together.
    by_view: dict[str, dict[str, Any]] = {}
    for hit in hits:
        view = hit.get("domain_view", "")
        entry = by_view.setdefault(view, {"view": view, "score": 0.0, "members": []})
        entry["score"] = max(entry["score"], hit.get("score", 0.0))
        entry["members"].append(
            {
                "name": hit.get("name"),
                "cube": hit.get("cube"),
                "type": hit.get("type"),
                "title": hit.get("title"),
                "description": hit.get("description"),
                "score": hit.get("score"),
            }
        )

    return {
        "question": payload.question,
        "views": sorted(by_view.values(), key=lambda v: -v["score"]),
        "total": len(hits),
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Groups the turns of one conversation into a Langfuse session. Nothing is
    # stored server-side; the client decides what counts as a conversation.
    session_id: str | None = None


@router.post("/{project_id}/ask")
async def ask(project_id: str, payload: AskRequest) -> dict[str, Any]:
    """Answer a question from this project's model.

    Runs inline. Nothing is stored -- no chat history yet -- so the response
    carries every step it took, which is also what makes a wrong answer
    debuggable.
    """
    _require_configured()
    try:
        return await flow_mod.answer(
            project_id, payload.question.strip(), session_id=payload.session_id
        )
    except vectors.QdrantUnavailable as exc:
        raise _unavailable(exc) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        log.exception("answering failed for project %s", project_id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Could not answer that: {exc}"
        ) from exc
