"""Tracing, with Langfuse when it is configured and nothing when it is not.

Observability must never be load-bearing. Every entry point here degrades to a
no-op if the keys are missing or Langfuse is unreachable, so a stopped container
costs you the traces and nothing else.

One trace per question, per Langfuse's own guidance: a trace is one
self-contained unit of work, and a conversation is a session over several of
them.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

from app.core.config import settings

log = logging.getLogger(__name__)

_configured = False


def _client():
    """The Langfuse client, or None when tracing is off."""
    global _configured
    if not settings.tracing_enabled:
        return None
    try:
        from langfuse import Langfuse, get_client

        if not _configured:
            Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_base_url,
            )
            _configured = True
            log.info("tracing to Langfuse at %s", settings.langfuse_base_url)
        return get_client()
    except Exception as exc:  # noqa: BLE001 - tracing must not break the request
        log.warning("tracing unavailable: %s", exc)
        return None


def openai_client_class():
    """`AsyncOpenAI`, wrapped when tracing is on.

    The wrapped class is a drop-in: same calls, but each one is recorded as a
    generation carrying the model, token usage and cost. That is why the model
    calls are not instrumented by hand anywhere.
    """
    if settings.tracing_enabled:
        try:
            from langfuse.openai import AsyncOpenAI  # type: ignore[no-redef]

            return AsyncOpenAI
        except Exception as exc:  # noqa: BLE001
            log.warning("falling back to the plain OpenAI client: %s", exc)
    from openai import AsyncOpenAI

    return AsyncOpenAI


def generation_kwargs(name: str) -> dict[str, Any]:
    """Extra kwargs for an OpenAI call, empty when tracing is off.

    `name` labels the generation so evaluators and dashboards can target it,
    but only the Langfuse wrapper accepts it -- the plain OpenAI SDK raises
    TypeError. Keeping that knowledge here means a caller cannot pass a
    wrapper-only argument to an unwrapped client.
    """
    return {"name": name} if settings.tracing_enabled else {}


class _Nothing:
    """Stands in for an observation when tracing is off."""

    def update(self, **_kwargs: Any) -> None:
        return None

    def update_trace(self, **_kwargs: Any) -> None:
        return None


@contextlib.contextmanager
def observation(name: str, *, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    """One step of the flow. Children opened inside nest under it."""
    client = _client()
    opened = None
    if client is not None:
        try:
            opened = client.start_as_current_observation(
                as_type=as_type, name=name, **fields
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open observation %s: %s", name, exc)

    if opened is None:
        yield _Nothing()
        return

    # Only opening the observation is guarded. Wrapping the caller's body in
    # try/except and yielding again turns any exception raised inside it into
    # "generator didn't stop after throw()", which destroys the real error --
    # and the SQL repair step depends on reading Cube's message.
    with opened as span:
        yield span


@contextlib.contextmanager
def attributes(**fields: Any) -> Iterator[None]:
    """Attach session and user to everything opened inside."""
    client = _client()
    if client is None:
        yield
        return
    try:
        from langfuse import propagate_attributes

        with propagate_attributes(**{k: v for k, v in fields.items() if v}):
            yield
    except Exception as exc:  # noqa: BLE001
        log.warning("could not propagate attributes: %s", exc)
        yield


def flush() -> None:
    """Send anything buffered. Traces are batched in the background."""
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not flush traces: %s", exc)


def shutdown() -> None:
    client = _client()
    if client is None:
        return
    try:
        client.shutdown()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not shut tracing down: %s", exc)
