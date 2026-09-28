"""Connection probe: what this role can actually see (plan.md steps 1-2).

The connect screen is the first place the pipeline's assumptions meet a real
database, so the probe reports the conditions that degrade later steps rather
than just succeeding or failing. Everything here runs in the metadata phase.
"""

from __future__ import annotations

from psycopg import AsyncConnection

from app.db.session import ConnectionParams, Phase, connect, phase
from app.models.connection import (
    ProbeResult,
    SchemaSummary,
    ServerInfo,
    Warning_,
)

# Postgres 14 is where reltuples = -1 means "never analyzed" rather than "empty".
MIN_SUPPORTED_VERSION_NUM = 140_000

_SERVER_INFO_SQL = """
SELECT
    current_database()                          AS database,
    current_user                                AS role_name,
    version()                                   AS server_version,
    current_setting('server_version_num')::int  AS server_version_num,
    pg_is_in_recovery()                         AS is_replica,
    current_setting('default_transaction_read_only') = 'on' AS read_only_enforced,
    (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser,
    (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user)
                                                AS bypasses_rls,
    EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')
                                                AS has_pg_stat_statements
"""

# pg_read_all_stats governs pg_stat_* views only. It does NOT unblock pg_stats,
# which follows SELECT privilege on the table (plan.md step 1).
_READ_ALL_STATS_SQL = """
SELECT pg_has_role(current_user, 'pg_read_all_stats', 'member') AS has_read_all_stats
"""

# User-visible schemas: system schemas and extension-owned schemas are excluded.
_SCHEMAS_SQL = """
SELECT n.nspname AS name
FROM pg_namespace n
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\\_toast%'
  AND n.nspname NOT LIKE 'pg\\_temp%'
  AND has_schema_privilege(n.oid, 'USAGE')
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_namespace'::regclass
        AND d.objid = n.oid
        AND d.deptype = 'e'
  )
ORDER BY 1
"""

# Partition children are skipped via pg_inherits: only the parent is modelled.
_SCHEMA_SUMMARY_SQL = """
SELECT
    n.nspname AS name,
    count(*) FILTER (WHERE c.relkind IN ('r', 'p'))            AS tables,
    count(*) FILTER (WHERE c.relkind = 'p')                    AS partitioned_tables,
    count(*) FILTER (WHERE c.relkind = 'v')                    AS views,
    count(*) FILTER (WHERE c.relkind = 'm')                    AS materialized_views,
    count(*) FILTER (WHERE c.relkind IN ('r', 'p')
                       AND c.relrowsecurity)                   AS rls_tables,
    count(*) FILTER (WHERE c.relkind IN ('r', 'p')
                       AND c.reltuples = -1)                   AS never_analyzed,
    count(*) FILTER (WHERE c.relkind IN ('r', 'p', 'v', 'm')
                       AND NOT has_table_privilege(c.oid, 'SELECT')) AS unreadable
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s)
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
GROUP BY 1
ORDER BY 1
"""


async def _fetch_server_info(conn: AsyncConnection) -> ServerInfo:
    cur = await conn.execute(_SERVER_INFO_SQL)
    row = await cur.fetchone()
    assert row is not None

    # pg_read_all_stats has existed since PG 10, but probe defensively: a
    # missing role would otherwise fail the whole server-info read.
    has_read_all_stats = False
    try:
        cur = await conn.execute(_READ_ALL_STATS_SQL)
        stats_row = await cur.fetchone()
        has_read_all_stats = bool(stats_row and stats_row["has_read_all_stats"])
    except Exception:  # noqa: BLE001 - absence is a capability answer, not an error
        has_read_all_stats = False

    return ServerInfo(
        database=row["database"],
        role_name=row["role_name"],
        server_version=row["server_version"].split(" on ")[0],
        server_version_num=row["server_version_num"],
        is_replica=row["is_replica"],
        is_superuser=bool(row["is_superuser"]),
        bypasses_rls=bool(row["bypasses_rls"]),
        read_only_enforced=row["read_only_enforced"],
        has_pg_stat_statements=row["has_pg_stat_statements"],
        has_read_all_stats=has_read_all_stats,
    )


async def _fetch_schemas(conn: AsyncConnection) -> list[SchemaSummary]:
    cur = await conn.execute(_SCHEMAS_SQL)
    names = [r["name"] for r in await cur.fetchall()]
    if not names:
        return []

    cur = await conn.execute(_SCHEMA_SUMMARY_SQL, {"schemas": names})
    rows = {r["name"]: r for r in await cur.fetchall()}

    summaries: list[SchemaSummary] = []
    for name in names:
        row = rows.get(name)
        if row is None:
            # Schema is visible but holds no relations this role can see.
            summaries.append(SchemaSummary(name=name))
            continue
        summaries.append(
            SchemaSummary(
                name=name,
                tables=row["tables"],
                partitioned_tables=row["partitioned_tables"],
                views=row["views"],
                materialized_views=row["materialized_views"],
                rls_tables=row["rls_tables"],
                never_analyzed=row["never_analyzed"],
                unreadable=row["unreadable"],
            )
        )
    return summaries


def _build_warnings(info: ServerInfo, schemas: list[SchemaSummary]) -> list[Warning_]:
    """Conditions that degrade later pipeline steps, in severity order."""
    warnings: list[Warning_] = []

    if info.server_version_num < MIN_SUPPORTED_VERSION_NUM:
        warnings.append(
            Warning_(
                level="error",
                code="unsupported_version",
                message=(
                    f"Postgres {info.server_version_num // 10000} is below the supported "
                    "minimum of 14. Row-count estimates rely on reltuples = -1 meaning "
                    "'never analyzed', which older versions do not report."
                ),
            )
        )

    if not info.read_only_enforced:
        warnings.append(
            Warning_(
                level="error",
                code="not_read_only",
                message="default_transaction_read_only is not active on this session.",
            )
        )

    if info.is_superuser:
        warnings.append(
            Warning_(
                level="warning",
                code="superuser",
                message=(
                    "Connected as a superuser. Use a dedicated read-only role so the "
                    "pipeline cannot write even if a query is wrong."
                ),
            )
        )

    if not info.is_replica:
        warnings.append(
            Warning_(
                level="info",
                code="primary_server",
                message=(
                    "Connected to a primary. A read replica is preferred; sampling "
                    "queries are cheap but not free."
                ),
            )
        )

    if not info.has_pg_stat_statements:
        warnings.append(
            Warning_(
                level="info",
                code="no_pg_stat_statements",
                message=(
                    "pg_stat_statements is not installed. Usage mining is skipped; "
                    "the model is built from schema and statistics alone."
                ),
            )
        )
    elif not info.has_read_all_stats:
        warnings.append(
            Warning_(
                level="info",
                code="no_read_all_stats",
                message=(
                    "Without pg_read_all_stats this role sees only its own queries in "
                    "pg_stat_statements. Usage mining will be thin."
                ),
            )
        )

    never_analyzed = sum(s.never_analyzed for s in schemas)
    if never_analyzed:
        warnings.append(
            Warning_(
                level="warning",
                code="never_analyzed",
                message=(
                    f"{never_analyzed} table(s) have never been analyzed, so column "
                    "statistics are missing and profiling falls back to sampling. "
                    "Running ANALYZE on this database markedly improves the result."
                ),
            )
        )

    rls = sum(s.rls_tables for s in schemas)
    if rls and not info.bypasses_rls:
        warnings.append(
            Warning_(
                level="info",
                code="row_level_security",
                message=(
                    f"{rls} table(s) use row-level security. Their pg_stats rows are "
                    "hidden from this role, so they are profiled by sampling instead."
                ),
            )
        )
    elif rls:
        # Superusers and BYPASSRLS roles see every row, so profiling is complete --
        # but it does not match what a restricted role would see, and the model is
        # built from the wider view.
        warnings.append(
            Warning_(
                level="warning",
                code="row_level_security_bypassed",
                message=(
                    f"{rls} table(s) use row-level security, which this role bypasses. "
                    "Profiling sees every row, so the generated model may expose data a "
                    "policy-restricted role could not read. Connect as a role subject to "
                    "the policies, or plan to enforce the same rules in Cube's security "
                    "context."
                ),
            )
        )

    unreadable = sum(s.unreadable for s in schemas)
    if unreadable:
        warnings.append(
            Warning_(
                level="warning",
                code="unreadable_tables",
                message=(
                    f"{unreadable} relation(s) cannot be read by this role and will be "
                    "left out of the model entirely."
                ),
            )
        )

    return warnings


async def probe(params: ConnectionParams) -> ProbeResult:
    """Connect read-only and report what this role can see."""
    async with connect(params) as conn:
        async with phase(conn, Phase.METADATA):
            info = await _fetch_server_info(conn)
            schemas = await _fetch_schemas(conn)

    return ProbeResult(
        server=info,
        schemas=schemas,
        warnings=_build_warnings(info, schemas),
    )
