"""Checking a hand-edited model file before it reaches disk.

YAML fails quietly in ways that matter here. Indentation decides structure, so
a measure indented one space too far becomes a key inside the measure above it
rather than a new measure -- valid YAML, wrong model, and Cube reports it much
later as a missing member. The checks run in order of how early they can catch
that: does it parse, is it shaped like a model file, does it say what it needs
to say.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from app.generate.file_schema import ModelFile


class FileProblem(Exception):
    """A reason the file cannot be saved, located where possible."""

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        hint: str | None = None,
        kind: str = "schema",
    ):
        super().__init__(message)
        self.message = message
        self.line = line
        self.hint = hint
        # indentation | syntax | schema -- the UI words the message from this.
        self.kind = kind

    def as_dict(self) -> dict[str, Any]:
        return {
            "detail": self.message,
            "line": self.line,
            "hint": self.hint,
            "kind": self.kind,
        }


def _parse(content: str) -> Any:
    try:
        return yaml.safe_load(content)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        problem = (exc.problem or "could not be parsed").strip()
        context = (exc.context or "").strip()
        hint = None
        kind = "syntax"
        # These are what a mis-indented block actually looks like coming out of
        # the parser, so they are named as indentation rather than passed on in
        # the raw libyaml wording.
        if "found character '\\t'" in problem:
            hint = "Tabs cannot be used for indentation in YAML. Use spaces."
            kind = "indentation"
        elif "mapping values are not allowed" in problem:
            hint = (
                "This usually means a line is indented too far, so a key landed "
                "inside the value above it."
            )
            kind = "indentation"
        elif "expected <block end>" in problem or (context and "block" in context):
            hint = "Check the indentation of this line against the one above it."
            kind = "indentation"
        raise FileProblem(
            f"Line {mark.line + 1}: {problem}" if mark else problem,
            line=(mark.line + 1) if mark else None,
            hint=hint,
            kind=kind,
        ) from exc
    except yaml.YAMLError as exc:  # pragma: no cover - parser without a mark
        raise FileProblem(f"This is not valid YAML: {exc}") from exc


def _describe(error: dict[str, Any]) -> str:
    where = ".".join(str(p) for p in error["loc"]) or "the file"
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {message}"


def check(path: str, content: str) -> dict[str, Any]:
    """Validate one model file. Returns the parsed document, or raises."""
    if not content.strip():
        raise FileProblem("The file is empty.")

    data = _parse(content)

    if not isinstance(data, dict):
        raise FileProblem(
            "A model file must be a mapping with a `cubes:` or `views:` key at "
            "the top level.",
            hint="A list at the top level usually means the first line is indented.",
            kind="indentation",
        )

    try:
        ModelFile.model_validate(data)
    except ValidationError as exc:
        problems = [_describe(e) for e in exc.errors()[:4]]
        raise FileProblem("; ".join(problems)) from exc

    expected = "views" if path.startswith("views/") else "cubes"
    if not data.get(expected):
        raise FileProblem(
            f"`{path}` must define `{expected}:`, and it is missing or empty.",
            hint=(
                "Indentation is the usual cause: the key is there but its "
                "contents sit at the wrong depth."
            ),
            kind="indentation",
        )

    # The filename is the identity Cube uses, so a rename here would silently
    # orphan the file rather than rename the cube.
    stem = path.rsplit("/", 1)[-1].removesuffix(".yml")
    names = [entry.get("name") for entry in data[expected]]
    if stem not in names:
        raise FileProblem(
            f"`{path}` defines {', '.join(n for n in names if n) or 'nothing'}, "
            f"but the file is named `{stem}`.",
            hint="Rename the file rather than the entry, or change the name back.",
        )

    if expected == "cubes":
        for cube in data["cubes"]:
            # Dimensions, measures and segments share one namespace inside a
            # cube -- `claims.status` must mean exactly one thing -- so a name
            # is checked across all three, not within each list.
            keys = [
                m.get("name")
                for kind in ("dimensions", "measures", "segments")
                for m in (cube.get(kind) or [])
            ]
            duplicates = sorted({k for k in keys if keys.count(k) > 1 and k})
            if duplicates:
                raise FileProblem(
                    f"`{cube.get('name')}` uses {', '.join(duplicates)} more than once. "
                    "Dimensions, measures and segments share one set of names.",
                    hint="Two members with one name usually means a block was "
                    "pasted without changing its name.",
                )
            primaries = [d.get("name") for d in cube.get("dimensions", []) if d.get("primary_key")]
            if len(primaries) > 1:
                raise FileProblem(
                    f"`{cube.get('name')}` marks {len(primaries)} dimensions as "
                    f"primary_key ({', '.join(primaries)}). Cube allows one.",
                )

    return data
