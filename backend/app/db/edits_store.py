"""Hand edits to generated model files.

An edit is the user's, and the generator's output is not. Keeping both means a
file can be reverted, and an edit is not silently lost the next time the model
is regenerated.
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


async def save(project_id: str, path: str, content: str, generated: str) -> dict[str, Any]:
    async with pool().connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO file_edits (project_id, path, content, generated)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (project_id, path) DO UPDATE
               SET content = EXCLUDED.content, updated_at = now()
            RETURNING path, content, generated, updated_at
            """,
            (project_id, path, content, generated),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def list_for(project_id: str) -> list[dict[str, Any]]:
    if not _valid_id(project_id):
        return []
    async with pool().connection() as conn:
        cur = await conn.execute(
            # A row equal to the original is not an edit, whatever wrote it.
            "SELECT path, content, generated, updated_at FROM file_edits "
            "WHERE project_id = %s AND content <> generated ORDER BY path",
            (project_id,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def discard(project_id: str, path: str) -> str | None:
    """Drop the edit and hand back what the generator originally wrote."""
    if not _valid_id(project_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            "DELETE FROM file_edits WHERE project_id = %s AND path = %s "
            "RETURNING generated",
            (project_id, path),
        )
        row = await cur.fetchone()
    return row["generated"] if row else None
