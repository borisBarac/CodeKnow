"""Vector store abstraction — Protocol and shared result type.

Defines a duck-typed ``VectorStore`` contract that any backend (Chroma,
FAISS, pgvector, …) can satisfy.  ``SearchResult`` is the common return
type for similarity queries.

The protocol is *structural*: implementations need only contain methods
with matching signatures — no inheritance required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from codeknow.schemas import Chunk, ChunkMap


class SearchResult(BaseModel):
    hash: str
    distance: float | None = None
    document: str | None = None
    metadata: dict[str, Any] | None = None


@runtime_checkable
class VectorStore(Protocol):
    """Structural contract for a chunk-level vector store.

    Implementations must provide these methods; they do **not** need to
    explicitly subclass this Protocol.
    """

    def store_chunks(
        self,
        chunks: list[Chunk],
        *,
        batch_size: int = 500,
        slug: str | None = None,
        extra_metadata: dict[str, dict] | None = None,
    ) -> int:
        """Persist chunk embeddings.  Returns the number of chunks stored."""
        ...

    def store_chunk_map(
        self,
        chunk_map: ChunkMap,
        *,
        batch_size: int = 500,
        slug: str | None = None,
        extra_metadata: dict[str, dict] | None = None,
    ) -> int:
        """Flatten *chunk_map* and persist all chunks.  Returns count stored."""
        ...

    def search(
        self,
        query: str | list[float],
        *,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Query the store by text or pre-computed vector."""
        ...

    def delete(self, chunk_hashes: list[str]) -> None:
        """Remove chunks by their SHA-256 hashes."""
        ...

    def delete_by_file(self, file: str) -> int:
        """Remove all chunks belonging to *file*.  Returns count deleted."""
        ...

    def delete_by_slug(self, slug: str) -> int:
        """Remove all chunks belonging to *slug*.  Returns count deleted."""
        ...

    def count(self) -> int:
        """Return the total number of stored chunks."""
        ...
