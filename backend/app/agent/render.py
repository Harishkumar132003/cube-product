"""Resolving a prompt for one project: default, override, placeholders.

Kept apart from app.agent.prompts so that module stays pure text and rules, with
no database in it.
"""

from __future__ import annotations

from typing import Any

from app.agent import prompts
from app.db import projects, prompts_store, views_store

#: Shown in place of the business context when the project has none. Saying so
#: is better than leaving a hole: the model is told the description is missing
#: rather than silently reading an empty section.
NO_CONTEXT = (
    "(No business context has been written for this project yet, so treat any "
    "question about its data as a data question.)"
)

NO_VIEWS = "(This project has no views yet.)"


def _views_block(views: list[dict[str, Any]]) -> str:
    """The view menu the selection prompt chooses from.

    Generated rather than stored: views are added and renamed, and a menu typed
    out by hand goes stale without anything noticing.
    """
    if not views:
        return NO_VIEWS
    lines = []
    for view in views:
        title = view.get("title") or view["name"]
        description = (view.get("description") or "").strip() or "No description."
        cubes = ", ".join(
            sorted({m["cube"] for m in (view.get("members") or []) if m.get("cube")})
        )
        # The name is labelled, not just placed first: given "- x = Title. …" a
        # model reads the title as the answer and returns that instead.
        lines.append(f"- name: {view['name']}")
        lines.append(f"  {title}. {description}")
        if cubes:
            lines.append(f"  Covers: {cubes}")
    return "\n".join(lines)


async def values(project_id: str, **extra: str) -> dict[str, str]:
    """Everything a prompt's placeholders can be filled from."""
    project = await projects.get(project_id)
    context = ((project.business_context if project else "") or "").strip()
    views = await views_store.list_for(project_id)
    return {
        "business_context": context or NO_CONTEXT,
        "views": _views_block(views),
        **extra,
    }


async def effective(project_id: str) -> dict[str, dict[str, Any]]:
    """Every prompt, as it stands for this project."""
    overrides = await prompts_store.overrides_for(project_id)
    out: dict[str, dict[str, Any]] = {}
    for spec in prompts.SPECS:
        override = overrides.get(spec.key)
        out[spec.key] = {
            "key": spec.key,
            "label": spec.label,
            "purpose": spec.purpose,
            "default": spec.default,
            "body": override["body"] if override else spec.default,
            "customised": override is not None,
            "updated_at": override["updated_at"] if override else None,
            "allowed": list(spec.allowed),
            "required": list(spec.required),
            "is_llm": spec.is_llm,
            "tags": list(spec.tags),
        }
    return out


async def resolve(project_id: str, key: str, **extra: str) -> str:
    """The exact text this project would send for this prompt, right now."""
    spec = prompts.BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"There is no prompt called {key}")
    overrides = await prompts_store.overrides_for(project_id)
    body = overrides[key]["body"] if key in overrides else spec.default
    filled = await values(project_id, **extra)
    # Only what this prompt declares, so a stray placeholder in one prompt
    # cannot be filled by accident from another's data.
    return prompts.fill(body, {k: v for k, v in filled.items() if k in spec.allowed})
