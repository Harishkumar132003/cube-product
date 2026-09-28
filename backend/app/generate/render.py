"""IR -> Cube YAML (plan.md step 8).

Deterministic: the same IR always renders the same bytes. The model never
produces YAML, so a whole class of compile errors -- invented properties,
broken indentation, mis-typed relationship names -- cannot occur.

Layout follows the convention in a hand-written Cube project: base tables
become cubes under ``cubes/``, and the curated surface becomes views under
``views/``.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from app.generate.ir import Cube, Model, View


class _Dumper(yaml.SafeDumper):
    """Block style throughout, and literal blocks for multi-line prose."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # Keep list items indented under their key, which is how Cube's own
        # documentation formats models.
        return super().increase_indent(flow, False)


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def _dump(payload: dict[str, Any]) -> str:
    return yaml.dump(
        payload,
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=88,
    )


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def _normalise_join_sql(sql: str, target_name: str) -> str:
    """Resolve the target placeholder to the real cube name.

    Cube resolves `{CUBE}` to the declaring cube and `{other}` to a joined one.
    Models reliably get `{CUBE}` right and just as reliably invent the other
    side -- `{target_cube}`, `{target}`, the table name with its schema. The
    renderer knows the answer, so it substitutes rather than trusting it.
    """

    def swap(match: re.Match[str]) -> str:
        token = match.group(1)
        return match.group(0) if token == "CUBE" else "{" + target_name + "}"

    return _PLACEHOLDER.sub(swap, sql)


def _cube_payload(cube: Cube, model: Model) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": cube.name,
        "title": cube.title,
        "sql_table": cube.table,
        "description": cube.description,
    }

    joins = [j for j in model.joins if j.declaring_cube == cube.table]
    if joins:
        by_table = {c.table: c.name for c in model.cubes}
        body["joins"] = [
            {
                "name": by_table[j.target_cube],
                "sql": _normalise_join_sql(j.sql, by_table[j.target_cube]),
                "relationship": j.relationship,
            }
            for j in joins
            # A join to a cube that was not generated would not compile.
            if j.target_cube in by_table
        ]
        if not body["joins"]:
            del body["joins"]

    dimensions: list[dict[str, Any]] = []
    for dim in cube.dimensions:
        entry: dict[str, Any] = {
            "name": dim.name,
            "title": dim.title,
            "sql": dim.sql,
            "type": dim.type,
        }
        if dim.primary_key:
            entry["primary_key"] = True
        if not dim.public:
            entry["public"] = False
        if dim.description:
            entry["description"] = dim.description
        dimensions.append(entry)
    if dimensions:
        body["dimensions"] = dimensions

    measures: list[dict[str, Any]] = []
    for measure in cube.measures:
        entry = {"name": measure.name, "title": measure.title, "type": measure.type}
        # `count` takes no sql; everything else needs one.
        if measure.sql and measure.type != "count":
            entry["sql"] = measure.sql
        if measure.filters:
            entry["filters"] = [{"sql": f.sql} for f in measure.filters]
        if measure.description:
            entry["description"] = measure.description
        measures.append(entry)
    if measures:
        body["measures"] = measures

    if cube.segments:
        body["segments"] = [
            {"name": s.name, "title": s.title, "sql": s.sql, "description": s.description}
            for s in cube.segments
        ]

    return {"cubes": [body]}


def _view_payload(view: View) -> dict[str, Any]:
    return {
        "views": [
            {
                "name": view.name,
                "title": view.title,
                "description": view.description,
                "cubes": [
                    {"join_path": m.join_path, "includes": m.includes} for m in view.cubes
                ],
            }
        ]
    }


def render_view(view: View) -> str:
    """One view's YAML. The API renders a preview before anything is saved."""
    return _dump(_view_payload(view))


def render_cubes(model: Model) -> dict[str, str]:
    """Just the cubes. Views are saved separately and rendered from storage."""
    return {
        f"cubes/{cube.name}.yml": _dump(_cube_payload(cube, model))
        for cube in model.cubes
    }


def render(model: Model) -> dict[str, str]:
    """Render to a map of relative path -> file contents."""
    files = render_cubes(model)
    for view in model.views:
        files[f"views/{view.name}.yml"] = _dump(_view_payload(view))
    return files
