from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import tracing
from app.agent import vectors as vectors_mod
from app.api.agent import router as agent_router
from app.api.bootstrap import router as bootstrap_router
from app.api.connections import router as connections_router
from app.api.model_files import router as model_files_router
from app.api.projects import router as projects_router
from app.api.prompts import router as prompts_router
from app.api.schema import router as schema_router
from app.api.views import router as views_router
from app.core.config import settings
from app.db.appdb import close_pool, open_pool

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_pool()
    if not settings.openai_configured:
        log.warning(
            "CUBEGEN_OPENAI_API_KEY is not set; semantic generation is unavailable"
        )
    yield
    tracing.shutdown()
    await vectors_mod.close()
    await close_pool()


app = FastAPI(
    title="Cube semantic layer generator",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bootstrap_router)
app.include_router(connections_router)
app.include_router(projects_router)
app.include_router(model_files_router)
app.include_router(schema_router)
app.include_router(agent_router)
app.include_router(prompts_router)
app.include_router(views_router)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Flatten pydantic's error list into one sentence the UI can show.

    The default 422 body is a list of dicts, which the client would have to
    understand. Connection input is user-typed, so the message matters.
    """
    messages = []
    for error in exc.errors():
        message = error.get("msg", "Invalid value")
        messages.append(message.removeprefix("Value error, "))
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "; ".join(dict.fromkeys(messages))},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
