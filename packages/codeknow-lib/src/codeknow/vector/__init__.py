"""Vector integration — embeddings, vector store abstraction, and ChromaDB backend.

Quick start::

    from codeknow.vector import (
        EmbeddingConfig,
        ChromaConfig,
        ChromaStore,
        create_embeddings,
    )

    # Reads EMBEDDING_PROVIDER and EMBEDDING_MODEL from .env
    # (defaults: docker / ai/qwen3-embedding:4B)
    embeddings = create_embeddings(EmbeddingConfig())
    store = ChromaStore(ChromaConfig(), embeddings)
    store.store_chunk_map(chunk_map)
    results = store.search("authentication middleware")
"""

import contextlib

from .embeddings import (
    EmbeddingConfig,
    create_embeddings,
    embed_chunk_map,
    embed_chunks,
    embed_texts,
)
from .ingest import (
    EmbedChunkMapStats,
    EmbeddedChunkBatch,
    embed_chunk_batches,
    embed_chunk_map_only,
)
from .store import SearchResult, VectorStore

with contextlib.suppress(ImportError):
    from .chroma import ChromaConfig, ChromaStore
    from .search import (
        GraphSearcher,
        HybridSearchResponse,
        HybridSearchResult,
    )

__all__ = [
    "ChromaConfig",
    "ChromaStore",
    "EmbedChunkMapStats",
    "EmbeddedChunkBatch",
    "EmbeddingConfig",
    "GraphSearcher",
    "HybridSearchResponse",
    "HybridSearchResult",
    "SearchResult",
    "VectorStore",
    "create_embeddings",
    "embed_chunk_batches",
    "embed_chunk_map",
    "embed_chunk_map_only",
    "embed_chunks",
    "embed_texts",
]
