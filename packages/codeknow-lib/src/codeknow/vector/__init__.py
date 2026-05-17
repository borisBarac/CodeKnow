"""Vector integration — embeddings, vector store abstraction, and ChromaDB backend.

Quick start::

    from codeknow.vector import (
        EmbeddingConfig,
        ChromaConfig,
        ChromaStore,
        create_embeddings,
    )

    # Reads EMBEDDING_PROVIDER and EMBEDDING_MODEL from .env
    # (defaults: ollama / qwen3-embedding:4b)
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
from .metadata import build_chunk_metadata
from .pipeline_stage import embed
from .store import SearchResult, VectorStore

with contextlib.suppress(ImportError):
    from .chroma import ChromaConfig, ChromaStore
    from .multi_search import multi_graph_search
    from .search import HybridSearchResponse, HybridSearchResult, hybrid_search

__all__ = [
    "ChromaConfig",
    "ChromaStore",
    "EmbeddingConfig",
    "HybridSearchResponse",
    "HybridSearchResult",
    "SearchResult",
    "VectorStore",
    "build_chunk_metadata",
    "create_embeddings",
    "embed",
    "embed_chunk_map",
    "embed_chunks",
    "embed_texts",
    "hybrid_search",
    "multi_graph_search",
]
