# Cube semantic layer generator

Connect a Postgres database, get a reviewed Cube data model. See [plan.md](plan.md)
for the pipeline design.

## Layout

| Path | What |
|---|---|
| [docker/](docker/) | Standalone Cube (playground + SQL API) |
| [backend/](backend/) | FastAPI service: safe connection, catalog extraction, profiling |
| `frontend/` | React + TypeScript UI (not started) |

## Running

### Cube

```bash
cd docker
cp .env.example .env     # fill in the database Cube should read
docker compose up -d
```

- Playground: http://localhost:4000
- SQL API (Postgres wire): `localhost:15432`
- Generated model is mounted from `docker/cube/model/`, which is empty until the
  renderer writes to it.

`docker/.env` is gitignored and holds database credentials. On Linux the
container reaches a host database through `host.docker.internal`, which
`compose.yaml` maps explicitly via `extra_hosts`.

### Backend

```bash
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- Health: `GET /health`
- Docs: http://localhost:8000/docs

## API

A **project** is the top-level object. It owns exactly one connection, and will
own the evidence bundle and generated model built from it.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/bootstrap` | What to show on load: projects, active one, OpenAI status |
| GET, POST | `/api/projects` | List, create |
| GET, PATCH, DELETE | `/api/projects/{id}` | Read, rename, delete (cascades to the connection) |
| PUT | `/api/projects/{id}/connection` | Probe, then attach. Accepts fields or a `dsn` |
| DELETE | `/api/projects/{id}/connection` | Disconnect, keeping the project |
| POST | `/api/projects/{id}/connection/probe` | Reconnect and refresh |
| PUT | `/api/projects/{id}/connection/schemas` | Record which schemas to model |
| POST | `/api/connections/test` | Probe without saving anything |

A connection can be given as individual fields or as a whole connection string
in `dsn`, in either the URI form (`postgresql://user:pass@host:5432/db`) or the
keyword form (`host=... dbname=...`). The string is expanded into fields and
then discarded, so the password lives only in the encrypted column.

The probe implements [plan.md](plan.md) step 1. It opens a read-only session
(`default_transaction_read_only`), runs catalog reads under the metadata phase's
5s `statement_timeout`, and reports the conditions that degrade later steps
rather than only succeeding or failing:

- Postgres below 14, where `reltuples = -1` does not mean "never analyzed"
- connecting as a superuser, or to a primary rather than a replica
- missing `pg_stat_statements`, or no `pg_read_all_stats` to see other roles' queries
- tables never analyzed, so profiling falls back to sampling
- row-level security, split by whether this role is subject to it or bypasses it
- relations this role cannot `SELECT`, which are left out of the model entirely

## State

The app keeps its own Postgres database, `cubegen`, separate from any database
being modelled. It holds saved connections, and will hold evidence bundles and
generated models. Create it once:

```sql
CREATE DATABASE cubegen;
```

Point the backend at it with `CUBEGEN_APP_DATABASE_URL` in `backend/.env`; the
schema is applied at startup. Connection passwords are encrypted with
`backend/var/secret.key`, generated on first use. Neither the key nor `.env` is
in git, and neither is a substitute for a real secret manager before this ships.

## Semantic generation

Set `CUBEGEN_OPENAI_API_KEY` in `backend/.env` (and optionally
`CUBEGEN_OPENAI_MODEL`, default `gpt-4o`). Without it the backend starts and
warns, and the UI shows the key as unset; everything up to generation still
works.

Generation takes roughly ten seconds per cube, so it does not run inside the
request. `POST .../generate` validates what can fail cheaply -- no key, no
scan, nothing in scope -- starts a background job and returns `202` with a
`job_id`. Progress is streamed from the events endpoint as SSE, one `step`
event per step as it starts and again as it finishes, then a final `done` or
`failed` event carrying the whole snapshot.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/projects/{id}/generate` | Start a job; `{tables}` narrows one run |
| GET | `/api/projects/{id}/generate/{job_id}` | Snapshot, for a client that cannot stream |
| GET | `/api/projects/{id}/generate/{job_id}/events` | SSE progress stream |
| GET | `/api/projects/{id}/model` | The latest model, with its rendered YAML |

The **Generate** tab (`/projects/{id}/generate`) is where a model is built: the
table selection for a run, the evidence bundle to inspect first, and the button.
The **Review** tab is where the result is read — counts, open questions and the
YAML. They used to show the counts and questions twice, which made neither the
place to look. `/projects/{id}/model` still redirects to `/generate`.

The registry is in-process and in-memory, so a backend restart loses running
jobs. That is the first thing to replace before this runs on more than one
worker.

Generating replaces every `.yml` in `docker/cube/model/`, so a run narrowed to
a subset of tables deletes the cubes it did not generate.

## Viewing the model

The generated YAML is shown in the app in a CodeMirror 6 editor, currently
read-only (`CodeView`'s `readOnly` prop). `GET /api/schema/model-file` serves a
JSON Schema for a rendered model file, generated from the pydantic models in
[backend/app/generate/file_schema.py](backend/app/generate/file_schema.py) so
the editor's idea of a valid cube cannot drift from the generator's.

That schema describes the *file*, not the IR: the renderer writes `sql_table`
rather than `table`, and moves joins onto the cube that declares them. It
deliberately allows unknown properties, because Cube supports far more than the
renderer emits and flagging a hand-added `refresh_key` as an error would make
the editor worse than none.

Schema validation and completion are loaded on demand, only when the editor is
editable: `codemirror-json-schema` costs ~160KB gzipped, mostly from a hover
tooltip that renders markdown through Shiki, and the package does not declare
`sideEffects: false` so none of it tree-shakes.

## Retrieval

Views are indexed into Qdrant so a question can be matched against them. This is
the first half of the agent flow; turning a match into a Cube query is not built
yet.

Qdrant is an existing long-lived local service (container `qdrant`, port 6333),
deliberately not part of `docker/compose.yaml`. Start it with
`docker start qdrant`. Set `CUBEGEN_QDRANT_URL` if it lives elsewhere.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/projects/{id}/views/index` | Whether an index exists, and its size |
| POST | `/api/projects/{id}/views/index` | Rebuild this project's points |
| POST | `/api/projects/{id}/views/search` | Rank members against a question |

The shape follows the non-hybrid flow in `oasys-cube/scripts/embed_views_openai.py`:
one point per (view, cube, member), a single unnamed 1536-dimension Cosine
vector from `text-embedding-3-small`, a deterministic `uuid5` point id so a
rebuild upserts in place, and a payload of `domain_view`, `cube`, `type`, `name`,
`title`, `description` and the embedded `text`.

Two things differ, because that reference is single-tenant and owns its whole
collection. Every point here also carries `project_id`, with a keyword index on
it, and a rebuild deletes only this project's points instead of dropping the
collection. The collection is `cubegen_view_members`, owned by this app;
`cube_metadata_openai` and `cube_members_v2` belong to oasys and are never
touched.

Indexing is manual, from the Sync button on the Views page. Deleting a project
clears its points on a best-effort basis, since Qdrant does not cascade with the
Postgres row.

Editing a cube file feeds back into the stored model. Renaming a member updates
the IR, carries every saved view across the rename, and re-renders the view
files — without that, a view keeps naming a member that no longer exists, and
because Cube compiles the folder as one unit that fails *every* cube, not just
that view. A member that is removed rather than renamed is dropped from the
views that used it and reported. Renames are recognised by matching kind and
`sql`, and only when the match is unambiguous.

Because it is manual, the index can fall behind the views, so every point stores
a `hash` of the text it was built from and an `indexed_at`. `GET .../views/index`
rebuilds what the views would embed to right now and compares fingerprints,
reporting `stale` along with what was added, removed or changed. Comparing
content rather than timestamps is deliberate: editing a view, renaming a measure
and regenerating the model all change what a member embeds to, and none of them
reliably move a clock the index can see.

## Prompts

The question-answering flow runs eight prompts, listed on the Prompts page in
the order they execute. Defaults live in
[backend/app/agent/prompts.py](backend/app/agent/prompts.py); a project stores a
row only for a prompt it has actually changed, so an unedited prompt keeps
following the default and picks up later improvements to it. Saving a body that
matches the default deletes the override rather than freezing today's wording.

| Prompt | Decides | Placeholders |
|---|---|---|
| Gatekeeper | data question, small talk, or refuse | `{business_context}` (required) |
| Small talk | the reply when no data is needed | `{business_context}` |
| Refusal message | returned verbatim; no model is called | — |
| View selection | which view answers the question | `{views}` (required) |
| Concept extraction | the metrics and filters to retrieve | — |
| SQL generation | the Cube SQL | `{target_view_name}` (required) |
| SQL repair | the rewrite after Cube rejects a query | — |
| Answer | the sentence the reader sees | — |

Placeholders are filled at request time: `{business_context}` from the project's
context, `{views}` generated from its saved views. Generating the view menu
rather than storing it is deliberate — a menu typed by hand goes stale as views
are added and renamed, with nothing to notice. Saving a prompt that drops a
required placeholder, or invents one that will never be filled, is refused.

## Answering a question

`POST /api/projects/{id}/ask` with `{"question": "..."}` runs the flow from
[backend/app/agent/flow.py](backend/app/agent/flow.py), mirroring the non-hybrid
pipeline in oasys:

1. **Gatekeeper** — data question, small talk, or refuse.
2. **View selection** — one view, chosen from the project's saved views. What
   the model returns is matched against the real names rather than trusted; an
   unmatched name is reported instead of failing later as an empty result.
3. **Concept extraction** — the question split into metrics and filters.
4. **Retrieval** — one Qdrant search per concept, filtered to that view, with
   metrics searching measures and filters searching dimensions and segments.
   Best score per member wins.
5. **SQL** — written against that shortlist only.
6. **Execute** — through Cube's SQL API on port 15432. One repair attempt on
   failure, feeding back Cube's error, which names the members it accepts.
7. **Answer** — the rows narrated in a sentence.

Nothing is stored; the response carries every step it took, which is what makes
a wrong answer debuggable. Chat history comes later.

Three things about Cube's SQL API that the prompts have to state, each found by
running the queries rather than by reading the docs:

- every measure must be wrapped in `MEASURE(...)`, not selected bare;
- `NULLS LAST` is **ignored** — the API plans the ordering itself — so ranking a
  measure needs `HAVING MEASURE(...) IS NOT NULL`;
- the retrieved shortlist names each member's source cube, because a view
  exposing both `claims.status` and `hospitalization.case_status` otherwise
  gives "cancelled cases" two plausible answers, and the wrong one returns 0
  rather than an error.

## Tracing

Langfuse runs self-hosted from [docker/langfuse/](docker/langfuse/), vendored from
the upstream compose with two changes: its Postgres publishes no host port (this
machine's long-lived `local-postgres` owns 5432), and the two Langfuse images
come from Docker Hub because `docker.langfuse.com` is unreachable from here.
Headless initialisation provisions the org, project, login and API keys on first
boot, so nothing is clicked through. Secrets live in `docker/langfuse/.env`,
which is gitignored.

```bash
cd docker/langfuse && docker compose up -d     # UI on http://localhost:3000
```

Tracing is a no-op unless `CUBEGEN_LANGFUSE_PUBLIC_KEY` and
`CUBEGEN_LANGFUSE_SECRET_KEY` are set, so a stopped Langfuse costs the traces and
nothing else. Every OpenAI client is built through
[app/agent/tracing.py](backend/app/agent/tracing.py), which returns the Langfuse
drop-in when tracing is on — so generation and the agent are both instrumented
from one place, with model, tokens and latency captured per call.

One question is one trace, which is Langfuse's guidance for a chatbot turn:

```
AGENT       answer-question      10.4s
  GENERATION  classify            1.4s
  GENERATION  select_view         0.9s
  GENERATION  concepts            1.0s
  SPAN        retrieve-members    2.2s
    EMBEDDING   OpenAI-embedding  2.1s
  GENERATION  sql                 1.6s
  TOOL        cube-sql            0.3s
  GENERATION  narrate-answer      1.6s
```

Each model call is named for its step rather than the wrapper's default
`OpenAI-generation`, because evaluators and dashboards target observations by
name. `session_id` on the ask request groups turns into a conversation without
the backend storing any history.

Two things to know. Langfuse v4 writes to `events_core`/`events_full`; the
legacy `traces`/`observations` tables stay empty, and the read API is
`/api/public/v2/observations` — `/api/public/traces` returns nothing. And cost
comes back as 0: the seeded price list has 87 models but none matching
`gpt-4.1-mini`, so a model definition has to be added before spend is tracked.
