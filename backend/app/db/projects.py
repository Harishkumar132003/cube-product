"""Projects: the top-level object, each owning at most one connection."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from uuid import UUID

from app.db import models_store, scans, store
from app.db.appdb import pool
from app.models.project import Project

_COLUMNS = (
    "id::text, name, created_at, business_context, business_context_updated_at, "
    "selected_tables, table_notes"
)


def _valid_id(record_id: str) -> bool:
    try:
        UUID(record_id)
    except ValueError:
        return False
    return True


def _to_model(row: dict[str, Any]) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        business_context=row["business_context"] or "",
        business_context_updated_at=row["business_context_updated_at"],
        selected_tables=row["selected_tables"] or [],
        table_notes=row["table_notes"] or {},
    )


async def create(name: str) -> Project:
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"INSERT INTO projects (name) VALUES (%s) RETURNING {_COLUMNS}", (name,)
        )
        row = await cur.fetchone()
    assert row is not None
    return _to_model(row)


async def list_all() -> list[Project]:
    """Every project, each with its connection attached if it has one."""
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM projects ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()

    projects = [_to_model(r) for r in rows]
    connections = {c.project_id: c for c in await store.list_all()}
    for project in projects:
        project.connection = connections.get(project.id)
        project.last_scan_at = await scans.last_scan_at(project.id)
        project.last_generated_at = await models_store.last_generated_at(project.id)
    return projects


async def get(record_id: str) -> Project | None:
    if not _valid_id(record_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM projects WHERE id = %s", (record_id,)
        )
        row = await cur.fetchone()
    if row is None:
        return None
    project = _to_model(row)
    project.connection = await store.get_by_project(record_id)
    project.last_scan_at = await scans.last_scan_at(record_id)
    project.last_generated_at = await models_store.last_generated_at(record_id)
    return project


async def rename(record_id: str, name: str) -> Project | None:
    if not _valid_id(record_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"UPDATE projects SET name = %s WHERE id = %s RETURNING {_COLUMNS}",
            (name, record_id),
        )
        row = await cur.fetchone()
    return _to_model(row) if row else None


async def delete(record_id: str) -> bool:
    """Removes the project and, by cascade, its connection."""
    if not _valid_id(record_id):
        return False
    async with pool().connection() as conn:
        cur = await conn.execute("DELETE FROM projects WHERE id = %s", (record_id,))
    return cur.rowcount > 0


async def set_business_context(record_id: str, text: str) -> Project | None:
    """Store the user's description. Clearing it clears the timestamp too."""
    if not _valid_id(record_id):
        return None
    stamp = "now()" if text.strip() else "NULL"
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"""
            UPDATE projects
               SET business_context = %s,
                   business_context_updated_at = {stamp}
             WHERE id = %s
         RETURNING {_COLUMNS}
            """,
            (text, record_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    project = _to_model(row)
    project.connection = await store.get_by_project(record_id)
    return project


async def set_selected_tables(record_id: str, tables: list[str]) -> Project | None:
    """Record which relations the model is built from."""
    if not _valid_id(record_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"UPDATE projects SET selected_tables = %s WHERE id = %s RETURNING {_COLUMNS}",
            (Jsonb(tables), record_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    project = _to_model(row)
    project.connection = await store.get_by_project(record_id)
    project.last_scan_at = await scans.last_scan_at(record_id)
    project.last_generated_at = await models_store.last_generated_at(record_id)
    return project


async def set_table_notes(record_id: str, notes: dict[str, dict]) -> Project | None:
    """Replace the per-table purposes. Blank entries are dropped."""
    if not _valid_id(record_id):
        return None
    cleaned = {
        name: note
        for name, note in notes.items()
        if (note.get("purpose") or "").strip()
    }
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"UPDATE projects SET table_notes = %s WHERE id = %s RETURNING {_COLUMNS}",
            (Jsonb(cleaned), record_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    project = _to_model(row)
    project.connection = await store.get_by_project(record_id)
    project.last_scan_at = await scans.last_scan_at(record_id)
    project.last_generated_at = await models_store.last_generated_at(record_id)
    return project
