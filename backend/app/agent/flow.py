"""Question in, answer out.

Mirrors the non-hybrid pipeline in oasys: classify, pick a view, pull the
concepts out of the question, retrieve the members those concepts point at,
write SQL against that shortlist, run it, and narrate the rows. One repair
attempt, because Cube's planner errors name the members it will accept, which
makes a second try worth far more than a first guess.

Every prompt is resolved per project, so the flow is shared and the wording is
not.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.agent import render, tracing, vectors
from app.db import views_store
from app.agent.embed import embed
from app.core.config import settings
from app.generate.llm import _client  # same client construction: timeout, retries

log = logging.getLogger(__name__)

#: How many members each concept contributes to the shortlist. Small on purpose:
#: a long menu makes the model browse instead of choose.
PER_CONCEPT = 3

#: A read-only analytical query that has not answered in this long will not.
QUERY_TIMEOUT = 30


class Classification(BaseModel):
    kind: Literal["db_query", "normal", "not_permitted"]
    confidence: float = Field(ge=0.0, le=1.0)


class ViewChoice(BaseModel):
    view: str


class Concepts(BaseModel):
    metrics: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)


class GeneratedSql(BaseModel):
    sql: str


async def _ask(schema: type[BaseModel], system: str, user: str, *, label: str):
    response = await _client().chat.completions.parse(
        **tracing.generation_kwargs(label),
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=schema,
        temperature=0,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(f"{label}: the model returned nothing usable")
    return parsed


async def _say(system: str, user: str, *, label: str = "say") -> str:
    """A plain sentence, no schema."""
    response = await _client().chat.completions.create(
        **tracing.generation_kwargs(label),
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    return (response.choices[0].message.content or "").strip()


def _resolve_view(picked: str, views: list[dict[str, Any]]) -> str | None:
    """Map whatever the model returned onto a real view name.

    It is asked for the identifier and will sometimes return the title, or a
    different case. The set of valid answers is known, so this is checked rather
    than trusted -- an unmatched name would otherwise fail later as an empty
    retrieval, which looks like "no data" instead of "wrong view".
    """
    candidate = (picked or "").strip()
    names = {v["name"] for v in views}
    if candidate in names:
        return candidate

    folded = candidate.lower().replace(" ", "_").replace("-", "_")
    for view in views:
        if view["name"].lower() == folded:
            return view["name"]
        if (view.get("title") or "").strip().lower() == candidate.lower():
            return view["name"]
    return None


# --- Retrieval --------------------------------------------------------------- #


async def shortlist(project_id: str, view: str, concepts: Concepts) -> list[dict[str, Any]]:
    """The members the question is most likely to need.

    One search per concept rather than one for the whole question: "approved
    amount by insurer" is two different neighbourhoods, and a single vector
    lands between them.
    """
    metrics = [c for c in concepts.metrics if c.strip()]
    filters = [c for c in concepts.filters if c.strip()]
    terms = metrics + filters
    if not terms:
        terms = ["overview"]

    vectors_for = await embed(terms)
    by_term = dict(zip(terms, vectors_for))

    found: dict[str, dict[str, Any]] = {}
    for term in terms:
        # A metric wants a measure; a filter wants something to slice by.
        kinds = ["measure"] if term in metrics else ["dimension", "segment"]
        for hit in await vectors.search(
            project_id, by_term[term], limit=PER_CONCEPT, view=view, kinds=kinds
        ):
            name = hit.get("name")
            if not name:
                continue
            # Keep the best score a member reached for any concept.
            if name not in found or hit["score"] > found[name]["score"]:
                found[name] = hit
    return sorted(found.values(), key=lambda h: -h["score"])


def menu(members: list[dict[str, Any]]) -> str:
    """The shortlist, as the SQL prompt reads it."""
    if not members:
        return "Available schema: (nothing retrieved)"
    lines = ["Available schema:"]
    for m in members:
        description = m.get("description") or ""
        # The source cube disambiguates same-named members. A view holding both
        # claims.status and hospitalization.case_status is otherwise two
        # plausible answers to "cancelled cases", and the wrong one returns 0
        # rather than an error.
        origin = f", from {m['cube']}" if m.get("cube") else ""
        lines.append(
            f"  {m['name']} ({m.get('type')}{origin})"
            f"{' — ' + description if description else ''}"
        )
    return "\n".join(lines)


# --- Execution --------------------------------------------------------------- #


async def run_sql(sql: str) -> list[dict[str, Any]]:
    """Execute against Cube's SQL API.

    Cube plans the query against the model, so an invalid member fails here with
    an error that lists the valid ones -- which is what the repair step reads.
    """
    with tracing.observation("cube-sql", as_type="tool", input={"sql": sql}) as step:
        rows = await _run_sql(sql)
        step.update(output={"rows": len(rows)})
        return rows


async def _run_sql(sql: str) -> list[dict[str, Any]]:
    async with await psycopg.AsyncConnection.connect(
        host=settings.cube_sql_host,
        port=settings.cube_sql_port,
        dbname=settings.cube_sql_database,
        user=settings.cube_sql_user,
        password=settings.cube_sql_password,
        connect_timeout=10,
        row_factory=dict_row,
    ) as conn:
        cur = await conn.execute(sql)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


def _clean(sql: str) -> str:
    """Strip the markdown fence the model adds however firmly it is told not to."""
    text = sql.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip().rstrip(";").strip()


# --- The flow ---------------------------------------------------------------- #


async def answer(
    project_id: str, question: str, *, session_id: str | None = None
) -> dict[str, Any]:
    """Run the whole pipeline and report every step it took.

    One trace per question, which is Langfuse's guidance for a chatbot turn: the
    trace stays small enough to read, and `session_id` ties a conversation
    together without the flow having to know about history.
    """
    with tracing.attributes(session_id=session_id, user_id=project_id):
        with tracing.observation(
            "answer-question", as_type="agent", input={"question": question}
        ) as trace:
            result = await _answer(project_id, question)
            trace.update(
                output={"answer": result.get("answer"), "kind": result.get("kind")},
                metadata={
                    "view": result.get("view"),
                    "ok": result.get("ok"),
                    "project_id": project_id,
                },
            )
            return result


async def _answer(project_id: str, question: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def note(step: str, **detail: Any) -> None:
        steps.append({"step": step, **detail})

    # 1. Gatekeeper.
    classification: Classification = await _ask(
        Classification,
        await render.resolve(project_id, "classifier"),
        question,
        label="classify",
    )
    note("classify", kind=classification.kind, confidence=classification.confidence)

    if classification.kind == "not_permitted":
        return {
            "kind": "not_permitted",
            "answer": await render.resolve(project_id, "not_permitted"),
            "steps": steps,
        }

    if classification.kind == "normal":
        reply = await _say(
            await render.resolve(project_id, "chat"), question, label="chat"
        )
        return {"kind": "normal", "answer": reply, "steps": steps}

    # 2. Which view.
    choice: ViewChoice = await _ask(
        ViewChoice,
        await render.resolve(project_id, "view_select"),
        question,
        label="select_view",
    )
    saved_views = await views_store.list_for(project_id)
    view = _resolve_view(choice.view, saved_views)
    if view is None:
        note("select_view", returned=choice.view, resolved=None)
        return {
            "kind": "db_query",
            "answer": (
                f"I could not match \"{choice.view.strip()}\" to a view in this "
                "project. The views are: "
                + ", ".join(v["name"] for v in saved_views)
                + "."
            ),
            "steps": steps,
            "ok": False,
        }
    note("select_view", view=view, returned=choice.view)

    # 3. What the question is asking for.
    concepts: Concepts = await _ask(
        Concepts,
        await render.resolve(project_id, "concepts"),
        question,
        label="concepts",
    )
    note("concepts", metrics=concepts.metrics, filters=concepts.filters)

    # 4. Which members those concepts point at.
    with tracing.observation(
        "retrieve-members",
        input={"view": view, "metrics": concepts.metrics, "filters": concepts.filters},
    ) as step:
        members = await shortlist(project_id, view, concepts)
        step.update(output={"members": [m["name"] for m in members]})
    note("retrieve", members=[m["name"] for m in members])
    if not members:
        return {
            "kind": "db_query",
            "view": view,
            "answer": (
                f"Nothing in the index matches that question for {view}. "
                "Sync the index on the Views page, or rephrase the question."
            ),
            "steps": steps,
            "ok": False,
        }

    schema = menu(members)

    # 5. Write the SQL.
    today = f"TODAY is {date.today().isoformat()}.\n\n"
    sql_system = (
        today
        + await render.resolve(project_id, "cube_sql", target_view_name=view)
        + "\n\n"
        + schema
    )
    generated: GeneratedSql = await _ask(GeneratedSql, sql_system, question, label="sql")
    sql = _clean(generated.sql)
    note("sql", sql=sql)

    # 6. Run it, and repair once if Cube rejects it.
    try:
        rows = await run_sql(sql)
        note("execute", rows=len(rows))
    except Exception as first:  # noqa: BLE001 - the message is the repair input
        error = str(first).strip()
        note("execute_failed", error=error[:400])

        repair_system = (
            today
            + await render.resolve(project_id, "repair")
            + "\n\n"
            + await render.resolve(project_id, "cube_sql", target_view_name=view)
            + "\n\n"
            + schema
        )
        retry: GeneratedSql = await _ask(
            GeneratedSql,
            repair_system,
            f"{question}\n\nYour previous SQL failed.\n\nSQL:\n{sql}\n\nError:\n{error}",
            label="repair",
        )
        sql = _clean(retry.sql)
        note("repair", sql=sql)
        try:
            rows = await run_sql(sql)
            note("execute", rows=len(rows))
        except Exception as second:  # noqa: BLE001
            note("execute_failed", error=str(second)[:400])
            return {
                "kind": "db_query",
                "view": view,
                "sql": sql,
                "answer": "I could not build a query for that question against this model.",
                "error": str(second)[:400],
                "steps": steps,
                "ok": False,
            }

    # 7. Say it in words.
    narration = await _say(
        await render.resolve(project_id, "answer"),
        f"Question: {question}\nResult rows: {rows[:50]}",
        label="narrate-answer",
    )
    return {
        "kind": "db_query",
        "view": view,
        "sql": sql,
        "rows": rows[:50],
        "answer": narration,
        "steps": steps,
        "ok": True,
    }
