from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from codeknow.vector.embeddings import (
    EmbeddingConfig,
    _read_chunk_content,
    embed_texts,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from langchain_core.embeddings import Embeddings

    from codeknow.schemas import Chunk, ChunkMap


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
            if chunk.vector_id in seen:
                continue
            seen.add(chunk.vector_id)
            chunks.append(chunk)
    return chunks


def embed_chunk_batches(
    chunks: list[Chunk],
    embeddings: Embeddings,
    embedding_config: EmbeddingConfig | None = None,
    *,
    batch_size: int = 50,
    slug: str | None = None,
    extra_metadata: dict[str, dict[str, Any]] | None = None,
    repo_root: Path | None = None,
) -> Iterator[EmbeddedChunkBatch]:
    """Yield embedded chunk batches ready for a vector-store sink."""
    if batch_size < 1:
        msg = "batch_size must be greater than zero"
        raise ValueError(msg)

    config = embedding_config or EmbeddingConfig()
    unique_chunks: list[Chunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.vector_id in seen:
            continue
        seen.add(chunk.vector_id)
        unique_chunks.append(chunk)

    for offset in range(0, len(unique_chunks), batch_size):
        batch = unique_chunks[offset : offset + batch_size]
        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict[str, Any]] = []
        contexts: list[str | None] = []

        for chunk in batch:
            content = _read_chunk_content(chunk, repo_root)
            if not content.strip():
                continue

            ids.append(chunk.vector_id)
            texts.append(content)
            contexts.append(
                f"chunk={chunk.vector_id} "
                f"file={chunk.file}:{chunk.start_line}-{chunk.end_line} "
                f"provider={config.provider}"
            )
            meta: dict[str, Any] = {
                "file": chunk.file,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content_hash": chunk.hash,
            }
            if slug is not None:
                meta["slug"] = slug
            if extra_metadata is not None and chunk.vector_id in extra_metadata:
                meta.update(extra_metadata[chunk.vector_id])
            metadatas.append(meta)

        if not ids:
            continue

        embedding_requests = 0

        def _count_request() -> None:
            nonlocal embedding_requests
            embedding_requests += 1

        vectors = embed_texts(
            texts,
            embeddings,
            model=config.model,
            max_embedding_split_depth=config.max_embedding_split_depth,
            contexts=contexts,
            on_request=_count_request,
            skip_context_length_errors=True,
        )
        embedded = [
            (chunk_id, text, metadata, vector)
            for chunk_id, text, metadata, vector in zip(
                ids,
                texts,
                metadatas,
                vectors,
                strict=False,
            )
            if vector
        ]
        if not embedded:
            continue

        embedded_ids, embedded_texts, embedded_metadatas, embedded_vectors = zip(
            *embedded,
            strict=True,
        )
        yield EmbeddedChunkBatch(
            ids=list(embedded_ids),
            texts=list(embedded_texts),
            metadatas=list(embedded_metadatas),
            vectors=list(embedded_vectors),
            embedding_requests=embedding_requests,
        )


def embed_chunk_map_only(
    chunk_map: ChunkMap,
    embeddings: Embeddings,
    embedding_config: EmbeddingConfig | None = None,
    *,
    batch_size: int = 50,
    repo_root: Path | None = None,
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
        repo_root=repo_root,
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
