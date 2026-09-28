"""Project routes. A project owns one connection; the connection lives here."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.connections import build_params, run_probe
from app import bundle as bundle_mod
from app.agent import vectors
from app.core.config import settings
from app.generate import jobs
from app.generate import llm as llm_mod
from app.generate import render as render_mod
from app.db import catalog as catalog_mod
from app.db import profile as profile_mod
from app.db import models_store
from app.db import projects, scans, store
from app.models.connection import ConnectionInput, StoredConnection, TestConnectionResponse
from app.models.project import (
    BusinessContextInput,
    Project,
    ProjectInput,
    TableNotes,
    TableSelection,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _require(project_id: str) -> Project:
    project = await projects.get(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("", response_model=list[Project])
async def list_projects() -> list[Project]:
    return await projects.list_all()


@router.post("", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectInput) -> Project:
    return await projects.create(payload.name.strip())


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str) -> Project:
    return await _require(project_id)


@router.patch("/{project_id}", response_model=Project)
async def rename_project(project_id: str, payload: ProjectInput) -> Project:
    renamed = await projects.rename(project_id, payload.name.strip())
    if renamed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project not found")
    return await _require(project_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str) -> Response:
    """Removes the project and its connection."""
    if not await projects.delete(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project not found")
    # Postgres cascades; Qdrant does not. Best effort on purpose -- a stopped
    # Qdrant must not stop someone deleting a project, and the orphaned points
    # are unreachable anyway once the project id is gone.
    try:
        await vectors.delete_project(project_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not clear the vector index for %s: %s", project_id, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{project_id}/context", response_model=Project)
async def set_context(project_id: str, payload: BusinessContextInput) -> Project:
    """Record what the user told us about the business."""
    await _require(project_id)
    updated = await projects.set_business_context(project_id, payload.text)
    assert updated is not None
    return updated


# --- The project's connection ---------------------------------------------- #


@router.put("/{project_id}/connection", response_model=StoredConnection)
async def set_connection(project_id: str, payload: ConnectionInput) -> StoredConnection:
    """Probe, then attach. A connection that cannot be probed is not stored."""
    await _require(project_id)
    result, _kind, message = await run_probe(build_params(payload))
    if result is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=message)
    return await store.create(project_id, payload, probe=result)


@router.delete("/{project_id}/connection", status_code=status.HTTP_204_NO_CONTENT)
async def clear_connection(project_id: str) -> Response:
    """Detach the database, keeping the project."""
    await _require(project_id)
    if not await store.delete_by_project(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project has no connection")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/connection/probe", response_model=TestConnectionResponse)
async def reprobe(project_id: str) -> TestConnectionResponse:
    """Reconnect and refresh what this role can see."""
    project = await _require(project_id)
    if project.connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project has no connection")

    params = await store.params_for(project.connection.id)
    assert params is not None
    result, kind, message = await run_probe(params)
    if result is None:
        return TestConnectionResponse(ok=False, error=message, error_kind=kind)  # type: ignore[arg-type]

    await store.save_probe(project.connection.id, result)
    return TestConnectionResponse(ok=True, probe=result)


class ScanSummary(BaseModel):
    id: str
    created_at: datetime
    schemas: list[str]
    counts: dict[str, int]
    profile: dict[str, int]


@router.post("/{project_id}/scan", response_model=ScanSummary)
async def run_scan(project_id: str) -> ScanSummary:
    """Read the catalog and column statistics, and store the result.

    Read-only against the customer database. Everything written lands in the
    app's own database.
    """
    project = await _require(project_id)
    if project.connection is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Connect a database first")

    chosen = project.connection.selected_schemas
    if not chosen:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Select at least one schema to scan"
        )

    params = await store.params_for(project.connection.id)
    assert params is not None
    try:
        extracted = await catalog_mod.extract(params, chosen)
        extracted = await profile_mod.profile(params, chosen, extracted)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        log.exception("scan failed for project %s", project_id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Scan failed: {type(exc).__name__}"
        ) from exc

    saved = await scans.save(project_id, chosen, extracted)
    return ScanSummary(**saved)


@router.get("/{project_id}/scan")
async def get_scan(project_id: str) -> dict:
    """The latest scan, with the full catalog."""
    await _require(project_id)
    found = await scans.latest(project_id, with_catalog=True)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This project has not been scanned")
    return found


@router.put("/{project_id}/tables", response_model=Project)
async def select_tables(project_id: str, payload: TableSelection) -> Project:
    """Choose which relations the semantic model is built from."""
    await _require(project_id)
    updated = await projects.set_selected_tables(project_id, payload.tables)
    assert updated is not None
    return updated


@router.put("/{project_id}/table-notes", response_model=Project)
async def set_table_notes(project_id: str, payload: TableNotes) -> Project:
    """Record what each table is for."""
    await _require(project_id)
    updated = await projects.set_table_notes(
        project_id, {k: v.model_dump() for k, v in payload.notes.items()}
    )
    assert updated is not None
    return updated


class BundleRequest(BaseModel):
    """Optional narrowing for one run, without changing the saved selection."""

    tables: list[str] | None = None


@router.post("/{project_id}/bundle")
async def build_bundle(project_id: str, payload: BundleRequest | None = None) -> dict:
    """Assemble the evidence bundle that generation would consume.

    Deterministic and read-only: it reads the stored scan and the project's
    own context, and touches no database. Exposed on its own so the bundle can
    be inspected before anything is sent to a model provider.
    """
    project = await _require(project_id)
    scan = await scans.latest(project_id, with_catalog=True)
    if scan is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Scan the database before generating"
        )
    return bundle_mod.build(
        project=project.model_dump(mode="json"),
        scan=scan,
        include=payload.tables if payload else None,
    )


class GenerateRequest(BaseModel):
    """Narrow one run without changing the project's saved selection."""

    tables: list[str] | None = None
    include_views: bool = False


def _write_model_files(files: dict[str, str]) -> list[str]:
    """Write rendered YAML where Cube reads it.

    Previously generated files are cleared first, so a rerun that drops a cube
    does not leave the old one behind for Cube to compile.
    """
    root = settings.cube_model_dir
    for existing in sorted(root.rglob("*.yml")):
        existing.unlink()
    written = []
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        written.append(relative)
    return sorted(written)


@router.post("/{project_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_model(project_id: str, payload: GenerateRequest | None = None) -> dict:
    """Start generation in the background and return a job to watch.

    The work takes roughly ten seconds per cube, so it does not run inside the
    request. Everything that can fail cheaply -- no key, no scan, nothing in
    scope -- is still checked here, so those stay ordinary 400s rather than
    arriving later as a failed job.
    """
    if not settings.openai_configured:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="CUBEGEN_OPENAI_API_KEY is not set"
        )

    project = await _require(project_id)
    scan = await scans.latest(project_id, with_catalog=True)
    if scan is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Scan the database before generating"
        )

    request = payload or GenerateRequest()

    async def work(job: jobs.Job) -> dict:
        job.emit("bundle", "Building evidence bundle", "running")
        bundle = bundle_mod.build(
            project=project.model_dump(mode="json"), scan=scan, include=request.tables
        )
        if not bundle["tables"]:
            raise RuntimeError("No tables in scope")
        job.emit(
            "bundle",
            "Building evidence bundle",
            "done",
            f"{bundle['counts']['tables']} tables, {bundle['counts']['columns']} columns",
        )

        model = await llm_mod.generate(
            bundle,
            include_views=request.include_views,
            progress=job.emit,
        )

        job.emit("render", "Rendering YAML", "running")
        files = render_mod.render(model)
        written = _write_model_files(files)
        job.emit("render", "Rendering YAML", "done", f"{len(written)} files written")

        counts = {
            "cubes": len(model.cubes),
            "views": len(model.views),
            "joins": len(model.joins),
            "dimensions": sum(len(c.dimensions) for c in model.cubes),
            "measures": sum(len(c.measures) for c in model.cubes),
            "questions": len(model.questions),
        }
        saved = await models_store.save(
            project_id,
            scope=bundle["scope"],
            counts=counts,
            questions=[q.model_dump() for q in model.questions],
            ir=model.model_dump(),
            files=files,
        )
        job.emit(
            "saved",
            "Model saved",
            "done",
            f"{counts['cubes']} cubes, {counts['measures']} measures",
        )
        # `files` is returned as well as written, so the UI can show the YAML
        # straight after generating rather than only after a reload refetches it.
        return {**saved, "files_written": written, "files": files}

    job = jobs.start(project_id, work)
    return {"job_id": job.id, "state": job.state}


@router.get("/{project_id}/generate/{job_id}")
async def generation_status(project_id: str, job_id: str) -> dict:
    """A snapshot, for a client that cannot hold the stream open."""
    job = jobs.get(job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such generation job")
    return job.snapshot()


@router.get("/{project_id}/generate/{job_id}/events")
async def generation_events(project_id: str, job_id: str) -> StreamingResponse:
    """Server-sent events for one generation job."""
    job = jobs.get(job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such generation job")

    async def stream() -> AsyncIterator[str]:
        queue = job.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # A comment keeps the connection alive through proxies that
                    # drop idle streams; a long cube can be silent for a while.
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"event: step\ndata: {json.dumps(event.as_dict())}\n\n"
            # The snapshot carries the saved model, whose created_at is a
            # datetime; json.dumps alone cannot encode it.
            payload = json.dumps(jsonable_encoder(job.snapshot()))
            yield f"event: {job.state}\ndata: {payload}\n\n"
        finally:
            job.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers proxied responses by default, which would hold every
            # event until the stream closed and defeat the point of streaming.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{project_id}/model")
async def get_model(project_id: str) -> dict:
    """The latest generated model, with its rendered files."""
    await _require(project_id)
    found = await models_store.latest(project_id, with_files=True)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Nothing generated yet")
    return found


class SchemaSelection(BaseModel):
    schemas: list[str]


@router.put("/{project_id}/connection/schemas", response_model=StoredConnection)
async def select_schemas(project_id: str, payload: SchemaSelection) -> StoredConnection:
    """Record which schemas the model should be built from."""
    project = await _require(project_id)
    if project.connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project has no connection")
    updated = await store.set_selected_schemas(project.connection.id, payload.schemas)
    assert updated is not None
    return updated
