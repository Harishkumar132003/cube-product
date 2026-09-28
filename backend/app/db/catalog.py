"""Metadata extraction from pg_catalog (plan.md step 2).

Read directly from pg_catalog rather than information_schema: it is more
complete and considerably faster on wide schemas. Everything here runs in the
metadata phase, so it dies fast on a slow server.

Two rules that are easy to get wrong and expensive to discover later:

* ``reltuples = -1`` means "never analyzed" on Postgres 14+, not "empty". A
  freshly restored database looks like this everywhere.
* A partitioned parent carries no rows of its own. Model the parent, skip the
  children, and sum the children's estimates onto it.
"""

from __future__ import annotations

from typing import Any, Literal

from psycopg import AsyncConnection

from app.db.session import ConnectionParams, Phase, connect, phase

RelKind = Literal["table", "partitioned_table", "view", "materialized_view"]

_KIND = {
    "r": "table",
    "p": "partitioned_table",
    "v": "view",
    "m": "materialized_view",
}

# Partition children are excluded via pg_inherits: only the parent is modelled.
_RELATIONS_SQL = """
SELECT
    c.oid::bigint                               AS oid,
    n.nspname                                   AS schema,
    c.relname                                   AS name,
    c.relkind                                   AS kind,
    c.reltuples                                 AS reltuples,
    c.relpages                                  AS relpages,
    pg_total_relation_size(c.oid)               AS bytes,
    c.relrowsecurity                            AS rls,
    obj_description(c.oid, 'pg_class')          AS comment,
    has_table_privilege(c.oid, 'SELECT')        AS readable
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s)
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
ORDER BY n.nspname, c.relname
"""

# Children of partitioned parents, so their row estimates can be folded in.
_PARTITION_ROLLUP_SQL = """
SELECT i.inhparent::bigint AS parent_oid,
       sum(GREATEST(child.reltuples, 0))::bigint AS child_rows,
       count(*)                                  AS partitions
FROM pg_inherits i
JOIN pg_class child ON child.oid = i.inhrelid
GROUP BY i.inhparent
"""

_COLUMNS_SQL = """
SELECT
    a.attrelid::bigint                          AS oid,
    a.attnum                                     AS position,
    a.attname                                    AS name,
    format_type(a.atttypid, a.atttypmod)         AS data_type,
    t.typname                                    AS base_type,
    t.typtype                                    AS type_kind,
    a.attnotnull                                 AS not_null,
    pg_get_expr(d.adbin, d.adrelid)              AS default_expr,
    col_description(a.attrelid, a.attnum)        AS comment
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = ANY(%(schemas)s)
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attrelid, a.attnum
"""

# Primary, foreign, unique and check constraints in one pass. Column positions
# are resolved to names here so the caller never touches attnum again.
_CONSTRAINTS_SQL = """
SELECT
    con.conrelid::bigint                         AS oid,
    con.conname                                  AS name,
    con.contype                                  AS kind,
    con.confrelid::bigint                        AS target_oid,
    pg_get_constraintdef(con.oid)                AS definition,
    ARRAY(
        SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        ORDER BY k.ord
    )                                            AS columns,
    ARRAY(
        SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
        ORDER BY k.ord
    )                                            AS target_columns
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s)
  AND con.contype IN ('p', 'f', 'u', 'c')
ORDER BY con.conrelid, con.contype
"""

# Unique indexes that are not backed by a constraint still prove uniqueness,
# which step 4 needs to decide cardinality.
_UNIQUE_INDEX_SQL = """
SELECT
    i.indrelid::bigint AS oid,
    ci.relname         AS name,
    ARRAY(
        SELECT a.attname
        FROM unnest(i.indkey::int[]) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
        ORDER BY k.ord
    ) AS columns
FROM pg_index i
JOIN pg_class ci ON ci.oid = i.indexrelid
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s)
  AND i.indisunique
  AND i.indpred IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM pg_constraint con
      WHERE con.conindid = i.indexrelid AND con.contype IN ('p', 'u')
  )
"""

# Indexed columns are a supporting hint for relationship inference.
_INDEXED_COLUMNS_SQL = """
SELECT DISTINCT i.indrelid::bigint AS oid, a.attname AS column
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey::int[])
WHERE n.nspname = ANY(%(schemas)s)
"""

_ENUMS_SQL = """
SELECT t.typname AS name, n.nspname AS schema,
       array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE n.nspname = ANY(%(schemas)s)
GROUP BY t.typname, n.nspname
"""

# A hand-written view is the best available evidence of intended joins.
_VIEWDEF_SQL = """
SELECT c.oid::bigint AS oid, pg_get_viewdef(c.oid, true) AS definition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s) AND c.relkind IN ('v', 'm')
"""


def _row_estimate(reltuples: float, rolled_up: int | None) -> tuple[int | None, str]:
    """Rows, plus how the number was arrived at.

    Returns None when the count is genuinely unknown, so downstream code can
    tell "no statistics" apart from "no rows".
    """
    if rolled_up is not None:
        return rolled_up, "summed from partitions"
    if reltuples is None or reltuples < 0:
        return None, "never analyzed"
    return int(reltuples), "estimate"


async def _fetch(conn: AsyncConnection, sql: str, schemas: list[str]) -> list[dict[str, Any]]:
    cur = await conn.execute(sql, {"schemas": schemas})
    return await cur.fetchall()


async def extract(params: ConnectionParams, schemas: list[str]) -> dict[str, Any]:
    """Read the catalog for the given schemas into a plain dict."""
    async with connect(params) as conn:
        async with phase(conn, Phase.METADATA):
            relations = await _fetch(conn, _RELATIONS_SQL, schemas)
            columns = await _fetch(conn, _COLUMNS_SQL, schemas)
            constraints = await _fetch(conn, _CONSTRAINTS_SQL, schemas)
            unique_indexes = await _fetch(conn, _UNIQUE_INDEX_SQL, schemas)
            indexed = await _fetch(conn, _INDEXED_COLUMNS_SQL, schemas)
            enums = await _fetch(conn, _ENUMS_SQL, schemas)
            viewdefs = await _fetch(conn, _VIEWDEF_SQL, schemas)
            cur = await conn.execute(_PARTITION_ROLLUP_SQL)
            rollup = {r["parent_oid"]: r for r in await cur.fetchall()}

    by_oid: dict[int, dict[str, Any]] = {}
    for rel in relations:
        oid = rel["oid"]
        parent_rollup = rollup.get(oid)
        rows, basis = _row_estimate(
            rel["reltuples"], parent_rollup["child_rows"] if parent_rollup else None
        )
        by_oid[oid] = {
            "schema": rel["schema"],
            "name": rel["name"],
            "qualified_name": f'{rel["schema"]}.{rel["name"]}',
            "kind": _KIND[rel["kind"]],
            "rows": rows,
            "row_basis": basis,
            "pages": rel["relpages"],
            "bytes": rel["bytes"],
            "partitions": parent_rollup["partitions"] if parent_rollup else 0,
            "rls": rel["rls"],
            "readable": rel["readable"],
            "comment": rel["comment"],
            "columns": [],
            "primary_key": [],
            "foreign_keys": [],
            "unique": [],
            "checks": [],
            "view_definition": None,
        }

    indexed_by_oid: dict[int, set[str]] = {}
    for row in indexed:
        indexed_by_oid.setdefault(row["oid"], set()).add(row["column"])

    for col in columns:
        table = by_oid.get(col["oid"])
        if table is None:
            continue
        table["columns"].append(
            {
                "name": col["name"],
                "position": col["position"],
                "data_type": col["data_type"],
                "base_type": col["base_type"],
                # 'e' is an enum type; the labels are attached below.
                "is_enum": col["type_kind"] == "e",
                "not_null": col["not_null"],
                "default": col["default_expr"],
                "comment": col["comment"],
                "indexed": col["name"] in indexed_by_oid.get(col["oid"], set()),
            }
        )

    for con in constraints:
        table = by_oid.get(con["oid"])
        if table is None:
            continue
        if con["kind"] == "p":
            table["primary_key"] = list(con["columns"])
        elif con["kind"] == "f":
            target = by_oid.get(con["target_oid"])
            table["foreign_keys"].append(
                {
                    "name": con["name"],
                    "columns": list(con["columns"]),
                    "target": target["qualified_name"] if target else None,
                    "target_columns": list(con["target_columns"]),
                    "provenance": "declared",
                }
            )
        elif con["kind"] == "u":
            table["unique"].append(list(con["columns"]))
        elif con["kind"] == "c":
            table["checks"].append({"name": con["name"], "definition": con["definition"]})

    for idx in unique_indexes:
        table = by_oid.get(idx["oid"])
        if table is not None and idx["columns"]:
            table["unique"].append(list(idx["columns"]))

    for view in viewdefs:
        table = by_oid.get(view["oid"])
        if table is not None:
            table["view_definition"] = view["definition"]

    enum_labels = {f'{e["schema"]}.{e["name"]}': list(e["labels"]) for e in enums}
    for table in by_oid.values():
        for col in table["columns"]:
            if col["is_enum"]:
                key = f'{table["schema"]}.{col["base_type"]}'
                col["enum_values"] = enum_labels.get(key, [])

    tables = sorted(by_oid.values(), key=lambda t: (t["schema"], t["name"]))
    return {
        "schemas": schemas,
        "tables": tables,
        "enums": [
            {"name": f'{e["schema"]}.{e["name"]}', "values": list(e["labels"])} for e in enums
        ],
        "counts": {
            "tables": sum(1 for t in tables if t["kind"] in ("table", "partitioned_table")),
            "views": sum(1 for t in tables if t["kind"] == "view"),
            "materialized_views": sum(1 for t in tables if t["kind"] == "materialized_view"),
            "columns": sum(len(t["columns"]) for t in tables),
            "declared_foreign_keys": sum(len(t["foreign_keys"]) for t in tables),
            "enums": len(enums),
            "unreadable": sum(1 for t in tables if not t["readable"]),
        },
    }
