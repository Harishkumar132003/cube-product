"""Per-project prompt management.

The prompts that decide what a question means are as much a part of a project's
configuration as its views, and they are the part a reviewer most often needs to
adjust. They are editable, with the defaults always one click away.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.agent import prompts as prompt_defs
from app.agent import render
from app.db import prompts_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["prompts"])


class PromptBody(BaseModel):
    body: str


@router.get("/{project_id}/prompts")
async def list_prompts(project_id: str) -> list[dict[str, Any]]:
    """Every prompt, its default, and whether this project has changed it."""
    resolved = await render.effective(project_id)
    # Declaration order, which is the order the flow runs in.
    return [resolved[spec.key] for spec in prompt_defs.SPECS]


@router.get("/{project_id}/prompts/{key}/preview")
async def preview_prompt(project_id: str, key: str) -> dict[str, Any]:
    """The prompt with its placeholders filled from this project's real data.

    The point of editing a prompt is what the model finally reads, and that is
    not what is in the editor -- the view menu and the business context are
    substituted in.
    """
    spec = prompt_defs.BY_KEY.get(key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No prompt called {key}")
    try:
        text = await render.resolve(
            project_id,
            key,
            # Stand-ins for the placeholders only a live request can fill.
            target_view_name="<the view chosen for the question>",
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"key": key, "text": text, "characters": len(text)}


@router.put("/{project_id}/prompts/{key}")
async def save_prompt(project_id: str, key: str, payload: PromptBody) -> dict[str, Any]:
    """Store an override, after checking it can still do its job."""
    try:
        prompt_defs.validate(key, payload.body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    spec = prompt_defs.BY_KEY[key]
    # Saving the default back is the same as never having changed it. Storing it
    # would quietly freeze this project on today's wording.
    if payload.body.strip() == spec.default.strip():
        await prompts_store.reset(project_id, key)
        return {"key": key, "customised": False, "body": spec.default}

    saved = await prompts_store.save(project_id, key, payload.body)
    return {"key": key, "customised": True, **saved}


@router.delete("/{project_id}/prompts/{key}")
async def reset_prompt(project_id: str, key: str) -> dict[str, Any]:
    """Drop the override and go back to the default."""
    spec = prompt_defs.BY_KEY.get(key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No prompt called {key}")
    await prompts_store.reset(project_id, key)
    return {"key": key, "customised": False, "body": spec.default}
