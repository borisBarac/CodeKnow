"""LangChain embeddings factory and chunk embedding utilities.

Provides a provider-agnostic factory that returns any LangChain ``Embeddings``
instance, plus helpers that convert the project's ``Chunk`` / ``ChunkMap``
types into ``{hash: embedding}`` dicts ready for a vector store.

All supported providers use the OpenAI-compatible API via
``langchain_openai.OpenAIEmbeddings``:

- **docker** — local inference via Docker Model Runner (DMR exposes an
  ``/engines/v1`` OpenAI-compatible endpoint)
- **ollama** — local inference (Ollama exposes an ``/v1`` OpenAI-compatible
  endpoint)
- **openrouter** — cloud inference (OpenRouter exposes an OpenAI-compatible API)

Configuration is read from a ``.env`` file in the working directory:

- ``EMBEDDING_PROVIDER`` — ``"docker"`` (default), ``"ollama"``, or
  ``"openrouter"``
- ``EMBEDDING_MODEL`` — model name (default ``"ai/qwen3-embedding:4B"``)
- ``DOCKER_MODEL_RUNNER_URL``, ``OLLAMA_BASE_URL``, ``OPENROUTER_BASE_URL``,
  ``OPENROUTER_API_KEY`` — provider-specific connection details.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from codeknow.schemas import Chunk, ChunkMap

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate for provider-agnostic request budgeting."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3))


def _batch_texts_for_embedding_requests(
    texts: list[str],
    *,
    max_tokens: int,
) -> list[list[str]]:
    """Group existing chunk texts into embedding requests under a token budget.

    This does *not* create or resize CodeKnow source chunks. Source chunks are
    produced earlier by ``codeknow.chunking`` using line-based/AST-aware rules.
    At this point each string in ``texts`` is already one chunk's content.

    The purpose of this helper is only to avoid sending too many existing chunk
    texts in one OpenAI-compatible ``/embeddings`` request. If one individual
    chunk text exceeds ``max_tokens``, it is sent alone and a warning is logged;
    this layer intentionally does not split it into smaller chunks.
    """
    if max_tokens < 1:
        msg = "max_tokens must be greater than zero"
        raise ValueError(msg)

    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for text in texts:
        text_tokens = _estimate_tokens(text)
        if text_tokens > max_tokens:
            logger.warning(
                "Embedding text estimate exceeds token budget: %d > %d",
                text_tokens,
                max_tokens,
            )

        if current and current_tokens + text_tokens > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0

        current.append(text)
        current_tokens += text_tokens

    if current:
        batches.append(current)

    return batches


def _read_chunk_content(chunk: Chunk) -> str:
    p = Path(chunk.file)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        return ""

    start = max(chunk.start_line - 1, 0)
    end = min(chunk.end_line, len(lines))
    return "".join(lines[start:end])


try:
    from langchain_openai import OpenAIEmbeddings
except ImportError as exc:
    msg = (
        "langchain-openai is required for embeddings. "
        "Install with: pip install codeknow[embeddings]"
    )
    raise ImportError(msg) from exc


class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    provider: Literal["docker", "ollama", "openrouter"] = Field(
        default="docker", alias="EMBEDDING_PROVIDER"
    )
    model: str = Field(default="ai/qwen3-embedding:4B", alias="EMBEDDING_MODEL")
    base_url: str | None = None
    api_key: str | None = None
    docker_base_url: str = Field(
        default="http://localhost:12434/engines/v1", alias="DOCKER_MODEL_RUNNER_URL"
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434/v2", alias="OLLAMA_BASE_URL"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    request_chunk_size: int | None = Field(
        default=None, alias="EMBEDDING_REQUEST_CHUNK_SIZE"
    )
    # Request-budgeting controls how existing chunk texts are packed into
    # embedding requests. It does not affect source chunk size or overlap.
    max_request_tokens: int | None = Field(
        default=1800,
        alias="EMBEDDING_MAX_REQUEST_TOKENS",
    )
    token_safety_margin: int = Field(
        default=128,
        alias="EMBEDDING_TOKEN_SAFETY_MARGIN",
    )

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.provider == "docker":
            return self.docker_base_url
        if self.provider == "ollama":
            return self.ollama_base_url
        return self.openrouter_base_url

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.provider == "docker":
            return "not-needed"
        if self.provider == "ollama":
            return "ollama"
        if self.openrouter_api_key:
            return self.openrouter_api_key
        msg = (
            "OpenRouter requires an API key. "
            "Set OPENROUTER_API_KEY env var or pass api_key in config."
        )
        raise ValueError(msg)


def create_embeddings(config: EmbeddingConfig) -> Embeddings:
    kwargs: dict = {
        "model": config.model,
        "api_key": config.resolved_api_key(),
        "base_url": config.resolved_base_url(),
    }
    if config.provider in ("docker", "ollama"):
        kwargs["check_embedding_ctx_length"] = False
    if config.request_chunk_size is not None:
        kwargs["chunk_size"] = config.request_chunk_size
    return OpenAIEmbeddings(**kwargs)


def embed_texts(
    texts: list[str],
    embeddings: Embeddings,
    *,
    max_request_tokens: int | None = None,
    token_safety_margin: int = 0,
) -> list[list[float]]:
    """Embed existing texts, optionally splitting requests by token budget.

    ``texts`` are already-built chunk contents. Token-budget splitting only
    controls the size of each outbound embedding request; it does not create,
    resize, or overlap CodeKnow chunks.
    """
    if not texts:
        return []

    if max_request_tokens is None:
        return embeddings.embed_documents(texts)

    effective_max_tokens = max(1, max_request_tokens - token_safety_margin)
    vectors: list[list[float]] = []
    for batch in _batch_texts_for_embedding_requests(
        texts,
        max_tokens=effective_max_tokens,
    ):
        vectors.extend(embeddings.embed_documents(batch))
    return vectors


def embed_chunks(
    chunks: list[Chunk],
    embeddings: Embeddings,
) -> dict[str, list[float]]:
    if not chunks:
        return {}

    texts = [_read_chunk_content(c) for c in chunks]
    vectors = embeddings.embed_documents(texts)

    return {
        chunk.hash: vector
        for chunk, vector in zip(chunks, vectors, strict=False)
        if vector
    }


def embed_chunk_map(
    chunk_map: ChunkMap,
    embeddings: Embeddings,
) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for file_chunks in chunk_map.values():
        result.update(embed_chunks(file_chunks, embeddings))
    return result
