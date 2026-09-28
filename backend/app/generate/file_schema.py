"""JSON Schema for the rendered Cube YAML files.

This is not the IR. The IR is what the model fills in; this is the shape the
renderer actually writes, which differs -- `table` becomes `sql_table`, and
joins move from the model onto the cube that declares them. The editor
validates files, so it needs this one.

It is written as pydantic models rather than a hand-kept JSON blob so the
vocabularies come from `app.generate.ir` and cannot drift from what the
generator is allowed to produce.

Extra properties are deliberately allowed. Cube supports far more than the
renderer emits -- refresh keys, pre-aggregations, data sources -- and flagging
a legitimate Cube property as an error the moment someone hand-edits a file
would make the editor worse than no editor.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.generate.ir import DimensionType, MeasureType, Relationship


class FileJoin(BaseModel):
    name: str = Field(description="The cube being joined to")
    sql: str = Field(description="Join condition, e.g. {CUBE}.other_id = {other}.id")
    relationship: Relationship


class FileDimension(BaseModel):
    name: str = Field(description="snake_case, unique within the cube")
    title: str
    sql: str = Field(description="Column name or SQL expression")
    type: DimensionType
    primary_key: bool = Field(default=False, description="Exactly one per cube")
    public: bool = Field(default=True, description="false hides it from the API")
    description: str | None = Field(
        default=None, description="Read by AI agents through Cube's Meta API"
    )


class FileMeasureFilter(BaseModel):
    sql: str = Field(description="Filter expression using {CUBE}")


class FileMeasure(BaseModel):
    name: str
    title: str
    type: MeasureType
    sql: str | None = Field(default=None, description="Column or expression; omit for count")
    filters: list[FileMeasureFilter] = Field(default_factory=list)
    description: str | None = Field(
        default=None, description="Say what the number is, its unit, and what it is not"
    )


class FileSegment(BaseModel):
    name: str
    title: str
    sql: str
    description: str | None = None


class FileCube(BaseModel):
    name: str = Field(description="snake_case cube name")
    title: str
    sql_table: str = Field(description="Qualified table name, e.g. public.claims")
    description: str | None = None
    joins: list[FileJoin] = Field(default_factory=list)
    dimensions: list[FileDimension] = Field(default_factory=list)
    measures: list[FileMeasure] = Field(default_factory=list)
    segments: list[FileSegment] = Field(default_factory=list)


class FileViewCube(BaseModel):
    join_path: str = Field(description="Dot path from the root cube")
    includes: list[str]


class FileView(BaseModel):
    name: str
    title: str
    description: str | None = None
    cubes: list[FileViewCube]


class ModelFile(BaseModel):
    """One rendered file. Cubes and views live in separate files, so in
    practice exactly one of these keys is present."""

    cubes: list[FileCube] = Field(default_factory=list)
    views: list[FileView] = Field(default_factory=list)


def json_schema() -> dict[str, Any]:
    """Draft-07, because that is what the editor's validator understands.

    Pydantic emits 2020-12, which keeps subschemas under `$defs`. The editor
    resolves `definitions`, so the refs are pointed there and the key renamed;
    nothing else about the schema changes.
    """
    schema = ModelFile.model_json_schema(ref_template="#/definitions/{model}")
    if "$defs" in schema:
        schema["definitions"] = schema.pop("$defs")
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["title"] = "Cube model file"
    return schema
