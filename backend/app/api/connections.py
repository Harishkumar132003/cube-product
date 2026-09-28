"""Probing a database without saving anything.

Persistence lives in app.api.projects, since a connection belongs to a
project. The helpers here are shared by both.
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter

from app.db import probe as probe_mod
from app.db.session import ConnectionParams, RecoveryConflict
from app.models.connection import ConnectionInput, ProbeResult, TestConnectionResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connections", tags=["connections"])


def build_params(payload: ConnectionInput) -> ConnectionParams:
    return ConnectionParams(
        host=payload.host,
        port=payload.port,
        database=payload.database,
        user=payload.user,
        password=payload.password.get_secret_value(),
        sslmode=payload.sslmode,
    )


def _classify(exc: Exception) -> tuple[str, str]:
    """Map a driver exception to (error_kind, user-facing message).

    Driver messages can echo back connection details, so the message shown is
    written here rather than passed through raw.
    """
    text = str(exc).lower()

    if isinstance(exc, RecoveryConflict):
        return "timeout", (
            "The query was cancelled by a replica recovery conflict. This is normal on a "
            "busy replica; retry, or connect to a less active one."
        )
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return "permission", "This role lacks the privileges needed to read the catalog."
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return "timeout", "The server took too long to answer the catalog queries."

    if "password authentication failed" in text or "no password supplied" in text:
        return "auth", "Authentication failed. Check the user and password."
    if "role" in text and "does not exist" in text:
        return "auth", "That role does not exist on this server."
    if "database" in text and "does not exist" in text:
        return "auth", "That database does not exist on this server."
    if "no pg_hba.conf entry" in text:
        return "auth", (
            "The server rejected the connection (no pg_hba.conf entry). The host or SSL "
            "mode may need to be allowed for this role."
        )
    if "could not translate host name" in text:
        return "network", "Host name could not be resolved."
    if "connection refused" in text or "could not connect" in text:
        return "network", "Could not reach the server on that host and port."
    if "timeout expired" in text or "timed out" in text:
        return "network", "Timed out connecting to the server."
    if "server does not support ssl" in text:
        return "network", "The server does not support SSL. Try sslmode 'prefer' or 'disable'."

    log.warning("unclassified connection error: %s", type(exc).__name__)
    return "unknown", "Could not connect. See the backend log for details."


async def run_probe(params: ConnectionParams) -> tuple[ProbeResult | None, str | None, str | None]:
    try:
        return await probe_mod.probe(params), None, None
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the UI
        kind, message = _classify(exc)
        log.info("probe failed for %s: %s", params.safe_repr(), type(exc).__name__)
        return None, kind, message


@router.post("/test", response_model=TestConnectionResponse)
async def test_connection(payload: ConnectionInput) -> TestConnectionResponse:
    """Probe a database without saving anything."""
    result, kind, message = await run_probe(build_params(payload))
    if result is None:
        return TestConnectionResponse(ok=False, error=message, error_kind=kind)  # type: ignore[arg-type]
    return TestConnectionResponse(ok=True, probe=result)
