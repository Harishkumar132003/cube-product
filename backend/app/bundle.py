"""The evidence bundle (plan.md step 7).

One JSON document per generation run, and the only interface between the
Postgres adapter and everything downstream. Supporting another engine later
means producing this same shape.

Two properties hold throughout:

* **Every fact carries provenance and confidence.** ``declared`` came from the
  catalog, ``sampled`` from statistics, ``inferred`` from a heuristic,
  ``user`` from something a person wrote. The repair agent may overturn
  inferred facts; it may never overturn declared or user ones.
* **Redaction already happened.** Column values reaching here were filtered by
  the profiler. Nothing in this module re-reads the database, so it cannot
  reintroduce raw values.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

Provenance = Literal["declared", "sampled", "inferred", "user"]

BUNDLE_VERSION = 1

# Framework bookkeeping. Never part of a semantic model.
JUNK_TABLE_NAMES = {
    "schema_migrations", "django_migrations", "alembic_version",
    "flyway_schema_history", "ar_internal_metadata", "migrations",
    "sessions", "django_session", "cache_entries", "jobs", "delayed_jobs",
    "celery_taskmeta", "celery_tasksetmeta", "oban_jobs", "good_jobs",
}
JUNK_TABLE_PATTERNS = re.compile(
    r"(^|_)(migrations?|schema_version|cache|session|job_queue)($|_)", re.IGNORECASE
)

SOFT_DELETE_COLUMNS = {"deleted_at", "is_deleted", "deleted", "archived_at", "is_archived"}
TECHNICAL_PATTERN = re.compile(
    r"(password|token|secret|salt|digest|hash|api_key|private_key|otp)", re.IGNORECASE
)
TENANT_PATTERN = re.compile(
    r"^(org|organisation|organization|tenant|account|company|workspace|hospital|"
    r"client|customer)_id$",
    re.IGNORECASE,
)
MONEY_PATTERN = re.compile(r"(_cents|_paise|_minor_units)$", re.IGNORECASE)
TIME_HINTS = ("timestamp", "date")

# A column must appear on at least this share of tables to read as a tenant key.
TENANT_SHARE = 0.5


def _fact(value: Any, provenance: Provenance, confidence: float = 1.0, **extra: Any) -> dict:
    return {"value": value, "provenance": provenance, "confidence": confidence, **extra}


def _is_junk(name: str) -> bool:
    return name in JUNK_TABLE_NAMES or bool(JUNK_TABLE_PATTERNS.search(name))


def _time_columns(table: dict) -> list[str]:
    return [
        c["name"]
        for c in table["columns"]
        if any(h in c["data_type"].lower() for h in TIME_HINTS)
    ]


def _default_time_dimension(table: dict) -> str | None:
    """created_at wins; otherwise the first temporal column, if any."""
    times = _time_columns(table)
    for preferred in ("created_at", "created", "inserted_at"):
        if preferred in times:
            return preferred
    return times[0] if times else None


def _detect_tenant_column(tables: list[dict]) -> dict | None:
    """A column carried by most tables that names the owning organisation.

    Flagged for Cube's security context rather than modelled as a plain
    dimension: grouping by it would expose the shape of other tenants' data.
    """
    modelled = [t for t in tables if t["kind"] in ("table", "partitioned_table")]
    if len(modelled) < 3:
        return None

    counts: dict[str, int] = {}
    for table in modelled:
        for column in table["columns"]:
            if TENANT_PATTERN.match(column["name"]):
                counts[column["name"]] = counts.get(column["name"], 0) + 1
    if not counts:
        return None

    name, seen = max(counts.items(), key=lambda kv: kv[1])
    share = seen / len(modelled)
    if share < TENANT_SHARE:
        return None
    return {
        "column": name,
        "tables": [
            t["qualified_name"]
            for t in modelled
            if any(c["name"] == name for c in t["columns"])
        ],
        "share": round(share, 2),
        "confidence": round(min(0.95, 0.5 + share / 2), 2),
    }


def _column_facts(table: dict, column: dict) -> dict:
    """One column, with whatever is known about it and where that came from."""
    stats = column.get("stats") or {}
    entry: dict[str, Any] = {
        "name": column["name"],
        "data_type": _fact(column["data_type"], "declared"),
        "nullable": _fact(not column["not_null"], "declared"),
        "indexed": _fact(column["indexed"], "declared"),
    }
    if column.get("comment"):
        entry["comment"] = _fact(column["comment"], "user")

    if stats:
        entry["null_fraction"] = _fact(stats.get("null_fraction"), "sampled", 0.8)
        entry["distinct"] = _fact(stats.get("distinct"), "sampled", 0.7)
        if stats.get("unique"):
            entry["unique"] = _fact(True, "sampled", 0.8)
        if stats.get("range"):
            entry["range"] = _fact(stats["range"], "sampled", 0.7)
        if stats.get("pii_suspected"):
            entry["pii"] = _fact(True, "inferred", 0.6, basis="name pattern")

    # Enum values: declared beats sampled.
    if column.get("enum_values"):
        entry["vocabulary"] = _fact(column["enum_values"], "declared", 1.0)
    elif stats.get("values"):
        entry["vocabulary"] = _fact(
            [v["value"] for v in stats["values"]], "sampled", 0.7,
            basis="most common values",
        )

    if TECHNICAL_PATTERN.search(column["name"]):
        entry["technical"] = _fact(True, "inferred", 0.8, action="hide")
    if MONEY_PATTERN.search(column["name"]):
        entry["minor_units"] = _fact(True, "inferred", 0.7, action="divide by 100")

    return entry


def _is_evidence(table: dict) -> bool:
    """A view is modelled as evidence rather than as a cube."""
    return table["kind"] in ("view", "materialized_view")


def build(
    *,
    project: dict,
    scan: dict,
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the bundle.

    ``include`` overrides the project's stored selection for this run, which is
    how a generation can be narrowed to a few tables for testing without
    changing what the project is configured to model.
    """
    catalog = scan["catalog"]
    all_tables = catalog["tables"]
    all_names = [t["qualified_name"] for t in all_tables]

    # Database views are evidence, never cubes, so they are not something to
    # pick: their definitions are the best record of which join paths people
    # actually traverse, and dropping them would blind the join step. They stay
    # in scope whatever the user selected, and stay out of the selection count.
    view_names = {t["qualified_name"] for t in all_tables if _is_evidence(t)}
    selectable = [n for n in all_names if n not in view_names]

    stored = project.get("selected_tables") or []
    chosen = include if include is not None else (stored or selectable)
    chosen_set = {n for n in chosen if n in all_names} - view_names

    notes = project.get("table_notes") or {}
    in_scope = [
        t for t in all_tables
        if t["qualified_name"] in chosen_set or t["qualified_name"] in view_names
    ]

    # --- Tables -----------------------------------------------------------
    tables: list[dict] = []
    excluded: list[dict] = []

    for table in in_scope:
        if _is_junk(table["name"]):
            excluded.append({"table": table["qualified_name"], "reason": "framework bookkeeping"})
            continue
        if not table["readable"]:
            excluded.append({"table": table["qualified_name"], "reason": "not readable by this role"})
            continue

        entry: dict[str, Any] = {
            "name": table["qualified_name"],
            "kind": table["kind"],
            # A database view is evidence of intended joins, not a cube: the
            # same grain reached twice would double-count.
            "model_as": "evidence" if _is_evidence(table) else "cube",
            "rows": _fact(table["rows"], "declared", 0.7 if table["rows"] is not None else 0.0,
                          basis=table["row_basis"]),
            "columns": [_column_facts(table, c) for c in table["columns"]],
        }

        if table["primary_key"]:
            entry["primary_key"] = _fact(table["primary_key"], "declared")
            if len(table["primary_key"]) > 1:
                # Cube needs a single key, so the renderer concatenates.
                entry["composite_key"] = _fact(True, "declared", 1.0, action="concatenated dimension")
        else:
            entry["primary_key"] = _fact(None, "declared", 1.0, note="no primary key declared")

        if table["unique"]:
            entry["unique"] = _fact(table["unique"], "declared")
        if table.get("comment"):
            entry["comment"] = _fact(table["comment"], "user")
        if notes.get(table["qualified_name"], {}).get("purpose"):
            entry["purpose"] = _fact(notes[table["qualified_name"]]["purpose"], "user")
        if table["rls"]:
            entry["row_level_security"] = _fact(True, "declared")
        if table["rows"] == 0:
            entry["empty"] = _fact(True, "declared", 1.0, note="would produce a cube returning nothing")
        if table.get("view_definition"):
            entry["view_definition"] = _fact(table["view_definition"], "declared")

        soft = [c["name"] for c in table["columns"] if c["name"] in SOFT_DELETE_COLUMNS]
        if soft:
            entry["soft_delete"] = _fact(soft[0], "inferred", 0.85, action="filter in base sql")

        time_dim = _default_time_dimension(table)
        if time_dim:
            entry["default_time_dimension"] = _fact(time_dim, "inferred", 0.7)

        tables.append(entry)

    # --- Relationships ----------------------------------------------------
    relationships: list[dict] = []
    dangling: list[dict] = []
    unique_cols = {
        t["qualified_name"]: {tuple(u) for u in t["unique"]} | (
            {tuple(t["primary_key"])} if t["primary_key"] else set()
        )
        for t in all_tables
    }

    for table in in_scope:
        for fk in table["foreign_keys"]:
            target = fk["target"]
            if target not in chosen_set:
                dangling.append(
                    {
                        "child": table["qualified_name"],
                        "columns": fk["columns"],
                        "target": target,
                        "reason": "target not in scope",
                    }
                )
                continue
            # The child side is unique exactly when its columns are a key.
            child_unique = tuple(fk["columns"]) in unique_cols.get(table["qualified_name"], set())
            relationships.append(
                {
                    "child": table["qualified_name"],
                    "child_columns": fk["columns"],
                    "parent": target,
                    "parent_columns": fk["target_columns"],
                    "cardinality": _fact(
                        "one_to_one" if child_unique else "many_to_one", "declared", 0.95
                    ),
                    "provenance": "declared",
                    "confidence": 1.0,
                }
            )

    # --- Heuristics -------------------------------------------------------
    heuristics: list[dict] = []
    tenant = _detect_tenant_column(in_scope)
    if tenant:
        heuristics.append(
            {
                "kind": "tenant_column",
                "column": tenant["column"],
                "tables": tenant["tables"],
                "provenance": "inferred",
                "confidence": tenant["confidence"],
                "action": "security context, not a dimension",
                "basis": f"present on {tenant['share'] * 100:.0f}% of tables in scope",
            }
        )

    vocabularies = [
        {
            "column": f'{t["name"]}.{c["name"]}',
            "values": c["vocabulary"]["value"],
            "provenance": c["vocabulary"]["provenance"],
            "confidence": c["vocabulary"]["confidence"],
        }
        for t in tables
        for c in t["columns"]
        if "vocabulary" in c
    ]

    # --- Context ----------------------------------------------------------
    context: dict[str, Any] = {}
    if (project.get("business_context") or "").strip():
        context["business_flow"] = _fact(project["business_context"].strip(), "user")
    described = [t for t in tables if "purpose" in t]
    context["tables_described"] = len(described)

    return {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "project": {"id": project["id"], "name": project["name"]},
        "database": {
            "engine": "postgres",
            "schemas": scan["schemas"],
            "scanned_at": scan["created_at"].isoformat()
            if hasattr(scan["created_at"], "isoformat")
            else scan["created_at"],
        },
        "scope": {
            "selected": sorted(chosen_set),
            "excluded": excluded,
            "narrowed_for_this_run": include is not None,
            "total_available": len(selectable),
        },
        "tables": tables,
        "relationships": relationships,
        "dangling_foreign_keys": dangling,
        "vocabularies": vocabularies,
        "heuristics": heuristics,
        "context": context,
        "counts": {
            "tables": sum(1 for t in tables if t["model_as"] == "cube"),
            "views_as_evidence": sum(1 for t in tables if t["model_as"] == "evidence"),
            "columns": sum(len(t["columns"]) for t in tables),
            "relationships": len(relationships),
            "dangling": len(dangling),
            "vocabularies": len(vocabularies),
            "described": len(described),
        },
    }
