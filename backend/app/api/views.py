"""Views: the business surfaces the user assembles from generated cubes.

Cubes are generated; views are chosen. The model is deliberately not asked
which views should exist -- asked to guess, it returns one view per cube,
which is no view at all.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from app.db import edits_store, models_store, views_store
from app.generate import topology
from app.generate.ir import Model, View, ViewMember
from app.core.config import settings
from app.generate.render import render_cubes, render_view

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["views"])

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class MemberInput(BaseModel):
    cube: str = Field(description="Cube name, as generated")
    includes: list[str] = Field(default_factory=list)


class ViewInput(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    root_cube: str
    members: list[MemberInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip().lower().replace(" ", "_").replace("-", "_")
        if not _NAME.match(value):
            raise ValueError(
                "A view name must start with a letter and use only lowercase "
                "letters, numbers and underscores"
            )
        return value


async def _latest_model(project_id: str) -> tuple[Model, str]:
    """The stored model, with joins oriented for view traversal.

    Orientation is a deterministic transform, so it is applied on read rather
    than requiring a regeneration: a model generated before this existed still
    gets reachable join paths, and the cube files are re-rendered from the same
    transform when a view is saved, so what Cube compiles always agrees.
    """
    row = await models_store.latest(project_id, with_files=False)
    if row is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Generate a model before building views"
        )
    model = Model.model_validate(row["ir"])
    root_table, model.joins, _ = topology.orient(
        [c.table for c in model.cubes], model.joins
    )
    # The root has to come from the orientation pass. Re-deriving it afterwards
    # picks the wrong cube: once the hub declares its joins outward, its
    # in-degree is zero and every child looks equally central.
    by_table = {c.table: c.name for c in model.cubes}
    return model, by_table.get(root_table or "", "")


def _join_paths(model: Model, root_cube: str) -> dict[str, str | None]:
    """The dot path from `root_cube` to each cube, or None if unreachable.

    Cube resolves a member along a declared join path, so a cube with no path
    from the root cannot appear in that view at all. The screen needs to say
    which ones those are rather than offer them and fail at compile time.
    """
    by_table = {c.table: c.name for c in model.cubes}
    name_to_table = {c.name: c.table for c in model.cubes}
    root_table = name_to_table.get(root_cube)
    if root_table is None:
        return {}

    edges: dict[str, list[str]] = {}
    for join in model.joins:
        edges.setdefault(join.declaring_cube, []).append(join.target_cube)

    # Breadth-first, so each cube gets its shortest path from the root.
    paths: dict[str, str] = {root_table: root_cube}
    queue = [root_table]
    while queue:
        table = queue.pop(0)
        for target in edges.get(table, []):
            if target in paths or target not in by_table:
                continue
            paths[target] = f"{paths[table]}.{by_table[target]}"
            queue.append(target)

    return {c.name: paths.get(c.table) for c in model.cubes}


def _write_files(
    model: Model, saved_views: list[dict[str, Any]], edits: list[dict[str, Any]]
) -> list[str]:
    """Re-render every cube and view together, so the folder is self-consistent.

    Hand edits are laid back over the rendered cubes. Without that, saving a
    view rewrote every cube from the model and silently threw away anything
    the reviewer had changed on disk, while the edit record still claimed it.
    """
    files = dict(render_cubes(model))
    for edit in edits:
        if edit["path"] in files:
            files[edit["path"]] = edit["content"]
    for row in saved_views:
        row_paths = _join_paths(model, row["root_cube"])
        members = [
            ViewMember(join_path=row_paths[m["cube"]], includes=m["includes"])
            for m in row["members"]
            if m.get("includes") and row_paths.get(m["cube"])
        ]
        if not members:
            continue
        view = View(
            name=row["name"],
            title=row["title"],
            description=row["description"],
            cubes=members,
        )
        files[f"views/{row['name']}.yml"] = render_view(view)

    root = settings.cube_model_dir
    for existing in sorted(root.rglob("*.yml")):
        existing.unlink()
    written = []
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        written.append(relative)
    return sorted(written)


@router.get("/{project_id}/cubes")
async def available_cubes(project_id: str, root: str | None = None) -> dict:
    """Everything the view screen needs: cubes, their members, reachability."""
    model, suggested = await _latest_model(project_id)
    root_cube = root or suggested
    paths = _join_paths(model, root_cube)

    cubes = []
    for cube in model.cubes:
        cubes.append(
            {
                "name": cube.name,
                "title": cube.title,
                "table": cube.table,
                "description": cube.description,
                "join_path": paths.get(cube.name),
                "reachable": paths.get(cube.name) is not None,
                "dimensions": [
                    {"name": d.name, "title": d.title, "type": d.type}
                    for d in cube.dimensions
                    if d.public
                ],
                "measures": [
                    {"name": m.name, "title": m.title, "type": m.type}
                    for m in cube.measures
                ],
            }
        )

    # A member name is flattened into the view's namespace, so two cubes
    # offering the same name collide unless one is renamed.
    counts: dict[str, int] = {}
    for cube in cubes:
        for member in cube["dimensions"] + cube["measures"]:
            counts[member["name"]] = counts.get(member["name"], 0) + 1

    return {
        "root_cube": root_cube,
        "suggested_root": suggested,
        "cubes": sorted(cubes, key=lambda c: (not c["reachable"], c["name"])),
        "ambiguous_members": sorted(n for n, k in counts.items() if k > 1),
    }


@router.get("/{project_id}/views")
async def list_views(project_id: str) -> list[dict[str, Any]]:
    return await views_store.list_for(project_id)


def _to_ir(payload: ViewInput, paths: dict[str, str | None]) -> View:
    members = []
    for member in payload.members:
        if not member.includes:
            continue
        path = paths.get(member.cube)
        if path is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"{member.cube} cannot be reached from {payload.root_cube}",
            )
        members.append(ViewMember(join_path=path, includes=member.includes))
    return View(
        name=payload.name,
        title=payload.title or payload.name.replace("_", " ").title(),
        description=payload.description,
        cubes=members,
    )


@router.put("/{project_id}/views")
async def save_view(project_id: str, payload: ViewInput) -> dict[str, Any]:
    """Validate against the generated model, then store and render."""
    model, _ = await _latest_model(project_id)
    paths = _join_paths(model, payload.root_cube)
    if payload.root_cube not in paths:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"No cube named {payload.root_cube}"
        )

    known = {
        c.name: {d.name for d in c.dimensions} | {m.name for m in c.measures}
        for c in model.cubes
    }
    for member in payload.members:
        missing = [i for i in member.includes if i not in known.get(member.cube, set())]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"{member.cube} has no member named {', '.join(missing)}",
            )

    # A view flattens every member into one namespace, so the same name taken
    # from two cubes has no way to resolve. Cube reports this as a compile
    # error over the whole model, which takes the other cubes down with it, so
    # it is refused here instead. Note this is about what the view actually
    # selects: a name that several cubes happen to offer is fine as long as
    # only one of them contributes it.
    from_cubes: dict[str, list[str]] = {}
    for member in payload.members:
        for include in member.includes:
            from_cubes.setdefault(include, []).append(member.cube)
    collisions = {n: c for n, c in from_cubes.items() if len(c) > 1}
    if collisions:
        listed = "; ".join(
            f"`{name}` from {' and '.join(cubes)}" for name, cubes in sorted(collisions.items())
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{len(collisions)} member "
                f"{'name is' if len(collisions) == 1 else 'names are'} taken from more "
                f"than one cube: {listed}. A view flattens members into a single "
                "namespace, so drop one side or rename it in its cube first."
            ),
        )

    view = _to_ir(payload, paths)
    if not view.cubes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Select at least one member"
        )

    saved = await views_store.upsert(
        project_id,
        name=payload.name,
        title=view.title,
        description=payload.description,
        root_cube=payload.root_cube,
        members=[m.model_dump() for m in payload.members],
    )

    # The view's join paths only resolve if the cube files declare the joins in
    # that direction, so both are written from the one oriented model.
    written = _write_files(
        model,
        await views_store.list_for(project_id),
        await edits_store.list_for(project_id),
    )
    return {**saved, "yaml": render_view(view), "files_written": written}


@router.post("/{project_id}/views/preview")
async def preview_view(project_id: str, payload: ViewInput) -> dict[str, str]:
    """The YAML this view would render to, without saving it."""
    model, _ = await _latest_model(project_id)
    paths = _join_paths(model, payload.root_cube)
    return {"yaml": render_view(_to_ir(payload, paths))}


@router.delete("/{project_id}/views/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_view(project_id: str, name: str) -> Response:
    if not await views_store.delete(project_id, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such view")

    # Deleting the row is not enough: Cube compiles the folder, so a left-behind
    # views/<name>.yml keeps being served long after the view is gone. Rewrite
    # from what remains, which drops the orphan and leaves the rest untouched.
    try:
        model, _ = await _latest_model(project_id)
    except HTTPException:
        # No generated model to render from -- remove just this one file.
        stale = settings.cube_model_dir / "views" / f"{name}.yml"
        stale.unlink(missing_ok=True)
    else:
        _write_files(
            model,
            await views_store.list_for(project_id),
            await edits_store.list_for(project_id),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
