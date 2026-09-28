"""Stored scans: the catalog and column statistics read from a database."""

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
    project_id: str, schemas: list[str], catalog: dict[str, Any]
) -> dict[str, Any]:
    """Record a scan. The catalog dict carries its own counts and profile."""
    async with pool().connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO scans (project_id, schemas, counts, profile, catalog)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id::text, created_at, schemas, counts, profile
            """,
            (
                project_id,
                Jsonb(schemas),
                Jsonb(catalog["counts"]),
                Jsonb(catalog.get("profile", {})),
                Jsonb(catalog),
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def latest(project_id: str, *, with_catalog: bool = False) -> dict[str, Any] | None:
    """The most recent scan. The full catalog is large, so it is opt-in."""
    if not _valid_id(project_id):
        return None
    columns = "id::text, created_at, schemas, counts, profile"
    if with_catalog:
        columns += ", catalog"
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"""
            SELECT {columns} FROM scans
             WHERE project_id = %s
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (project_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def last_scan_at(project_id: str) -> datetime | None:
    if not _valid_id(project_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            "SELECT max(created_at) AS at FROM scans WHERE project_id = %s",
            (project_id,),
        )
        row = await cur.fetchone()
    return row["at"] if row else None
