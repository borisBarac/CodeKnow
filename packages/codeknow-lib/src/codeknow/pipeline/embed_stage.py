from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from codeknow.pipeline.metadata import build_chunk_metadata
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

if TYPE_CHECKING:
    from codeknow.pipeline import PipelineResult

logger = logging.getLogger(__name__)


def embed(result: PipelineResult, **kwargs: Any) -> PipelineResult:
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

    collection_name = config.chroma_collection or f"codeknow_{slug}"
    chroma_config = ChromaConfig(
        host=config.chroma_host,
        port=config.chroma_port,
        collection_name=collection_name,
    )

    store = ChromaStore(config=chroma_config, embeddings=embeddings)

    extra_metadata = build_chunk_metadata(result)

    start = time.monotonic()
    stored = store.store_chunk_map(
        result.chunk_map,
        slug=slug,
        extra_metadata=extra_metadata,
    )
    duration = time.monotonic() - start

    embed_stats: dict[str, Any] = {
        "chunks_embedded": stored,
        "provider": config.embed_provider,
        "model": config.embed_model,
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
