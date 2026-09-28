"""Generated models, stored per project."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db.appdb import pool


def _valid_id(record_id: str) -> bool:
    try:
        UUID(record_id)
    except ValueError:
        return False
    return True


async def save(
    project_id: str,
    *,
    scope: dict[str, Any],
    counts: dict[str, Any],
    questions: list[dict[str, Any]],
    ir: dict[str, Any],
    files: dict[str, str],
) -> dict[str, Any]:
    async with pool().connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO models (project_id, scope, counts, questions, ir, files)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id::text, created_at, scope, counts, questions
            """,
            (
                project_id,
                Jsonb(scope),
                Jsonb(counts),
                Jsonb(questions),
                Jsonb(ir),
                Jsonb(files),
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def latest(project_id: str, *, with_files: bool = False) -> dict[str, Any] | None:
    if not _valid_id(project_id):
        return None
    columns = "id::text, created_at, scope, counts, questions, ir"
    if with_files:
        columns += ", files"
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {columns} FROM models WHERE project_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def last_generated_at(project_id: str) -> datetime | None:
    if not _valid_id(project_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT max(created_at) AS at FROM models WHERE project_id = %s", (project_id,)
        )
        row = await cur.fetchone()
    return row["at"] if row else None


async def update_ir(project_id: str, ir: dict[str, Any]) -> bool:
    """Rewrite the latest model's IR in place.

    In place rather than as a new row: a hand edit is a correction to the model
    that was generated, not a new generation, and the history exists to compare
    generations against each other.
    """
    if not _valid_id(project_id):
        return False
    async with pool().connection() as conn:
        cur = await conn.execute(
            """
            UPDATE models SET ir = %s
             WHERE id = (SELECT id FROM models WHERE project_id = %s
                          ORDER BY created_at DESC LIMIT 1)
            """,
            (Jsonb(ir), project_id),
        )
    return cur.rowcount > 0
