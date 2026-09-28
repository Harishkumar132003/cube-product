"""What the app should show on load."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.db import projects
from app.models.project import Project

router = APIRouter(prefix="/api", tags=["bootstrap"])


class Bootstrap(BaseModel):
    """Drives the first screen.

    No projects -> the app opens on project creation. A project without a
    connection -> the connect form. Otherwise it reconnects to the most recent
    project and goes to the model.
    """

    projects: list[Project]
    active_project_id: str | None
    openai_configured: bool
    openai_model: str
    qdrant_configured: bool


@router.get("/bootstrap", response_model=Bootstrap)
async def bootstrap() -> Bootstrap:
    all_projects = await projects.list_all()
    # Prefer the most recent project that is actually connected.
    active = next((p for p in all_projects if p.connection), None) or (
        all_projects[0] if all_projects else None
    )
    return Bootstrap(
        projects=all_projects,
        active_project_id=active.id if active else None,
        openai_configured=settings.openai_configured,
        openai_model=settings.openai_model,
        qdrant_configured=settings.qdrant_configured,
    )
