"""Per-project prompt overrides.

A row exists only for a prompt someone changed. Absence means "use the default",
which is what lets an unedited project inherit improvements to the defaults
rather than being frozen at whatever they said the day it was created.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.appdb import pool


def _valid_id(record_id: str) -> bool:
    try:
        UUID(record_id)
    except ValueError:
        return False
    return True


async def overrides_for(project_id: str) -> dict[str, dict[str, Any]]:
    """Edited prompts only, keyed by prompt key."""
    if not _valid_id(project_id):
        return {}
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT key, body, updated_at FROM project_prompts WHERE project_id = %s",
            (project_id,),
        )
        rows = await cur.fetchall()
    return {r["key"]: dict(r) for r in rows}


async def save(project_id: str, key: str, body: str) -> dict[str, Any]:
    async with pool().connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO project_prompts (project_id, key, body)
            VALUES (%s, %s, %s)
            ON CONFLICT (project_id, key) DO UPDATE
               SET body = EXCLUDED.body, updated_at = now()
            RETURNING key, body, updated_at
            """,
            (project_id, key, body),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def reset(project_id: str, key: str) -> bool:
    """Drop the override, so the default applies again."""
    if not _valid_id(project_id):
        return False
    async with pool().connection() as conn:
        cur = await conn.execute(
            "DELETE FROM project_prompts WHERE project_id = %s AND key = %s",
            (project_id, key),
        )
    return cur.rowcount > 0
