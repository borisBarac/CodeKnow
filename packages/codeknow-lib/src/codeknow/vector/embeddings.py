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
- ``EMBEDDING_NUM_CTX`` — embedding context window (default ``4096``)
- ``DOCKER_MODEL_RUNNER_URL``, ``OLLAMA_BASE_URL``, ``OPENROUTER_BASE_URL``,
  ``OPENROUTER_API_KEY`` — provider-specific connection details.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeknow.vector.embedding_errors import (
    is_context_length_error,
    is_rate_limit_error,
    is_transient_embedding_error,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.embeddings import Embeddings

    from codeknow.schemas import Chunk, ChunkMap

logger = logging.getLogger(__name__)


class EmbeddingContextLengthError(RuntimeError):
    """Raised when a chunk cannot be split small enough for the provider."""


DEFAULT_MAX_EMBEDDING_SPLIT_DEPTH = int(
    os.environ.get("EMBEDDING_MAX_SPLIT_DEPTH", "3")
)


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
    num_ctx: int = Field(default=4096, alias="EMBEDDING_NUM_CTX")
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
    max_embedding_split_depth: int = Field(
        default=DEFAULT_MAX_EMBEDDING_SPLIT_DEPTH,
        alias="EMBEDDING_MAX_SPLIT_DEPTH",
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


def _split_text_in_half(text: str) -> tuple[str, str] | None:
    if len(text) < 2:
        return None

    lines = text.splitlines(keepends=True)
    if len(lines) > 1:
        midpoint = len(text) / 2
        best_index: int | None = None
        best_distance: float | None = None
        cursor = 0
        for index, line in enumerate(lines[:-1], start=1):
            cursor += len(line)
            distance = abs(cursor - midpoint)
            if best_distance is None or distance < best_distance:
                best_index = index
                best_distance = distance
        if best_index is not None:
            left = "".join(lines[:best_index])
            right = "".join(lines[best_index:])
            if left and right:
                return left, right

    midpoint = len(text) // 2
    left = text[:midpoint]
    right = text[midpoint:]
    if not left or not right:
        return None
    return left, right


def _merge_split_embedding_vectors(
    vectors: list[list[float]],
    text_lengths: list[int],
) -> list[float]:
    if not vectors:
        return []
    if len(vectors) != len(text_lengths):
        msg = "vectors and text_lengths must have the same length"
        raise ValueError(msg)

    total_weight = sum(max(text_length, 1) for text_length in text_lengths)
    dimensions = len(vectors[0])
    merged = [0.0] * dimensions
    for vector, raw_text_length in zip(vectors, text_lengths, strict=True):
        weight = max(raw_text_length, 1)
        for index, value in enumerate(vector):
            merged[index] += value * weight

    return [value / total_weight for value in merged]


def _format_context(context: str | None) -> str:
    return context or "unknown chunk"


def _handle_context_length_failure(
    message: str,
    context_error: Exception | None,
    *,
    skip_context_length_errors: bool,
) -> list[list[float]]:
    if skip_context_length_errors:
        logger.warning(message)
        # Empty vector is the ingest-layer sentinel for "skip this text".
        return [[]]
    raise EmbeddingContextLengthError(message) from context_error


@dataclass(frozen=True)
class _EmbeddingRetryOptions:
    model: str | None
    max_split_depth: int
    on_request: Callable[[], None] | None
    skip_context_length_errors: bool


@dataclass(frozen=True)
class _EmbeddingRetryRequest:
    texts: list[str]
    chunk_contexts: list[str | None]
    split_depth: int


def _embed_documents_with_retry(
    texts: list[str],
    embeddings: Embeddings,
    on_request: Callable[[], None] | None,
) -> list[list[float]]:
    transient_attempts = 0
    while True:
        if on_request is not None:
            on_request()
        try:
            return embeddings.embed_documents(texts)
        except Exception as exc:
            if is_rate_limit_error(exc):
                time.sleep(5)
                continue
            if not is_transient_embedding_error(exc) or transient_attempts >= 3:
                raise
            transient_attempts += 1
            time.sleep(2 * transient_attempts)


def _embed_split_batch_after_context_error(
    request: _EmbeddingRetryRequest,
    embeddings: Embeddings,
    options: _EmbeddingRetryOptions,
) -> list[list[float]]:
    midpoint = len(request.texts) // 2
    left_vectors = _embed_with_context_length_recovery(
        _EmbeddingRetryRequest(
            texts=request.texts[:midpoint],
            chunk_contexts=request.chunk_contexts[:midpoint],
            split_depth=request.split_depth,
        ),
        embeddings,
        options,
    )
    right_vectors = _embed_with_context_length_recovery(
        _EmbeddingRetryRequest(
            texts=request.texts[midpoint:],
            chunk_contexts=request.chunk_contexts[midpoint:],
            split_depth=request.split_depth,
        ),
        embeddings,
        options,
    )
    return left_vectors + right_vectors


def _context_for_single_text(request: _EmbeddingRetryRequest) -> str:
    context = request.chunk_contexts[0] if request.chunk_contexts else None
    return _format_context(context)


def _split_and_embed_single_text(
    text: str,
    request: _EmbeddingRetryRequest,
    embeddings: Embeddings,
    options: _EmbeddingRetryOptions,
) -> list[list[float]]:
    split_vectors: list[list[float]] = []
    split_text_lengths: list[int] = []
    for part in _split_text_in_half(text) or ():
        split_vectors.extend(
            _embed_with_context_length_recovery(
                _EmbeddingRetryRequest(
                    texts=[part],
                    chunk_contexts=request.chunk_contexts,
                    split_depth=request.split_depth + 1,
                ),
                embeddings,
                options,
            )
        )
        split_text_lengths.append(len(part))

    if not split_vectors or any(not vector for vector in split_vectors):
        # Empty vector is the ingest-layer sentinel for "skip this text".
        return [[]]
    return [_merge_split_embedding_vectors(split_vectors, split_text_lengths)]


def _embed_single_text_after_context_error(
    request: _EmbeddingRetryRequest,
    embeddings: Embeddings,
    options: _EmbeddingRetryOptions,
    context_error: Exception,
) -> list[list[float]]:
    context = _context_for_single_text(request)
    if request.split_depth >= options.max_split_depth:
        msg = (
            "Embedding context length exceeded after split retries for "
            f"{context}; model={options.model or 'unknown'}"
        )
        return _handle_context_length_failure(
            msg,
            context_error,
            skip_context_length_errors=options.skip_context_length_errors,
        )

    if _split_text_in_half(request.texts[0]) is None:
        msg = (
            "Embedding context length exceeded for an unsplittable text "
            f"from {context}; model={options.model or 'unknown'}"
        )
        return _handle_context_length_failure(
            msg,
            context_error,
            skip_context_length_errors=options.skip_context_length_errors,
        )

    return _split_and_embed_single_text(
        request.texts[0],
        request,
        embeddings,
        options,
    )


def _recover_from_context_length_error(
    request: _EmbeddingRetryRequest,
    embeddings: Embeddings,
    options: _EmbeddingRetryOptions,
    context_error: Exception,
) -> list[list[float]]:
    if len(request.texts) > 1:
        return _embed_split_batch_after_context_error(request, embeddings, options)
    return _embed_single_text_after_context_error(
        request,
        embeddings,
        options,
        context_error,
    )


def _embed_with_context_length_recovery(
    request: _EmbeddingRetryRequest,
    embeddings: Embeddings,
    options: _EmbeddingRetryOptions,
) -> list[list[float]]:
    try:
        return _embed_documents_with_retry(
            request.texts,
            embeddings,
            options.on_request,
        )
    except Exception as exc:
        if not is_context_length_error(exc):
            raise
        return _recover_from_context_length_error(
            request,
            embeddings,
            options,
            exc,
        )


def _embed_request_with_retry(
    texts: list[str],
    embeddings: Embeddings,
    *,
    model: str | None,
    split_depth: int,
    max_split_depth: int,
    chunk_contexts: list[str | None],
    on_request: Callable[[], None] | None,
    skip_context_length_errors: bool,
) -> list[list[float]]:
    return _embed_with_context_length_recovery(
        _EmbeddingRetryRequest(
            texts=texts,
            chunk_contexts=chunk_contexts,
            split_depth=split_depth,
        ),
        embeddings,
        _EmbeddingRetryOptions(
            model=model,
            max_split_depth=max_split_depth,
            on_request=on_request,
            skip_context_length_errors=skip_context_length_errors,
        ),
    )


def embed_texts(
    texts: list[str],
    embeddings: Embeddings,
    *,
    model: str | None = None,
    max_embedding_split_depth: int = DEFAULT_MAX_EMBEDDING_SPLIT_DEPTH,
    contexts: list[str | None] | None = None,
    on_request: Callable[[], None] | None = None,
    skip_context_length_errors: bool = False,
) -> list[list[float]]:
    """Embed chunk contents, relying on the provider's context-length limits.

    Requests are not pre-split by an estimated token budget. Provider context
    limits are handled by ``_embed_request_with_retry`` via recursive retry,
    which halves failing batches and split halves of an oversized text.
    """
    if not texts:
        return []

    resolved_contexts: list[str | None] = (
        contexts if contexts is not None else [None] * len(texts)
    )
    if len(resolved_contexts) != len(texts):
        msg = "contexts must have the same length as texts"
        raise ValueError(msg)

    return _embed_request_with_retry(
        texts,
        embeddings,
        model=model,
        split_depth=0,
        max_split_depth=max_embedding_split_depth,
        chunk_contexts=resolved_contexts,
        on_request=on_request,
        skip_context_length_errors=skip_context_length_errors,
    )


def embed_chunks(
    chunks: list[Chunk],
    embeddings: Embeddings,
) -> dict[str, list[float]]:
    if not chunks:
        return {}

    texts = [_read_chunk_content(c) for c in chunks]
    vectors = embed_texts(texts, embeddings)

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
