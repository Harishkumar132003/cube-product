"""Safe read-only connections to a customer database (plan.md step 1).

Two rules hold everywhere in this package:

* The session is read-only. ``default_transaction_read_only`` is set on connect,
  so any write the pipeline attempts by accident fails at the server.
* Timeouts are per phase, not per session. Metadata reads should die fast;
  sampling legitimately takes a minute. Each phase runs in its own transaction
  and sets ``SET LOCAL statement_timeout`` / ``lock_timeout``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

log = logging.getLogger(__name__)


class Phase(str, Enum):
    """Work phases, each with its own timeout budget."""

    METADATA = "metadata"
    PG_STATS = "pg_stats"
    SAMPLING = "sampling"
    JSONB_KEYS = "jsonb_keys"


# Per-phase statement timeouts in milliseconds (plan.md step 1).
PHASE_TIMEOUTS_MS: dict[Phase, int] = {
    Phase.METADATA: 5_000,
    Phase.PG_STATS: 10_000,
    Phase.SAMPLING: 60_000,
    Phase.JSONB_KEYS: 60_000,
}

# Locks should never be waited on against a production database.
LOCK_TIMEOUT_MS = 2_000


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = "prefer"
    # Seconds. Applied to the TCP connect, separate from statement timeouts.
    connect_timeout: int = 10

    def conninfo(self) -> str:
        return psycopg.conninfo.make_conninfo(
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
            connect_timeout=self.connect_timeout,
            application_name="cubegen-adapter",
        )

    def safe_repr(self) -> str:
        """Identity without the password, for logs and error messages."""
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


class RecoveryConflict(Exception):
    """The query was cancelled by a replica's recovery conflict (plan.md step 1).

    Callers retry with backoff, then mark the fact unverified rather than
    treating the failure as evidence.
    """


@asynccontextmanager
async def connect(params: ConnectionParams) -> AsyncIterator[AsyncConnection]:
    """Open a read-only session against a customer database."""
    conn = await psycopg.AsyncConnection.connect(
        params.conninfo(),
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        # Applies to every transaction started after this point.
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET idle_in_transaction_session_timeout = '30s'")
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def phase(conn: AsyncConnection, which: Phase) -> AsyncIterator[AsyncConnection]:
    """Run a block inside one transaction carrying that phase's timeouts.

    ``SET LOCAL`` is scoped to the transaction, so the budget cannot leak into
    the next phase.
    """
    timeout_ms = PHASE_TIMEOUTS_MS[which]
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
            await conn.execute(f"SET LOCAL lock_timeout = {LOCK_TIMEOUT_MS}")
            yield conn
    except psycopg.errors.QueryCanceled as exc:
        # On a replica this is usually a recovery conflict rather than our own
        # timeout. The sqlstate is the same, so distinguish on the message.
        message = str(exc)
        if "conflict with recovery" in message:
            raise RecoveryConflict(message) from exc
        log.warning("phase %s exceeded %sms", which.value, timeout_ms)
        raise
