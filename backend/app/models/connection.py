"""API models for database connections and the connect-screen probe."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import psycopg
from pydantic import BaseModel, Field, SecretStr, model_validator

SslMode = Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
WarningLevel = Literal["info", "warning", "error"]


class ConnectionInput(BaseModel):
    """Credentials as typed on the connect screen.

    Either the fields are filled in individually, or a whole connection string
    is pasted into `dsn` and the fields are derived from it. A pasted string
    wins over anything typed alongside it.
    """

    name: str = Field(default="", max_length=120, description="Label shown in the UI")
    host: str = ""
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = ""
    user: str = ""
    password: SecretStr = Field(default=SecretStr(""))
    sslmode: SslMode = "prefer"
    dsn: str | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def _expand_dsn(self) -> "ConnectionInput":
        if self.dsn:
            parsed = parse_dsn(self.dsn)
            for field, value in parsed.items():
                object.__setattr__(self, field, value)
            # Expanded into fields; discard it so the password is held only in
            # the SecretStr from here on.
            object.__setattr__(self, "dsn", None)
        missing = [
            label
            for label, value in (
                ("host", self.host),
                ("database", self.database),
                ("user", self.user),
            )
            if not str(value).strip()
        ]
        if missing:
            raise ValueError(f"Missing connection details: {', '.join(missing)}")
        if not self.name.strip():
            object.__setattr__(self, "name", f"{self.user}@{self.host}/{self.database}")
        return self


def parse_dsn(dsn: str) -> dict[str, object]:
    """Split a Postgres connection string into the fields the form holds.

    Accepts both the URI form (postgresql://user:pass@host:5432/db) and the
    keyword form (host=... dbname=...), because psycopg understands both.
    """
    try:
        parts = psycopg.conninfo.conninfo_to_dict(dsn.strip())
    except psycopg.ProgrammingError as exc:
        raise ValueError(f"Could not parse that connection string: {exc}") from exc

    out: dict[str, object] = {}
    if parts.get("host"):
        out["host"] = str(parts["host"])
    if parts.get("port"):
        try:
            out["port"] = int(str(parts["port"]))
        except ValueError as exc:
            raise ValueError("The port in that connection string is not a number") from exc
    if parts.get("dbname"):
        out["database"] = str(parts["dbname"])
    if parts.get("user"):
        out["user"] = str(parts["user"])
    if parts.get("password"):
        out["password"] = SecretStr(str(parts["password"]))
    mode = parts.get("sslmode")
    if mode in SSL_MODES:
        out["sslmode"] = mode
    return out


class Warning_(BaseModel):
    """A condition that degrades a later pipeline step."""

    level: WarningLevel
    code: str
    message: str


class ServerInfo(BaseModel):
    database: str
    role_name: str
    server_version: str
    server_version_num: int
    is_replica: bool
    is_superuser: bool
    bypasses_rls: bool
    read_only_enforced: bool
    has_pg_stat_statements: bool
    has_read_all_stats: bool


class SchemaSummary(BaseModel):
    name: str
    tables: int = 0
    partitioned_tables: int = 0
    views: int = 0
    materialized_views: int = 0
    rls_tables: int = 0
    never_analyzed: int = 0
    unreadable: int = 0

    @property
    def modelable(self) -> int:
        return self.tables + self.views + self.materialized_views


class ProbeResult(BaseModel):
    server: ServerInfo
    schemas: list[SchemaSummary]
    warnings: list[Warning_]


class TestConnectionResponse(BaseModel):
    ok: bool
    probe: ProbeResult | None = None
    error: str | None = None
    error_kind: (
        Literal["auth", "network", "timeout", "permission", "unsupported", "unknown"] | None
    ) = None


class StoredConnection(BaseModel):
    """A saved connection. The password is never returned by the API."""

    id: str
    name: str
    host: str
    port: int
    database: str
    user: str
    sslmode: SslMode
    selected_schemas: list[str] = []
    project_id: str
    created_at: datetime
    last_connected_at: datetime | None = None
    last_probe: ProbeResult | None = None
