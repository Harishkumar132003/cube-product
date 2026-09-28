"""Saved views.

A view is a decision about which business surface exists and what belongs on
it -- the user's, not the model's. It is stored per project rather than on a
generation, so regenerating the cubes does not discard it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db.appdb import pool

_COLUMNS = (
    "id::text, project_id::text, name, title, description, root_cube, "
    "members, created_at, updated_at"
)


def _valid_id(record_id: str) -> bool:
    try:
        UUID(record_id)
    except ValueError:
        return False
    return True


async def list_for(project_id: str) -> list[dict[str, Any]]:
    if not _valid_id(project_id):
        return []
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM views WHERE project_id = %s ORDER BY name",
            (project_id,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def upsert(
    project_id: str,
    *,
    name: str,
    title: str,
    description: str,
    root_cube: str,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create the view, or replace it if that name is already taken.

    The name is the filename and the Cube identifier, so it is the identity
    here too -- saving twice under one name is an edit, not a duplicate.
    """
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"""
            INSERT INTO views (project_id, name, title, description, root_cube, members)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, name) DO UPDATE
               SET title = EXCLUDED.title,
                   description = EXCLUDED.description,
                   root_cube = EXCLUDED.root_cube,
                   members = EXCLUDED.members,
                   updated_at = now()
            RETURNING {_COLUMNS}
            """,
            (project_id, name, title, description, root_cube, Jsonb(members)),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def delete(project_id: str, name: str) -> bool:
    if not _valid_id(project_id):
        return False
    async with pool().connection() as conn:
        cur = await conn.execute(
            "DELETE FROM views WHERE project_id = %s AND name = %s", (project_id, name)
        )
    return cur.rowcount > 0
