"""Column profiling from pg_stats (plan.md step 3), redacted at construction.

pg_stats covers most of what profiling needs without reading a single table
row. Two things it will not give you:

* nothing at all when the table was never analyzed, or when row-level security
  hides it from this role;
* anything safe to keep verbatim. ``most_common_vals`` and ``histogram_bounds``
  hold real values, so they are reduced here and never stored raw. The bundle
  gets logged and cached, so redaction happens where the data is built, not
  where a prompt is assembled.
"""

from __future__ import annotations

import re
from typing import Any

from app.db.session import ConnectionParams, Phase, connect, phase

# A column is treated as enum-like, and so safe to keep values for, only when
# the values genuinely repeat. Low distinct counts prove nothing on a small
# table: with two rows, every column looks like an enum, including identifiers.
ENUM_MAX_DISTINCT = 25
ENUM_MAX_LENGTH = 40
# Below this many rows, "few distinct values" carries no information.
ENUM_MIN_ROWS = 20
# Values must repeat: distinct may be at most this fraction of the rows.
ENUM_MAX_DISTINCT_RATIO = 0.5

# Name-pattern matching is a blunt instrument, so it is deliberately broad:
# a withheld status code costs nothing, a leaked identifier is unrecoverable.
PII_PATTERN = re.compile(
    r"(email|e_mail|phone|mobile|contact|ssn|aadhaar|pan_no|passport|address|"
    r"dob|birth|age|gender|occupation|name|password|token|secret|salt|digest|"
    r"otp|uhid|mrn|policy|insur|card|employee|account_no|acct|licen|"
    r"tax|nominee|guardian|relative)",
    re.IGNORECASE,
)

_STATS_SQL = """
SELECT
    schemaname                AS schema,
    tablename                 AS table,
    attname                   AS column,
    null_frac,
    n_distinct,
    avg_width,
    most_common_vals::text::text[]  AS mcv,
    most_common_freqs         AS mcf,
    histogram_bounds::text::text[]  AS histogram
FROM pg_stats
WHERE schemaname = ANY(%(schemas)s)
"""


def _distinct_count(n_distinct: float | None, rows: int | None) -> int | None:
    """pg_stats reports a negative n_distinct as a ratio of the row count."""
    if n_distinct is None:
        return None
    if n_distinct >= 0:
        return int(n_distinct)
    if rows is None:
        return None
    return int(round(-n_distinct * rows))


def _enum_like(
    values: list[str] | None,
    distinct: int | None,
    rows: int | None,
    unique: bool,
    column_name: str,
    data_type: str,
) -> bool:
    """Whether a column's values are a code set rather than data.

    Every condition has to hold. Erring towards withholding is the right
    trade: a status code kept out of the bundle is a small loss, an
    identifier written into it is a leak that survives in logs and caches.
    """
    if not values or unique:
        return False
    lowered_type = data_type.lower()
    if any(hint in lowered_type for hint in _NEVER_ENUM_TYPES):
        return False
    # A key is an identifier whatever its cardinality looks like.
    if column_name == "id" or column_name.endswith(_ID_SUFFIXES):
        return False
    if distinct is None or distinct > ENUM_MAX_DISTINCT:
        return False
    # Without a trustworthy row count there is no way to tell a code set from
    # a tiny table full of identifiers.
    if rows is None or rows < ENUM_MIN_ROWS:
        return False
    if distinct > rows * ENUM_MAX_DISTINCT_RATIO:
        return False
    return all(len(v) <= ENUM_MAX_LENGTH for v in values)


def _bounds(mcv: list[str] | None, histogram: list[str] | None) -> tuple[str, str] | None:
    """Approximate min and max.

    Taken across both arrays: the most common values are excluded from the
    histogram, so either one alone can miss an extreme.
    """
    pool = [v for v in (list(mcv or []) + list(histogram or [])) if v is not None]
    if not pool:
        return None
    return min(pool), max(pool)


# Types whose values are never a code set: identifiers, instants, flags.
# Money and measures are quantities, never code sets. Small integers can be
# status codes, so plain int types stay eligible.
_NEVER_ENUM_TYPES = (
    "uuid", "date", "time", "timestamp", "interval", "bool", "json", "bytea",
    "numeric", "decimal", "money", "real", "double",
)

# Business identifiers: unique-ish strings that are not a vocabulary.
_ID_SUFFIXES = ("_id", "_number", "_no", "_ref", "_code_no", "_uuid")

_NUMERIC_HINTS = ("int", "numeric", "decimal", "real", "double", "money", "serial")
_TEMPORAL_HINTS = ("date", "time", "timestamp")


def _is_rangeable(data_type: str) -> bool:
    lowered = data_type.lower()
    return any(h in lowered for h in _NUMERIC_HINTS + _TEMPORAL_HINTS)


async def profile(
    params: ConnectionParams, schemas: list[str], catalog: dict[str, Any]
) -> dict[str, Any]:
    """Attach redacted column statistics to a catalog produced by catalog.extract."""
    async with connect(params) as conn:
        async with phase(conn, Phase.PG_STATS):
            cur = await conn.execute(_STATS_SQL, {"schemas": schemas})
            rows = await cur.fetchall()

    stats = {(r["schema"], r["table"], r["column"]): r for r in rows}

    profiled = 0
    missing = 0
    redacted = 0

    for table in catalog["tables"]:
        for column in table["columns"]:
            key = (table["schema"], table["name"], column["name"])
            row = stats.get(key)
            if row is None:
                # Never analyzed, or hidden from this role by RLS.
                column["stats"] = None
                missing += 1
                continue

            distinct = _distinct_count(row["n_distinct"], table["rows"])
            is_pii = bool(PII_PATTERN.search(column["name"]))
            summary: dict[str, Any] = {
                "null_fraction": round(row["null_frac"], 4) if row["null_frac"] is not None else None,
                "distinct": distinct,
                "distinct_is_ratio": (row["n_distinct"] or 0) < 0,
                "unique": row["n_distinct"] == -1,
                "avg_width": row["avg_width"],
                "pii_suspected": is_pii,
            }

            if is_pii:
                # Statistics only: no values, not even enum-like ones.
                summary["values"] = None
                summary["range"] = None
                redacted += 1
            else:
                if _enum_like(
                    row["mcv"],
                    distinct,
                    table["rows"],
                    summary["unique"],
                    column["name"],
                    column["data_type"],
                ):
                    freqs = list(row["mcf"] or [])
                    summary["values"] = [
                        {"value": v, "frequency": round(freqs[i], 4) if i < len(freqs) else None}
                        for i, v in enumerate(row["mcv"] or [])
                    ]
                else:
                    summary["values"] = None
                    if row["mcv"]:
                        redacted += 1
                # Derived bounds only. The raw arrays never leave this function.
                summary["range"] = (
                    dict(zip(("min", "max"), _bounds(row["mcv"], row["histogram"]) or ()))
                    or None
                    if _is_rangeable(column["data_type"])
                    else None
                )

            column["stats"] = summary
            profiled += 1

    catalog["profile"] = {
        "columns_profiled": profiled,
        "columns_without_stats": missing,
        "columns_redacted": redacted,
    }
    return catalog
