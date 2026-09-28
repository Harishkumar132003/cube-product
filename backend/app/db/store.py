"""Saved connections, stored in the app's own Postgres database.

Passwords are encrypted with the key in backend/var/secret.key and are never
returned through the API -- they leave this module only to open a connection.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.core.crypto import decrypt, encrypt
from app.db.appdb import pool
from app.db.session import ConnectionParams
from app.models.connection import ConnectionInput, ProbeResult, StoredConnection

_COLUMNS = """
    id::text, project_id::text, name, host, port, database, username, sslmode,
    selected_schemas, last_probe, created_at, last_connected_at
"""


def _to_model(row: dict[str, Any]) -> StoredConnection:
    return StoredConnection(
        id=row["id"],
        project_id=row["project_id"],
        name=row["name"],
        host=row["host"],
        port=row["port"],
        database=row["database"],
        user=row["username"],
        sslmode=row["sslmode"],
        selected_schemas=row["selected_schemas"] or [],
        created_at=row["created_at"],
        last_connected_at=row["last_connected_at"],
        last_probe=(
            ProbeResult.model_validate(row["last_probe"]) if row["last_probe"] else None
        ),
    )


def _valid_id(record_id: str) -> bool:
    """Guard the uuid cast, so a bad path segment is a 404 rather than a 500."""
    try:
        UUID(record_id)
    except ValueError:
        return False
    return True


async def create(
    project_id: str, payload: ConnectionInput, probe: ProbeResult | None = None
) -> StoredConnection:
    """Attach a connection to a project, replacing any it already had."""
    async with pool().connection() as conn:
        await conn.execute("DELETE FROM connections WHERE project_id = %s", (project_id,))
        cur = await conn.execute(
            f"""
            INSERT INTO connections (project_id, name, host, port, database, username,
                                     password_enc, sslmode, last_probe)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (
                project_id,
                payload.name,
                payload.host,
                payload.port,
                payload.database,
                payload.user,
                encrypt(payload.password.get_secret_value()),
                payload.sslmode,
                Jsonb(probe.model_dump(mode="json")) if probe else None,
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    return _to_model(row)


async def list_all() -> list[StoredConnection]:
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM connections ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()
    return [_to_model(r) for r in rows]


async def get(record_id: str) -> StoredConnection | None:
    if not _valid_id(record_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM connections WHERE id = %s", (record_id,)
        )
        row = await cur.fetchone()
    return _to_model(row) if row else None


async def delete(record_id: str) -> bool:
    if not _valid_id(record_id):
        return False
    async with pool().connection() as conn:
        cur = await conn.execute("DELETE FROM connections WHERE id = %s", (record_id,))
    return cur.rowcount > 0


async def set_selected_schemas(record_id: str, schemas: list[str]) -> StoredConnection | None:
    if not _valid_id(record_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"UPDATE connections SET selected_schemas = %s WHERE id = %s RETURNING {_COLUMNS}",
            (Jsonb(schemas), record_id),
        )
        row = await cur.fetchone()
    return _to_model(row) if row else None


async def save_probe(record_id: str, probe: ProbeResult) -> None:
    if not _valid_id(record_id):
        return
    async with pool().connection() as conn:
        await conn.execute(
            """
            UPDATE connections
               SET last_probe = %s, last_connected_at = now()
             WHERE id = %s
            """,
            (Jsonb(probe.model_dump(mode="json")), record_id),
        )


async def params_for(record_id: str) -> ConnectionParams | None:
    """Rebuild connection parameters, decrypting the password."""
    if not _valid_id(record_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            """
            SELECT host, port, database, username, password_enc, sslmode
              FROM connections WHERE id = %s
            """,
            (record_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return ConnectionParams(
        host=row["host"],
        port=row["port"],
        database=row["database"],
        user=row["username"],
        password=decrypt(row["password_enc"]),
        sslmode=row["sslmode"],
    )


async def get_by_project(project_id: str) -> StoredConnection | None:
    if not _valid_id(project_id):
        return None
    async with pool().connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM connections WHERE project_id = %s", (project_id,)
        )
        row = await cur.fetchone()
    return _to_model(row) if row else None


async def delete_by_project(project_id: str) -> bool:
    """Drop a project's connection, leaving the project itself in place."""
    if not _valid_id(project_id):
        return False
    async with pool().connection() as conn:
        cur = await conn.execute(
            "DELETE FROM connections WHERE project_id = %s", (project_id,)
        )
    return cur.rowcount > 0
