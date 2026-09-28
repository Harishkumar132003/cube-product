"""The generation steps (plan.md step 8).

Several narrow calls rather than one large one. Each gets only the evidence it
needs and returns JSON validated against a schema in app.generate.ir, so the
model's output is checked before it reaches the renderer.

The rules below are repeated in each prompt on purpose: they are the ones that
produce silently wrong numbers when broken.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypeVar

from openai import APITimeoutError, AsyncOpenAI

from app.agent import tracing
from pydantic import BaseModel

from app.core.config import settings
from app.generate import topology
from app.generate.ir import (
    ClassifyResult,
    Cube,
    CubeResult,
    JoinDecision,
    JoinResult,
    Model,
    Question,
    TableClass,
    ViewResult,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SYSTEM = """You design Cube semantic layers from evidence about a Postgres database.

Rules that matter more than anything else:
- Work only from the evidence given. Never invent a table, column or value.
- Respect provenance. A fact marked `declared` came from the catalog and is
  certain. `user` came from a person and outranks your own judgement about
  meaning. `inferred` and `sampled` are evidence, not truth.
- Never sum a price, rate, ratio, percentage or identifier. Summing a unit
  price is meaningless; summing an id is nonsense.
- A column marked as the tenant column is a security boundary, not a
  dimension. Leave it out of the public surface.
- Columns marked technical or pii must be `public: false` if modelled at all.
- When the evidence does not settle something that changes a number, do not
  guess: return it as a question.
"""


#: A single structured call on a wide table can legitimately take a minute. The
#: SDK's default is generous enough to look like a hang, and too short for the
#: largest cubes, so both the ceiling and the retries are set explicitly.
REQUEST_TIMEOUT = 180.0
MAX_RETRIES = 3

#: Called with (key, label, state, detail) as each step starts and finishes.
Progress = Callable[..., None]


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _plural(count: int, noun: str) -> str:
    """Progress lines are read by the user, so "1 measures" will not do."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _client() -> AsyncOpenAI:
    if not settings.openai_configured:
        raise RuntimeError("CUBEGEN_OPENAI_API_KEY is not set")
    # Traced when Langfuse is configured, plain otherwise. Same interface.
    return tracing.openai_client_class()(
        api_key=settings.openai_api_key,
        timeout=REQUEST_TIMEOUT,
        max_retries=MAX_RETRIES,
    )


async def _ask(schema: type[T], prompt: str, *, label: str) -> T:
    """One structured call. Raises if the model returns nothing usable."""
    try:
        response = await _client().chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format=schema,
            temperature=0,
        )
    except APITimeoutError as exc:
        # The SDK has already retried; say which step gave up and for how long,
        # because "Request timed out" alone tells the user nothing actionable.
        raise RuntimeError(
            f"{label}: the model did not respond within {REQUEST_TIMEOUT:.0f}s "
            f"after {MAX_RETRIES} attempts"
        ) from exc
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(f"{label}: model returned no parsable output")
    log.info(
        "%s: %s tokens", label, getattr(response.usage, "total_tokens", "?")
    )
    return parsed


# --- Evidence projections ----------------------------------------------- #
# Each step gets a trimmed view of the bundle. Sending the whole thing to
# every call wastes context and buries the fact that matters.


def _table_digest(table: dict[str, Any]) -> str:
    bits = [f"{table['name']} ({table['kind']}, model_as={table['model_as']})"]
    rows = table["rows"]["value"]
    bits.append(f"rows={rows if rows is not None else 'unknown'}")
    if table.get("purpose"):
        bits.append(f"purpose[user]={table['purpose']['value']}")
    if table.get("primary_key", {}).get("value"):
        bits.append(f"pk={','.join(table['primary_key']['value'])}")
    if table.get("empty"):
        bits.append("EMPTY")
    if table.get("row_level_security"):
        bits.append("rls")
    return " | ".join(bits)


def _column_digest(column: dict[str, Any]) -> str:
    bits = [f"{column['name']}:{column['data_type']['value']}"]
    if column.get("unique"):
        bits.append("unique")
    if not column["nullable"]["value"]:
        bits.append("not_null")
    if column.get("vocabulary"):
        v = column["vocabulary"]
        bits.append(f"values[{v['provenance']}]={v['value']}")
    if column.get("range"):
        r = column["range"]["value"]
        bits.append(f"range={r.get('min')}..{r.get('max')}")
    if column.get("distinct", {}).get("value") is not None:
        bits.append(f"distinct={column['distinct']['value']}")
    if column.get("pii"):
        bits.append("PII")
    if column.get("technical"):
        bits.append("TECHNICAL")
    if column.get("minor_units"):
        bits.append("MINOR_UNITS")
    if column.get("comment"):
        bits.append(f"comment={column['comment']['value']}")
    return "  - " + " | ".join(bits)


def _context_block(bundle: dict[str, Any]) -> str:
    context = bundle.get("context", {})
    flow = context.get("business_flow")
    heuristics = "\n".join(
        f"- {h['kind']}: {h['column']} ({h['basis']}) -> {h['action']}"
        for h in bundle.get("heuristics", [])
    )
    parts = []
    if flow:
        parts.append(f"BUSINESS CONTEXT (provenance: user, authoritative on meaning)\n{flow['value']}")
    if heuristics:
        parts.append(f"HEURISTICS (inferred)\n{heuristics}")
    return "\n\n".join(parts)


# --- Steps --------------------------------------------------------------- #


async def classify(bundle: dict[str, Any]) -> ClassifyResult:
    tables = "\n".join(_table_digest(t) for t in bundle["tables"])
    rels = "\n".join(
        f"- {r['child']}.{','.join(r['child_columns'])} -> {r['parent']} ({r['cardinality']['value']})"
        for r in bundle["relationships"]
    )
    prompt = f"""Classify each relation.

fact       measured events or transactions, the thing counted
dimension  descriptive lookups joined for context
bridge     pure join table, two foreign keys and little else
staging    scratch, import or intermediate data
audit      history and change-log tables
ignore     framework bookkeeping, or nothing worth modelling

A relation with model_as=evidence is a database view. Classify it `ignore`:
its definition informs joins and measures, but modelling it as a cube would
reach the same grain twice and double-count.

TABLES
{tables}

DECLARED RELATIONSHIPS
{rels}

{_context_block(bundle)}"""
    return await _ask(ClassifyResult, prompt, label="classify")


async def decide_joins(
    bundle: dict[str, Any], classifications: list[TableClass]
) -> JoinResult:
    modelled = {c.table for c in classifications if c.classification not in ("ignore", "staging")}
    rels = "\n".join(
        f"- {r['child']}.{','.join(r['child_columns'])} -> {r['parent']}.{','.join(r['parent_columns'])}"
        f" (cardinality {r['cardinality']['value']}, provenance {r['provenance']})"
        for r in bundle["relationships"]
        if r["child"] in modelled and r["parent"] in modelled
    )
    views = "\n\n".join(
        f"-- {t['name']}\n{t['view_definition']['value']}"
        for t in bundle["tables"]
        if t.get("view_definition")
    )
    prompt = f"""Decide which cube declares each join, and its relationship.

In Cube the declaring cube is the LEFT side of a LEFT JOIN, and joins do not
auto-reverse. Direction therefore decides which cube can be the query root.

Two valid shapes:
- Star: the fact declares `many_to_one` out to its dimensions.
- Hub and spoke: one anchor declares every path, including `one_to_many` down
  to its children, so queries can bridge between children.

Pick the shape that matches the evidence. The hand-written view definitions
below show which paths people actually traverse.

Write sql as "{{CUBE}}.child_column = {{target_cube_name}}.parent_column".

CUBES BEING MODELLED
{chr(10).join(sorted(modelled))}

DECLARED RELATIONSHIPS
{rels}

VIEW DEFINITIONS (evidence of intended joins)
{views or '(none)'}

{_context_block(bundle)}"""
    return await _ask(JoinResult, prompt, label="joins")


async def model_cube(
    bundle: dict[str, Any], table: dict[str, Any], classification: str
) -> CubeResult:
    columns = "\n".join(_column_digest(c) for c in table["columns"])
    time_dim = table.get("default_time_dimension", {}).get("value")
    prompt = f"""Model this one table as a Cube cube.

TABLE
{_table_digest(table)}
classification: {classification}
suggested default time dimension: {time_dim or '(none found)'}

COLUMNS
{columns}

Guidance:
- Exactly one dimension must be primary_key: true. If the table has no primary
  key, say so as a question rather than inventing one.
- Every column with a `values[...]` list is a status vocabulary. Make it a
  string dimension, and add one filtered `count` measure per meaningful state
  using filters: [{{sql: "{{CUBE}}.col = 'VALUE'"}}]. Do not invent states that
  are not in the list.
- Timestamps become type: time dimensions.
- Amounts become sum and avg measures. Never sum a rate, ratio or identifier.
- Columns marked PII or TECHNICAL: public: false, or leave out entirely.
- Columns marked MINOR_UNITS are stored in minor units; divide by 100.0.
- Always include a plain `count` measure.
- Descriptions are read by AI agents through Cube's Meta API. Write them to
  disambiguate: say what the number is, its unit, and what it is not.

{_context_block(bundle)}"""
    return await _ask(CubeResult, prompt, label=f"cube:{table['name']}")


async def propose_views(bundle: dict[str, Any], cubes: list[Cube], joins: list[JoinDecision]) -> ViewResult:
    inventory = "\n".join(
        f"- {c.name} (from {c.table})\n"
        f"    dimensions: {', '.join(d.name for d in c.dimensions)}\n"
        f"    measures:   {', '.join(m.name for m in c.measures)}"
        for c in cubes
    )
    join_lines = "\n".join(
        f"- {j.declaring_cube} -> {j.target_cube} ({j.relationship})" for j in joins
    )
    views = "\n\n".join(
        f"-- {t['name']}\n{t['view_definition']['value']}"
        for t in bundle["tables"]
        if t.get("view_definition")
    )
    prompt = f"""Propose the business-facing views.

A Cube view has no SQL. It selects members from cubes along a join_path, e.g.
join_path "hospitalization.claims" reaching a claims member from the anchor.

Use only member names that exist below. The database views, where present,
show what people already assemble by hand -- mirror those first, then add at
most two more if the evidence supports them.

CUBES
{inventory}

JOINS
{join_lines}

DATABASE VIEWS (what people already built)
{views or '(none)'}

{_context_block(bundle)}"""
    return await _ask(ViewResult, prompt, label="views")


# --- Orchestration ------------------------------------------------------- #


def _dedupe_questions(questions: list[Question]) -> list[Question]:
    """Each cube step re-raises the same cross-cutting question.

    Collapse on the normalised text so the review screen asks once about the
    date that decides when a claim counts, not once per cube.
    """
    seen: dict[str, Question] = {}
    for question in questions:
        key = "".join(ch for ch in question.question.lower() if ch.isalnum())
        seen.setdefault(key, question)
    return list(seen.values())


async def generate(
    bundle: dict[str, Any],
    *,
    max_parallel: int = 4,
    include_views: bool = False,
    progress: Progress | None = None,
) -> Model:
    """Run the steps in order and assemble the IR.

    Views are off by default. Which business surfaces are worth having, and
    which cubes belong in each, is a decision the user makes -- a model asked
    to guess produces one view per cube, which is no view at all.
    """
    report = progress or _noop

    count = len(bundle["tables"])
    report("classify", "Classifying tables", "running", _plural(count, "relation"))
    classification = await classify(bundle)
    by_table = {c.table: c for c in classification.tables}
    kept = [c for c in classification.tables if c.classification not in ("ignore", "staging")]
    report(
        "classify",
        "Classifying tables",
        "done",
        f"{len(kept)} of {count} will be modelled",
    )

    report("joins", "Deciding joins", "running")
    joins = await decide_joins(bundle, classification.tables)
    report("joins", "Deciding joins", "done", _plural(len(joins.joins), "join"))

    modelled = [
        t
        for t in bundle["tables"]
        if t["model_as"] == "cube"
        and by_table.get(t["name"]) is not None
        and by_table[t["name"]].classification not in ("ignore", "staging")
    ]

    # Each cube is independent, so they run together under a small cap. Each
    # reports separately, so a slow one is visible rather than hidden inside a
    # single "modelling cubes" line.
    gate = asyncio.Semaphore(max_parallel)

    async def one(table: dict[str, Any]) -> CubeResult:
        key = f"cube:{table['name']}"
        label = f"Modelling {table['name']}"
        async with gate:
            report(key, label, "running")
            result = await model_cube(
                bundle, table, by_table[table["name"]].classification
            )
        report(
            key,
            label,
            "done",
            f"{_plural(len(result.cube.dimensions), 'dimension')}, "
            f"{_plural(len(result.cube.measures), 'measure')}",
        )
        return result

    results = await asyncio.gather(*(one(t) for t in modelled))
    cubes = [r.cube for r in results]

    # Direction is a fact about the graph, not a judgement, so it is settled
    # deterministically rather than left to the model: a view has to reach
    # every member from one root cube.
    report("orient", "Orienting joins", "running")
    root, oriented, unreachable = topology.orient([c.table for c in cubes], joins.joins)
    inverted = sum(
        1
        for a, b in zip(joins.joins, oriented)
        if a.declaring_cube != b.declaring_cube
    )
    joins.joins = oriented
    detail = f"root {root.split('.')[-1]}" if root else "no root"
    if inverted:
        detail += f", {_plural(inverted, 'join')} redirected"
    if unreachable:
        detail += f", {len(unreachable)} unreachable"
    report("orient", "Orienting joins", "done", detail)

    if include_views:
        report("views", "Proposing views", "running")
        views = await propose_views(bundle, cubes, joins.joins)
        report("views", "Proposing views", "done", _plural(len(views.views), "view"))
    else:
        views = ViewResult(views=[], questions=[])

    questions = _dedupe_questions(
        [
            *classification.questions,
            *joins.questions,
            *[q for r in results for q in r.questions],
            *views.questions,
        ]
    )

    return Model(
        cubes=cubes,
        views=views.views,
        joins=joins.joins,
        classifications=classification.tables,
        questions=questions,
    )
