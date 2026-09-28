"""The JSON Schema the editor validates model files against.

Served rather than duplicated in the frontend, so the editor's idea of a valid
cube and the generator's cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from app.generate.file_schema import json_schema

router = APIRouter(prefix="/api/schema", tags=["schema"])


@router.get("/model-file")
async def model_file_schema(response: Response) -> dict[str, Any]:
    # It only changes when the code does, so let the browser keep it.
    response.headers["Cache-Control"] = "public, max-age=3600"
    return json_schema()
