"""The app's own Postgres database: pool and schema.

Distinct from every connection in app.db.session, which targets a customer
database read-only. This one the app owns and writes to.
"""

from __future__ import annotations

import logging

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings

log = logging.getLogger(__name__)

_SCHEMA = """
-- A project is the top-level object. It owns exactly one connection, and will
-- own the evidence bundle and generated model built from it.
CREATE TABLE IF NOT EXISTS projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS connections (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name              text NOT NULL,
    host              text NOT NULL,
    port              integer NOT NULL,
    database          text NOT NULL,
    username          text NOT NULL,
    password_enc      text NOT NULL,
    sslmode           text NOT NULL,
    selected_schemas  jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_probe        jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_connected_at timestamptz
);

CREATE INDEX IF NOT EXISTS connections_created_at_idx
    ON connections (created_at DESC);

CREATE INDEX IF NOT EXISTS projects_created_at_idx
    ON projects (created_at DESC);
"""

# Applied after _SCHEMA. Idempotent, so startup can run it every time.
#
# Connections predate projects. Rather than dropping them, each orphan is given
# a project named after it, then the column is tightened to NOT NULL and one
# connection per project.
_MIGRATIONS = """
ALTER TABLE connections
    ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES projects (id) ON DELETE CASCADE;

DO $$
DECLARE
    orphan record;
    new_project_id uuid;
BEGIN
    FOR orphan IN SELECT id, name FROM connections WHERE project_id IS NULL LOOP
        INSERT INTO projects (name) VALUES (orphan.name) RETURNING id INTO new_project_id;
        UPDATE connections SET project_id = new_project_id WHERE id = orphan.id;
    END LOOP;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM connections WHERE project_id IS NULL) THEN
        ALTER TABLE connections ALTER COLUMN project_id SET NOT NULL;
    END IF;
END $$;

-- One connection per project.
CREATE UNIQUE INDEX IF NOT EXISTS connections_project_id_key
    ON connections (project_id);

-- Step 6b: what the user tells us about the business. Free text for now;
-- structured facts are extracted from it later and stored alongside.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS business_context text NOT NULL DEFAULT '';
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS business_context_updated_at timestamptz;

-- Step 2 + 3: one row per scan of a project's database. Kept as history so
-- schema drift can be diffed later rather than silently overwritten.
CREATE TABLE IF NOT EXISTS scans (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    schemas     jsonb NOT NULL,
    counts      jsonb NOT NULL,
    profile     jsonb NOT NULL,
    catalog     jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS scans_project_idx
    ON scans (project_id, created_at DESC);

-- Which relations the model is built from. Empty means every readable one,
-- so a fresh project needs no migration of intent.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS selected_tables jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Step 6b: what each table is for, keyed by qualified name. Written by the
-- user; later an AI pass fills in guesses for them to correct.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS table_notes jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Step 8: one row per generation run. History, so a regeneration can be
-- compared against what it replaced rather than silently overwriting it.
CREATE TABLE IF NOT EXISTS models (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    scope       jsonb NOT NULL,
    counts      jsonb NOT NULL,
    questions   jsonb NOT NULL,
    ir          jsonb NOT NULL,
    files       jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS models_project_idx
    ON models (project_id, created_at DESC);

-- Views are the user's decisions about which business surfaces exist, so they
-- live apart from the generation that produced the cubes and survive a rerun.
CREATE TABLE IF NOT EXISTS views (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    name        text NOT NULL,
    title       text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    root_cube   text NOT NULL,
    -- [{cube, join_path, includes: [member, ...]}, ...] in display order.
    members     jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

-- Hand edits to generated files. Kept apart from the generation that produced
-- them: a rerun rewrites the folder, and without this the edit would be gone
-- with no copy anywhere.
CREATE TABLE IF NOT EXISTS file_edits (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    path        text NOT NULL,
    content     text NOT NULL,
    -- What the generator produced, so an edit can be told from the original
    -- and reverted without regenerating.
    generated   text NOT NULL DEFAULT '',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, path)
);

-- Per-project prompt overrides. Only rows for prompts actually changed: a
-- project that never edits one keeps following the default in code, and picks
-- up later improvements to it.
CREATE TABLE IF NOT EXISTS project_prompts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    key         text NOT NULL,
    body        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, key)
);
"""

_pool: AsyncConnectionPool | None = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("App database pool is not open. Did startup run?")
    return _pool


async def open_pool() -> None:
    """Open the pool and apply the schema. Called once at startup."""
    global _pool
    _pool = AsyncConnectionPool(
        settings.app_database_url,
        min_size=1,
        max_size=8,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    await _pool.open(wait=True, timeout=10)
    async with _pool.connection() as conn:
        await conn.execute(_SCHEMA)
        await conn.execute(_MIGRATIONS)
    log.info("app database ready")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
