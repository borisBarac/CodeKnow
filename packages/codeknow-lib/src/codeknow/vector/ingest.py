from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from codeknow.vector.embeddings import (
    EmbeddingConfig,
    _batch_texts_for_embedding_requests,
    _estimate_tokens,
    _read_chunk_content,
    embed_texts,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.embeddings import Embeddings

    from codeknow.schemas import Chunk, ChunkMap

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbedChunkMapStats:
    chunks_seen: int
    chunks_embedded: int
    embedding_requests: int
    duration_seconds: float
    provider: str
    model: str


@dataclass(frozen=True)
class EmbeddedChunkBatch:
    ids: list[str]
    texts: list[str]
    metadatas: list[dict[str, Any]]
    vectors: list[list[float]]
    embedding_requests: int


def _flatten_unique_chunks(chunk_map: ChunkMap) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for file_chunks in chunk_map.values():
        for chunk in file_chunks:
            if chunk.hash in seen:
                continue
            seen.add(chunk.hash)
            chunks.append(chunk)
    return chunks


def _embedding_request_count(
    texts: list[str],
    embedding_config: EmbeddingConfig,
) -> int:
    if not texts:
        return 0
    if embedding_config.max_request_tokens is None:
        return 1

    effective_max_tokens = max(
        1,
        embedding_config.max_request_tokens - embedding_config.token_safety_margin,
    )
    return len(
        _batch_texts_for_embedding_requests(
            texts,
            max_tokens=effective_max_tokens,
        )
    )


def _effective_max_tokens(embedding_config: EmbeddingConfig) -> int | None:
    if embedding_config.max_request_tokens is None:
        return None
    return max(
        1,
        embedding_config.max_request_tokens - embedding_config.token_safety_margin,
    )


def embed_chunk_batches(
    chunks: list[Chunk],
    embeddings: Embeddings,
    embedding_config: EmbeddingConfig | None = None,
    *,
    batch_size: int = 50,
    slug: str | None = None,
    extra_metadata: dict[str, dict[str, Any]] | None = None,
) -> Iterator[EmbeddedChunkBatch]:
    """Yield embedded chunk batches ready for a vector-store sink."""
    if batch_size < 1:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    config = embedding_config or EmbeddingConfig()
    effective_max_tokens = _effective_max_tokens(config)
    unique_chunks: list[Chunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.hash in seen:
            continue
        seen.add(chunk.hash)
        unique_chunks.append(chunk)

    for offset in range(0, len(unique_chunks), batch_size):
        batch = unique_chunks[offset : offset + batch_size]
        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for chunk in batch:
            content = _read_chunk_content(chunk)
            if not content.strip():
                continue

            token_estimate = _estimate_tokens(content)
            if (
                effective_max_tokens is not None
                and token_estimate > effective_max_tokens
            ):
                logger.warning(
                    "Skipping chunk %s from %s:%d-%d: token estimate %d exceeds "
                    "embedding request budget %d",
                    chunk.hash,
                    chunk.file,
                    chunk.start_line,
                    chunk.end_line,
                    token_estimate,
                    effective_max_tokens,
                )
                continue

            ids.append(chunk.hash)
            texts.append(content)
            meta: dict[str, Any] = {
                "file": chunk.file,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
            }
            if slug is not None:
                meta["slug"] = slug
            if extra_metadata is not None and chunk.hash in extra_metadata:
                meta.update(extra_metadata[chunk.hash])
            metadatas.append(meta)

        if not ids:
            continue

        embedding_requests = _embedding_request_count(texts, config)
        vectors = embed_texts(
            texts,
            embeddings,
            max_request_tokens=config.max_request_tokens,
            token_safety_margin=config.token_safety_margin,
        )
        yield EmbeddedChunkBatch(
            ids=ids,
            texts=texts,
            metadatas=metadatas,
            vectors=vectors,
            embedding_requests=embedding_requests,
        )


def embed_chunk_map_only(
    chunk_map: ChunkMap,
    embeddings: Embeddings,
    embedding_config: EmbeddingConfig | None = None,
    *,
    batch_size: int = 50,
) -> EmbedChunkMapStats:
    """Embed chunk contents without graph metadata or vector-store writes.

    This is the graph-independent embedding path intended for load tests and
    raw provider benchmarking. It mirrors the production request budgeting used
    by Chroma ingestion but intentionally discards vectors after generation.
    """
    config = embedding_config or EmbeddingConfig()
    chunks = _flatten_unique_chunks(chunk_map)
    chunks_embedded = 0
    embedding_requests = 0
    start = time.monotonic()

    for batch in embed_chunk_batches(
        chunks,
        embeddings,
        config,
        batch_size=batch_size,
    ):
        embedding_requests += batch.embedding_requests
        chunks_embedded += len(batch.vectors)

    duration = time.monotonic() - start
    return EmbedChunkMapStats(
        chunks_seen=len(chunks),
        chunks_embedded=chunks_embedded,
        embedding_requests=embedding_requests,
        duration_seconds=round(duration, 3),
        provider=config.provider,
        model=config.model,
    )
