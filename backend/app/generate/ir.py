"""The JSON IR: what the model emits, and the only thing the renderer reads.

The model never writes YAML. It fills in these shapes, which are validated
before anything is rendered, so a hallucinated Cube property cannot reach a
file. Every field the renderer needs has a home here; anything the model
invents outside them is rejected by validation rather than silently written.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Classification = Literal["fact", "dimension", "bridge", "staging", "audit", "ignore"]
Relationship = Literal["one_to_one", "one_to_many", "many_to_one"]
DimensionType = Literal["string", "number", "time", "boolean"]
MeasureType = Literal["count", "count_distinct", "sum", "avg", "min", "max", "number"]


class TableClass(BaseModel):
    table: str = Field(description="Qualified name exactly as given in the bundle")
    classification: Classification
    reason: str = Field(description="One sentence, citing the evidence used")


class JoinDecision(BaseModel):
    """A join, stated from the cube that will declare it."""

    declaring_cube: str = Field(description="Qualified name of the cube the join lives on")
    target_cube: str
    relationship: Relationship
    sql: str = Field(description="Join condition using {CUBE} and {target} placeholders")
    reason: str


class Dimension(BaseModel):
    name: str = Field(description="snake_case, unique within the cube")
    title: str
    sql: str = Field(description="Column name or SQL expression")
    type: DimensionType
    description: str
    primary_key: bool = False
    # Technical columns stay out of the public surface.
    public: bool = True


class MeasureFilter(BaseModel):
    sql: str = Field(description="Filter expression using {CUBE}")


class Measure(BaseModel):
    name: str
    title: str
    type: MeasureType
    sql: str | None = Field(default=None, description="Column or expression; omit for count")
    description: str
    filters: list[MeasureFilter] = Field(default_factory=list)


class Segment(BaseModel):
    name: str
    title: str
    sql: str
    description: str


class Cube(BaseModel):
    name: str = Field(description="snake_case cube name")
    title: str
    table: str = Field(description="Qualified table name for sql_table")
    description: str
    dimensions: list[Dimension]
    measures: list[Measure]
    segments: list[Segment] = Field(default_factory=list)


class ViewMember(BaseModel):
    join_path: str = Field(description="Dot path from the root cube, e.g. hospitalization.claims")
    includes: list[str]


class View(BaseModel):
    name: str
    title: str
    description: str
    cubes: list[ViewMember]


class Question(BaseModel):
    """Something the evidence could not settle."""

    subject: str
    question: str
    why_it_matters: str
    options: list[str] = Field(default_factory=list)


# --- Step outputs ------------------------------------------------------- #


class ClassifyResult(BaseModel):
    tables: list[TableClass]
    questions: list[Question] = Field(default_factory=list)


class JoinResult(BaseModel):
    joins: list[JoinDecision]
    questions: list[Question] = Field(default_factory=list)


class CubeResult(BaseModel):
    cube: Cube
    questions: list[Question] = Field(default_factory=list)


class ViewResult(BaseModel):
    views: list[View]
    questions: list[Question] = Field(default_factory=list)


class Model(BaseModel):
    """The whole generated model, assembled from the steps."""

    cubes: list[Cube]
    views: list[View]
    joins: list[JoinDecision]
    classifications: list[TableClass]
    questions: list[Question]
