"""Turning text into vectors.

Separate from app.generate.llm: that module runs structured chat completions
against a schema, this one only asks for embeddings. They share nothing but the
API key.
"""

from __future__ import annotations

import logging

from openai import APITimeoutError, AsyncOpenAI

from app.agent import tracing
from app.core.config import settings

log = logging.getLogger(__name__)

#: Matching the reference indexer. Large enough that a whole model is one or two
#: requests, small enough to stay inside the per-request token limit.
BATCH_SIZE = 50

REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3

#: text-embedding-3-small. Asserted rather than assumed, because a collection
#: created at the wrong width silently rejects every later upsert.
VECTOR_SIZE = 1536


def _client() -> AsyncOpenAI:
    if not settings.openai_configured:
        raise RuntimeError("CUBEGEN_OPENAI_API_KEY is not set")
    return tracing.openai_client_class()(
        api_key=settings.openai_api_key,
        timeout=REQUEST_TIMEOUT,
        max_retries=MAX_RETRIES,
    )


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed in batches, preserving order."""
    if not texts:
        return []

    client = _client()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        try:
            response = await client.embeddings.create(
                model=settings.embedding_model, input=batch
            )
        except APITimeoutError as exc:
            raise RuntimeError(
                f"The embedding model did not respond within {REQUEST_TIMEOUT:.0f}s "
                f"after {MAX_RETRIES} attempts"
            ) from exc
        # The API returns results in request order, but it states an index for
        # each one; sorting on it means a future change cannot silently pair a
        # vector with the wrong text.
        for item in sorted(response.data, key=lambda d: d.index):
            vectors.append(item.embedding)

    if vectors and len(vectors[0]) != VECTOR_SIZE:
        raise RuntimeError(
            f"{settings.embedding_model} returned {len(vectors[0])}-dimensional "
            f"vectors; the collection expects {VECTOR_SIZE}"
        )
    return vectors
