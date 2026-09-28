"""Editing generated model files.

Generation produces a first draft; the reviewer corrects it. An edit is checked
before it reaches disk, because Cube compiles the folder and a broken file
takes the whole model down rather than just itself.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

import difflib
import re
from typing import Literal

from app import bundle as bundle_mod
from app.core.config import settings
from app.db import edits_store, models_store, scans, views_store
from app.generate import sync
from app.generate.ir import Model
from app.generate.validate import FileProblem, check

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["model files"])


class FileEdit(BaseModel):
    path: str
    content: str


def _target(path: str):
    """Resolve inside the model directory, refusing anything that escapes it."""
    root = settings.cube_model_dir.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not path.endswith(".yml"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Not a model file")
    return target


async def _propagate(
    project_id: str, row: dict, path: str, content: str
) -> dict[str, Any]:
    """Update the stored model, and every saved view, to match this edit."""
    parsed = sync.parse_cube(content)
    if parsed is None:
        return {}

    model = Model.model_validate(row["ir"])
    before = next((c for c in model.cubes if c.name == parsed.name), None)
    if before is None:
        # A new cube name means the file was renamed in place; the validator
        # already refuses that, so there is nothing to reconcile.
        return {}

    changes = sync.diff_members(before, parsed)
    await models_store.update_ir(project_id, sync.apply_cube(model, parsed).model_dump())

    carried_all: list[str] = []
    dropped_all: list[str] = []
    for view in await views_store.list_for(project_id):
        members, carried, dropped = sync.remap_view_members(
            view["members"], parsed.name, changes
        )
        if not carried and not dropped:
            continue
        await views_store.upsert(
            project_id,
            name=view["name"],
            title=view["title"],
            description=view["description"],
            root_cube=view["root_cube"],
            members=members,
        )
        carried_all += [f"{view['name']}: {c}" for c in carried]
        dropped_all += [f"{view['name']}.{d}" for d in dropped]

    # Always, not only when something was renamed: the view files are rendered
    # from the model, so any cube edit can invalidate them, and Cube compiles a
    # view against a missing member by failing the whole folder rather than
    # that view. Re-rendering is a handful of small files.
    rerendered = _rerender_views(
        sync.apply_cube(model, parsed), await views_store.list_for(project_id)
    )

    return {
        "renamed": changes["renamed"],
        "carried": carried_all,
        "dropped": dropped_all,
        "views_rewritten": rerendered,
    }


def _rerender_views(model: Model, views: list[dict[str, Any]]) -> list[str]:
    """Write each saved view out again against the current model."""
    from app.api.views import _join_paths  # local: the API layer owns path resolution
    from app.generate.ir import View, ViewMember
    from app.generate.render import render_view

    written: list[str] = []
    root = settings.cube_model_dir / "views"
    for view in views:
        paths = _join_paths(model, view["root_cube"])
        members = [
            ViewMember(join_path=paths[m["cube"]], includes=m["includes"])
            for m in view["members"]
            if m.get("includes") and paths.get(m["cube"])
        ]
        if not members:
            continue
        body = render_view(
            View(
                name=view["name"],
                title=view["title"],
                description=view["description"],
                cubes=members,
            )
        )
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{view['name']}.yml").write_text(body)
        written.append(f"views/{view['name']}.yml")
    return written


@router.get("/{project_id}/model/edits")
async def list_edits(project_id: str) -> dict[str, str]:
    """Edited paths mapped to their content, so the UI can mark them."""
    return {e["path"]: e["content"] for e in await edits_store.list_for(project_id)}


@router.put("/{project_id}/model/files")
async def save_file(project_id: str, payload: FileEdit) -> dict:
    """Validate, write to disk, and remember the edit."""
    try:
        check(payload.path, payload.content)
    except FileProblem as problem:
        # 422 rather than 400: the request was well formed, its contents are not.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem.as_dict()
        ) from problem

    row = await models_store.latest(project_id, with_files=True)
    generated = (row or {}).get("files", {}).get(payload.path, "")

    # A cube file is also the model. Feed the edit back into the IR, and carry
    # any saved view across a rename -- Cube compiles the folder as one unit, so
    # a view left pointing at a renamed member takes every cube down with it.
    propagated: dict[str, Any] = {}
    if row is not None and payload.path.startswith("cubes/"):
        propagated = await _propagate(project_id, row, payload.path, payload.content)

    target = _target(payload.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content)

    # Changing a file back by hand makes it the original again. Keeping a row
    # for it would mark the file edited when nothing differs, and a later
    # regeneration would pin a stale copy of the generator's own output.
    if generated and payload.content == generated:
        await edits_store.discard(project_id, payload.path)
        return {"path": payload.path, "edited": False, "has_original": True}

    saved = await edits_store.save(project_id, payload.path, payload.content, generated)
    return {
        "path": saved["path"],
        "updated_at": saved["updated_at"],
        "edited": True,
        "has_original": bool(generated),
        **propagated,
    }


@router.delete("/{project_id}/model/files")
async def revert_file(project_id: str, path: str) -> dict:
    """Put back what the generator wrote."""
    generated = await edits_store.discard(project_id, path)
    if not generated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No edit to revert, or the original was not kept",
        )
    # A revert is an edit in the other direction. Putting the file back without
    # telling the model would leave every saved view naming the member the edit
    # introduced, which fails the whole folder in Cube -- the same way the edit
    # itself did before it was propagated.
    propagated: dict[str, Any] = {}
    row = await models_store.latest(project_id, with_files=True)
    if row is not None and path.startswith("cubes/"):
        propagated = await _propagate(project_id, row, path, generated)

    target = _target(path)
    target.write_text(generated)
    return {"path": path, "content": generated, "reverted": True, **propagated}


# --- Checking one proposed member against the real table --------------------- #

_BARE = re.compile(r"^(?:\{CUBE\}\.)?([A-Za-z_][A-Za-z0-9_]*)$")
_NUMERIC = ("int", "numeric", "decimal", "real", "double", "float", "money", "serial")
_TEMPORAL = ("timestamp", "date", "time")


class MemberCheck(BaseModel):
    table: str
    kind: Literal["dimension", "measure", "segment"]
    type: str | None = None
    sql: str | None = None


@router.post("/{project_id}/model/members/check")
async def check_member(project_id: str, payload: MemberCheck) -> dict:
    """What the file validator cannot know: whether the SQL fits the table.

    Errors block the add; warnings are shown and the user decides. The split
    is deliberate -- a column that does not exist will never work, while a
    PII column might be exactly what an internal view needs.
    """
    errors: list[str] = []
    warnings: list[str] = []

    scan = await scans.latest(project_id, with_catalog=True)
    table = None
    if scan:
        table = next(
            (t for t in scan["catalog"]["tables"] if t["qualified_name"] == payload.table),
            None,
        )
    if table is None:
        return {
            "errors": [],
            "warnings": [f"{payload.table} is not in the latest scan, so the SQL was not checked."],
        }

    sql = (payload.sql or "").strip()
    if payload.kind == "measure" and payload.type == "count":
        return {"errors": [], "warnings": []}
    if not sql:
        return {"errors": ["SQL is required."], "warnings": []}

    match = _BARE.match(sql)
    if not match:
        # An expression. Checking it properly means running it, which needs a
        # working Cube; saying so is better than pretending it passed.
        return {
            "errors": [],
            "warnings": ["This is an expression, so only the file shape is checked, not the SQL."],
        }

    name = match.group(1)
    columns = {c["name"]: c for c in table["columns"]}
    column = columns.get(name)
    if column is None:
        # Edit distance rather than substring: a typo drops or swaps a letter,
        # which a substring match never finds.
        close = difflib.get_close_matches(name, list(columns), n=3, cutoff=0.6)
        hint = f" Did you mean {', '.join(f'`{c}`' for c in close)}?" if close else ""
        return {"errors": [f"{payload.table} has no column named `{name}`.{hint}"], "warnings": []}

    data_type = column["data_type"].lower()
    stats = column.get("stats") or {}

    if payload.kind == "measure" and payload.type in ("sum", "avg"):
        if not any(k in data_type for k in _NUMERIC):
            errors.append(
                f"`{name}` is {column['data_type']}. {payload.type} needs a number."
            )
        elif name == "id" or name.endswith("_id"):
            warnings.append(f"`{name}` looks like an identifier. Summing or averaging it is rarely meaningful.")

    if payload.kind == "dimension" and payload.type == "time":
        if not any(k in data_type for k in _TEMPORAL):
            errors.append(f"`{name}` is {column['data_type']}, not a date or time.")

    if payload.kind == "dimension" and payload.type == "number":
        if not any(k in data_type for k in _NUMERIC):
            warnings.append(f"`{name}` is {column['data_type']}, not a number.")

    if stats.get("pii_suspected"):
        warnings.append(
            f"`{name}` looks like personal data and was left out of the model on "
            "purpose. Anything added here is visible to everyone who queries the cube."
        )

    if bundle_mod.TENANT_PATTERN.match(name):
        warnings.append(
            f"`{name}` is the tenant column. It is a security boundary, not "
            "something to group or filter reports by."
        )

    return {"errors": errors, "warnings": warnings}
