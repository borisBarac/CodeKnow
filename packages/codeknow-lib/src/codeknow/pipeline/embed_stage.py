from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from codeknow.pipeline.metadata import build_chunk_metadata
from codeknow.schemas import vector_ids_digest
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import (
    EmbeddingConfig,
    create_embeddings,
    read_chunk_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from codeknow.pipeline import PipelineResult

logger = logging.getLogger(__name__)


def embed(
    result: PipelineResult,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    **kwargs: Any,
) -> PipelineResult:
    config = result.config

    if config.no_embed:
        return result

    slug = config.slug

    embed_config = EmbeddingConfig(
        EMBEDDING_PROVIDER=config.embed_provider,
        EMBEDDING_MODEL=config.embed_model,
        base_url=config.embed_base_url,
    )
    embeddings = create_embeddings(embed_config)

    collection_name = (
        result.collection_name or config.chroma_collection or f"codeknow_{slug}"
    )
    chroma_config = ChromaConfig(
        host=config.chroma_host,
        port=config.chroma_port,
        collection_name=collection_name,
    )

    store = ChromaStore(
        config=chroma_config,
        embeddings=embeddings,
        embedding_config=embed_config,
    )

    extra_metadata = build_chunk_metadata(result)

    start = time.monotonic()
    copied = 0
    changed_map = result.chunk_map
    if result.changed_paths is not None and result.prior_collection_name is not None:
        unchanged_chunks = [
            chunk
            for path, chunks in result.chunk_map.items()
            if path not in result.changed_paths
            for chunk in chunks
        ]
        changed_map = {
            path: chunks
            for path, chunks in result.chunk_map.items()
            if path in result.changed_paths
        }
        source_config = chroma_config.model_copy(
            update={"collection_name": result.prior_collection_name}
        )
        source = ChromaStore(
            config=source_config,
            embeddings=embeddings,
            embedding_config=embed_config,
        )
        copied = store.copy_from(
            source,
            [chunk.vector_id for chunk in unchanged_chunks],
        )

    try:
        stored = store.store_chunk_map(
            changed_map,
            batch_size=config.embed_batch_size,
            slug=slug,
            extra_metadata=extra_metadata,
            on_progress=on_progress,
            repo_root=result.repo_root,
        )
        refreshed_metadata: dict[str, dict[str, Any]] = {}
        for chunks in result.chunk_map.values():
            for chunk in chunks:
                if not read_chunk_content(chunk, result.repo_root).strip():
                    continue
                metadata = {
                    "file": chunk.file,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "content_hash": chunk.hash,
                    "slug": slug,
                }
                metadata.update(extra_metadata.get(chunk.vector_id, {}))
                refreshed_metadata[chunk.vector_id] = metadata
        store.update_metadata(refreshed_metadata)
        store.validate_ids(set(refreshed_metadata))
    except Exception:
        store.drop_collection()
        raise
    duration = time.monotonic() - start

    embed_stats: dict[str, Any] = {
        "chunks_embedded": stored,
        "chunks_copied": copied,
        "vector_ids_digest": vector_ids_digest(set(refreshed_metadata)),
        "provider": config.embed_provider,
        "model": config.embed_model,
        "batch_size": config.embed_batch_size,
        "duration_seconds": round(duration, 3),
    }

    logger.info(
        "Embed stage complete: %d chunks in %.1fs via %s/%s",
        stored,
        duration,
        config.embed_provider,
        config.embed_model,
    )

    return replace(result, embed_stats=embed_stats)
