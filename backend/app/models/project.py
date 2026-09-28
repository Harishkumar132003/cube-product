"""A project is the top-level object: one connection, one generated model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.connection import StoredConnection


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class BusinessContextInput(BaseModel):
    """Free text from the user about the business and its flow.

    Sent to the model provider verbatim when generation runs, so the UI says
    so before anyone pastes a data dictionary into it.
    """

    text: str = Field(default="", max_length=60_000)


class TableNote(BaseModel):
    """What one table is for, in the user's words."""

    purpose: str = Field(default="", max_length=2000)


class TableNotes(BaseModel):
    # Keyed by qualified name, e.g. "public.claims".
    notes: dict[str, TableNote] = Field(default_factory=dict)


class TableSelection(BaseModel):
    tables: list[str] = Field(default_factory=list, max_length=5000)


class Project(BaseModel):
    id: str
    name: str
    created_at: datetime
    business_context: str = ""
    business_context_updated_at: datetime | None = None
    last_scan_at: datetime | None = None
    last_generated_at: datetime | None = None
    # Qualified names. Empty means every readable relation.
    selected_tables: list[str] = []
    table_notes: dict[str, TableNote] = {}
    # None until the project's database is connected.
    connection: StoredConnection | None = None

    @property
    def connected(self) -> bool:
        return self.connection is not None
