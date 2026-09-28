"""Building the points for a project's views.

The stored view row holds member *names* only. Everything that makes a member
findable -- its title, its description, whether it is a measure -- lives in the
generated model, so the two are joined here.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from qdrant_client.models import PointStruct

from app.agent import vectors
from app.agent.embed import embed
from app.db import models_store, views_store
from app.generate.ir import Model

log = logging.getLogger(__name__)


def _blob(element: dict[str, str]) -> str:
    """The text that gets embedded.

    Six labelled lines, exactly as the reference indexer writes them. The labels
    themselves carry no signal -- they are identical in every vector -- but
    matching the reference means its retrieval behaviour carries over unchanged.
    """
    return (
        f"Domain View: {element['domain_view']}\n"
        f"Cube: {element['cube']}\n"
        f"Type: {element['type']}\n"
        f"Name: {element['name']}\n"
        f"Title: {element['title']}\n"
        f"Description: {element['description']}"
    )


def fingerprint(text: str) -> str:
    """What a point was built from.

    Stored on the point so staleness is a comparison rather than a guess: if the
    blob a view would produce today differs from the one that was embedded, that
    member is out of date, whatever the timestamps say.
    """
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _elements(views: list[dict[str, Any]], model: Model) -> list[dict[str, str]]:
    by_cube = {c.name: c for c in model.cubes}
    out: list[dict[str, str]] = []

    for view in views:
        for member in view.get("members") or []:
            cube = by_cube.get(member.get("cube", ""))
            if cube is None:
                # The view names a cube the current model no longer has. Skip it
                # rather than indexing a member nothing can query.
                log.info("view %s references unknown cube %s", view["name"], member.get("cube"))
                continue

            dimensions = {d.name: d for d in cube.dimensions}
            measures = {m.name: m for m in cube.measures}
            segments = {s.name: s for s in cube.segments}

            for name in member.get("includes") or []:
                if name in measures:
                    kind, found = "measure", measures[name]
                elif name in dimensions:
                    kind, found = "dimension", dimensions[name]
                elif name in segments:
                    kind, found = "segment", segments[name]
                else:
                    log.info("view %s includes unknown member %s", view["name"], name)
                    continue

                out.append(
                    {
                        "id": f"{kind}.{view['name']}.{cube.name}.{name}",
                        "domain_view": view["name"],
                        "cube": cube.name,
                        "type": kind,
                        "name": name,
                        "title": (found.title or "").strip(),
                        "description": (found.description or "").strip(),
                    }
                )
    return out


async def expected(project_id: str) -> dict[str, str]:
    """What the index *should* hold: point id -> fingerprint.

    Cheap on purpose. No embedding call, so the Views page can ask on every
    load whether the index is current.
    """
    views = await views_store.list_for(project_id)
    row = await models_store.latest(project_id, with_files=False)
    if row is None:
        return {}
    model = Model.model_validate(row["ir"])
    return {
        vectors.point_id(
            project_id, e["type"], e["domain_view"], e["cube"], e["name"]
        ): fingerprint(_blob(e))
        for e in _elements(views, model)
    }


async def build(project_id: str) -> tuple[list[PointStruct], dict[str, Any]]:
    """Embed every member of every view, and report what went in."""
    views = await views_store.list_for(project_id)
    row = await models_store.latest(project_id, with_files=False)
    if row is None:
        return [], {"views": 0, "members": 0, "undescribed": []}

    model = Model.model_validate(row["ir"])
    elements = _elements(views, model)
    if not elements:
        return [], {"views": len(views), "members": 0, "undescribed": []}

    texts = [_blob(e) for e in elements]
    embedded = await embed(texts)
    indexed_at = datetime.now(timezone.utc).isoformat()

    points = [
        PointStruct(
            id=vectors.point_id(
                project_id, e["type"], e["domain_view"], e["cube"], e["name"]
            ),
            vector=vector,
            payload={
                **e,
                "project_id": project_id,
                "text": text,
                "hash": fingerprint(text),
                "indexed_at": indexed_at,
            },
        )
        for e, text, vector in zip(elements, texts, embedded)
    ]

    # A member with no description is embedded as a bare name, which is close to
    # unfindable. Worth naming rather than leaving the user to wonder why a
    # search misses it.
    undescribed = [
        f"{e['domain_view']}.{e['name']}" for e in elements if not e["description"]
    ]
    return points, {
        "views": len(views),
        "members": len(elements),
        "undescribed": undescribed,
    }
