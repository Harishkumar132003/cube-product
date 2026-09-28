"""Feeding a hand-edited model file back into the stored model.

The generated IR is what the view builder lists, what a saved view is validated
against, and what the vector index is built from. Editing a cube's YAML used to
change only the file on disk, so the two drifted: renaming a member left every
saved view pointing at a name that no longer existed, and because Cube compiles
the folder as one unit, that took the whole model down rather than just that
view.

So an edit is parsed back into the IR here, and anything a saved view referred
to is carried across with it.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from app.generate.ir import Cube, Dimension, Measure, MeasureFilter, Model, Segment

log = logging.getLogger(__name__)


def parse_cube(content: str) -> Cube | None:
    """Read one rendered cube file back into the IR shape."""
    data = yaml.safe_load(content)
    if not isinstance(data, dict) or not data.get("cubes"):
        return None
    body = data["cubes"][0]

    dimensions = [
        Dimension(
            name=d["name"],
            title=d.get("title") or d["name"],
            sql=d.get("sql") or d["name"],
            type=d.get("type") or "string",
            description=d.get("description") or "",
            primary_key=bool(d.get("primary_key")),
            public=d.get("public", True),
        )
        for d in body.get("dimensions") or []
        if d.get("name")
    ]
    measures = [
        Measure(
            name=m["name"],
            title=m.get("title") or m["name"],
            type=m.get("type") or "count",
            sql=m.get("sql"),
            description=m.get("description") or "",
            filters=[MeasureFilter(sql=f["sql"]) for f in (m.get("filters") or []) if f.get("sql")],
        )
        for m in body.get("measures") or []
        if m.get("name")
    ]
    segments = [
        Segment(
            name=s["name"],
            title=s.get("title") or s["name"],
            sql=s.get("sql") or "",
            description=s.get("description") or "",
        )
        for s in body.get("segments") or []
        if s.get("name")
    ]

    return Cube(
        name=body["name"],
        title=body.get("title") or body["name"],
        table=body.get("sql_table") or "",
        description=body.get("description") or "",
        dimensions=dimensions,
        measures=measures,
        segments=segments,
    )


def _members(cube: Cube) -> dict[str, tuple[str, str | None]]:
    """name -> (kind, sql). The sql is what identifies a member across a rename."""
    out: dict[str, tuple[str, str | None]] = {}
    for d in cube.dimensions:
        out[d.name] = ("dimension", d.sql)
    for m in cube.measures:
        out[m.name] = ("measure", m.sql)
    for s in cube.segments:
        out[s.name] = ("segment", s.sql)
    return out


def diff_members(before: Cube, after: Cube) -> dict[str, Any]:
    """What changed, with renames separated from genuine additions and removals.

    A member is taken to have been renamed when one disappears and another
    appears with the same kind and the same SQL. That is the difference between
    carrying a view across and silently dropping a column from it.
    """
    old, new = _members(before), _members(after)
    gone = [n for n in old if n not in new]
    added = [n for n in new if n not in old]

    renamed: dict[str, str] = {}
    for name in list(gone):
        signature = old[name]
        # Only unambiguous matches: two measures added with the same sql would
        # be a guess, and a wrong guess rewrites a view silently.
        matches = [
            candidate
            for candidate in added
            if new[candidate] == signature and candidate not in renamed.values()
        ]
        if len(matches) == 1:
            renamed[name] = matches[0]
            gone.remove(name)
            added.remove(matches[0])

    return {"renamed": renamed, "removed": gone, "added": added}


def apply_cube(model: Model, cube: Cube) -> Model:
    """Replace one cube in the model, keeping everything else as it was."""
    model.cubes = [cube if c.name == cube.name else c for c in model.cubes]
    return model


def remap_view_members(
    members: list[dict[str, Any]], cube_name: str, changes: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Carry a view's includes across a rename, and drop what no longer exists.

    Returns the new members plus what was renamed and what was dropped, so the
    user is told rather than discovering it as a missing column.
    """
    renamed: dict[str, str] = changes["renamed"]
    removed = set(changes["removed"])
    carried: list[str] = []
    dropped: list[str] = []

    out: list[dict[str, Any]] = []
    for member in members:
        if member.get("cube") != cube_name:
            out.append(member)
            continue
        includes: list[str] = []
        for name in member.get("includes") or []:
            if name in renamed:
                includes.append(renamed[name])
                carried.append(f"{name} -> {renamed[name]}")
            elif name in removed:
                dropped.append(name)
            else:
                includes.append(name)
        if includes:
            out.append({**member, "includes": includes})
    return out, carried, dropped
