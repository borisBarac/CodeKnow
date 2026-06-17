"""ChromaDB vector-store backend for persisting and searching chunk embeddings.

Wraps a remote or container-hosted ChromaDB instance (``HttpClient``) and
implements the :class:`~codeknow.langchain.store.VectorStore` protocol.

The store owns a LangChain ``Embeddings`` instance so callers never need
to pass it per-call.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .embeddings import EmbeddingConfig
from .ingest import embed_chunk_batches
from .store import SearchResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from chromadb.api.models.Collection import Collection
    from langchain_core.embeddings import Embeddings

    from codeknow.schemas import Chunk, ChunkMap


try:
    import chromadb
except ImportError as exc:
    msg = (
        "chromadb is required for vector storage. "
        "Install with: pip install codeknow[vector]"
    )
    raise ImportError(msg) from exc

try:
    import langchain_core  # noqa: F401
except ImportError as exc:
    msg = (
        "langchain-core is required for vector storage. "
        "Install with: pip install codeknow[embeddings]"
    )
    raise ImportError(msg) from exc

logger = logging.getLogger(__name__)

DEFAULT_CHROMA_HOST = "localhost"
DEFAULT_CHROMA_PORT = 8018
DEFAULT_COLLECTION_NAME = "codeknow_chunks"


class ChromaConfig(BaseModel):
    host: str | None = None
    port: int | None = None
    ssl: bool = False
    collection_name: str = DEFAULT_COLLECTION_NAME
    tenant: str = chromadb.DEFAULT_TENANT
    database: str = chromadb.DEFAULT_DATABASE

    def resolved_host(self) -> str:
        return self.host or os.environ.get("CHROMA_HOST", DEFAULT_CHROMA_HOST)

    def resolved_port(self) -> int:
        if self.port is not None:
            return self.port
        return int(os.environ.get("CHROMA_PORT", str(DEFAULT_CHROMA_PORT)))


class ChromaStore:
    """ChromaDB-backed implementation of the ``VectorStore`` protocol.

    Parameters
    ----------
    config:
        Connection and collection settings.  Defaults are sensible for a
        local Docker Chroma instance.
    embeddings:
        A LangChain ``Embeddings`` instance used for all vectorisation.

    """

    def __init__(
        self,
        config: ChromaConfig | None = None,
        embeddings: Embeddings | None = None,
        embedding_config: EmbeddingConfig | None = None,
    ) -> None:
        if embeddings is None:
            msg = (
                "An Embeddings instance is required. "
                "Create one with create_embeddings() from embeddings.py."
            )
            raise ValueError(msg)
        self._config = config or ChromaConfig()
        self._embeddings = embeddings
        self._embedding_config = embedding_config or EmbeddingConfig()
        self._client: chromadb.HttpClient | None = None  # type: ignore[valid-type]
        self._collection: Collection | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> chromadb.HttpClient:  # type: ignore[valid-type]
        if self._client is None:
            self._client = chromadb.HttpClient(
                host=self._config.resolved_host(),
                port=self._config.resolved_port(),
                ssl=self._config.ssl,
                tenant=self._config.tenant,
                database=self._config.database,
            )
        return self._client

    def _get_or_create_collection(self) -> Collection:
        if self._collection is None:
            self._collection = self._get_client().get_or_create_collection(  # type: ignore[attr-defined]
                name=self._config.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def reset(self) -> None:
        """Drop cached client/collection so they are re-created on next use."""
        self._collection = None

    def drop_collection(self) -> None:
        """Delete the underlying Chroma collection and reset cached refs.

        Safe to call when the collection does not exist.
        """
        with contextlib.suppress(Exception):
            self._get_client().delete_collection(  # type: ignore[attr-defined]
                name=self._config.collection_name
            )
        self._collection = None

    # ------------------------------------------------------------------
    # VectorStore protocol methods
    # ------------------------------------------------------------------

    def store_chunks(
        self,
        chunks: list[Chunk],
        *,
        batch_size: int = 50,
        slug: str | None = None,
        extra_metadata: dict[str, dict] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        if not chunks:
            return 0

        total = len(chunks)
        stored = 0
        collection = self._get_or_create_collection()
        for batch in embed_chunk_batches(
            chunks,
            self._embeddings,
            self._embedding_config,
            batch_size=batch_size,
            slug=slug,
            extra_metadata=extra_metadata,
        ):
            collection.upsert(
                ids=batch.ids,
                embeddings=batch.vectors,  # type: ignore[arg-type]
                documents=batch.texts,
                metadatas=batch.metadatas,  # type: ignore[arg-type]
            )
            stored += len(batch.ids)
            if on_progress is not None:
                on_progress(stored, total)

        logger.info(
            "Stored %d chunk embeddings in '%s'",
            stored,
            self._config.collection_name,
        )
        return stored

    def store_chunk_map(
        self,
        chunk_map: ChunkMap,
        *,
        batch_size: int = 50,
        slug: str | None = None,
        extra_metadata: dict[str, dict] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        all_chunks: list[Chunk] = []
        for file_chunks in chunk_map.values():
            all_chunks.extend(file_chunks)
        return self.store_chunks(
            all_chunks,
            batch_size=batch_size,
            slug=slug,
            extra_metadata=extra_metadata,
            on_progress=on_progress,
        )

    def search(
        self,
        query: str | list[float],
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if isinstance(query, str):
            query_vector = self._embeddings.embed_query(query)
        else:
            query_vector = query

        collection = self._get_or_create_collection()

        kwargs: dict[str, Any] = {
            "query_embeddings": [query_vector],
            "n_results": n_results,
        }
        if where is not None:
            kwargs["where"] = where
        if where_document is not None:
            kwargs["where_document"] = where_document

        results = collection.query(**kwargs)

        search_results: list[SearchResult] = []
        ids = results.get("ids", [[]])[0]  # type: ignore[index]
        distances = results.get("distances", [[]])[0]  # type: ignore[index]
        documents = results.get("documents", [[]])[0]  # type: ignore[index]
        metadatas = results.get("metadatas", [[]])[0]  # type: ignore[index]

        for i, chunk_hash in enumerate(ids):
            search_results.append(
                SearchResult(
                    hash=chunk_hash,
                    distance=distances[i] if i < len(distances) else None,
                    document=documents[i] if i < len(documents) else None,
                    metadata=metadatas[i] if i < len(metadatas) else None,  # type: ignore[arg-type]
                )
            )

        return search_results

    def delete(self, chunk_hashes: list[str]) -> None:
        if not chunk_hashes:
            return
        collection = self._get_or_create_collection()
        collection.delete(ids=chunk_hashes)
        logger.info(
            "Deleted %d chunks from '%s'",
            len(chunk_hashes),
            self._config.collection_name,
        )

    def delete_by_file(self, file: str) -> int:
        collection = self._get_or_create_collection()
        results = collection.get(where={"file": file})
        ids: list[str] = results.get("ids", [])  # type: ignore[assignment]
        if not ids:
            return 0
        collection.delete(ids=ids)
        logger.info(
            "Deleted %d chunks for file '%s' from '%s'",
            len(ids),
            file,
            self._config.collection_name,
        )
        return len(ids)

    def delete_by_slug(self, slug: str) -> int:
        collection = self._get_or_create_collection()
        results = collection.get(where={"slug": slug})
        ids: list[str] = results.get("ids", [])  # type: ignore[assignment]
        if not ids:
            return 0
        collection.delete(ids=ids)
        logger.info(
            "Deleted %d chunks for slug '%s' from '%s'",
            len(ids),
            slug,
            self._config.collection_name,
        )
        return len(ids)

    def get_by_ids(self, chunk_hashes: list[str]) -> list[SearchResult]:
        """Fetch chunk content + metadata by hash."""
        if not chunk_hashes:
            return []
        collection = self._get_or_create_collection()
        results = collection.get(ids=chunk_hashes, include=["documents", "metadatas"])
        search_results: list[SearchResult] = []
        ids: list[str] = results.get("ids", []) or []
        documents: list[str] = results.get("documents", []) or []
        metadatas_raw = results.get("metadatas", []) or []
        metadatas: list[dict[str, Any]] = [
            dict(m) if m is not None else {} for m in metadatas_raw
        ]
        for i, chunk_hash in enumerate(ids):
            search_results.append(
                SearchResult(
                    hash=chunk_hash,
                    document=documents[i] if i < len(documents) else None,
                    metadata=metadatas[i] if i < len(metadatas) else None,
                )
            )
        return search_results

    def count(self) -> int:
        return self._get_or_create_collection().count()
