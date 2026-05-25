"""
KB embedder — wraps Azure OpenAI text-embedding-3-small.

Uses the same AZURE_OPENAI_* credentials as the chat path.
Returns L2-normalised float32 numpy arrays (shape: (KB_EMBED_DIMENSIONS,))
so they are ready for cosine-similarity comparison in sqlite-vec.
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
from openai import AzureOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100  # Azure OpenAI embeddings API limit per call


def embed_model_key() -> str:
    """String stored in kb_chunks.embed_model — identifies both model and dimensions.
    A change here triggers full re-embed of all chunks."""
    s = get_settings()
    return f"{s.AZURE_OPENAI_EMBED_DEPLOYMENT}:{s.KB_EMBED_DIMENSIONS}"


def _get_client() -> tuple[AzureOpenAI, str, int]:
    s = get_settings()
    client = AzureOpenAI(
        azure_endpoint=s.AZURE_OPENAI_ENDPOINT,
        api_key=s.AZURE_OPENAI_API_KEY,
        api_version=s.AZURE_OPENAI_EMBED_API_VERSION,  # embeddings use a different version
    )
    return client, s.AZURE_OPENAI_EMBED_DEPLOYMENT, s.KB_EMBED_DIMENSIONS


def _normalise(vec: list[float]) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr /= norm
    return arr


def embed_texts(texts: Sequence[str]) -> list[np.ndarray]:
    """Embed a batch of document texts. Returns one L2-normalised array per text."""
    if not texts:
        return []
    client, deployment, dims = _get_client()
    results: list[np.ndarray] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = list(texts[i : i + _BATCH_SIZE])
        resp = client.embeddings.create(
            model=deployment,
            input=batch,
            dimensions=dims,
        )
        # API returns items sorted by index
        for item in sorted(resp.data, key=lambda x: x.index):
            results.append(_normalise(item.embedding))
    return results


def embed_query(query: str) -> np.ndarray:
    """Embed a single search query. Returns a L2-normalised array."""
    return embed_texts([query])[0]


def embed_query_for_search(query: str) -> np.ndarray:
    """Embed a search query with acronym expansion applied.

    Short acronym queries ('AAD identity setup') under-anchor a vector
    embedding — there isn't enough surface area for the model to find the
    target concept. Feeding the BM25-expanded phrase list into the embedder
    too widens the semantic surface so the vector half can hit the same
    docs BM25 already hits via expansion. No-op for queries with no
    acronyms (expand_query returns just the original).
    """
    from app.kb.acronyms import expand_query
    expanded = expand_query(query)
    if len(expanded) <= 1:
        return embed_query(query)
    # Join with ". " so the embedding model treats them as related phrases
    # in one passage, not a single concatenated noun phrase.
    return embed_query(". ".join(expanded))
