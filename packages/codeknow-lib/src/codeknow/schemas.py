"""Core data model — Pydantic v2 schemas for nodes, edges, chunks, and pipeline I/O.

These schemas extend the graph output format with:
- ``chunks[]`` on nodes (link to code chunk hashes)
- ``community`` on nodes (Leiden community ID)
- ``confidence_score`` on edges (numeric 0.0–1.0)

All new fields are Optional with defaults — old graph ``graph.json`` files
parse without error.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class ConfidenceLabel(str, Enum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


class Chunk(BaseModel):
    """A contiguous range of lines in a source file, identified by SHA-256 hash.

    The ``hash`` field is the join key between graph nodes and vector search
    results — both systems reference the same chunk hashes.
    """

    file: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @field_validator("end_line")
    @classmethod
    def end_after_start(cls, v: int, info: ValidationInfo) -> int:
        if info.data.get("start_line") and v < info.data["start_line"]:
            msg = "end_line must be >= start_line"
            raise ValueError(msg)
        return v


class ChunkRef(BaseModel):
    """A lightweight chunk reference stored inside a node — just the hash."""

    hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class Node(BaseModel):
    """A graph node representing a code entity (class, function, module, concept).

    Extends graph's node format with ``chunks`` and ``community``.
    All fields except ``id`` and ``label`` are optional for backward compatibility.
    """

    id: str
    label: str
    file_type: str = "code"
    source_file: str = ""
    source_location: str = ""
    chunks: list[ChunkRef] = Field(default_factory=list)
    community: int | None = None
    end_line: int | None = None

    model_config = {"extra": "allow"}


class Edge(BaseModel):
    """A directed relationship between two nodes.

    Extends graph's edge format with ``confidence_score``.
    """

    source: str
    target: str
    relation: str
    confidence: ConfidenceLabel = ConfidenceLabel.EXTRACTED
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    source_file: str = ""
    source_location: str = ""
    weight: float = 1.0

    model_config = {"extra": "allow"}


class ExtractionResult(BaseModel):
    """Output of an extraction stage (AST or semantic)."""

    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    hyperedges: list[dict] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class FileDiscovery(BaseModel):
    """Output of the detect() stage — classified file lists and corpus stats."""

    files: dict[str, list[str]] = Field(default_factory=dict)
    total_files: int = 0
    total_words: int = 0
    needs_graph: bool = False
    warning: str | None = None
    skipped_sensitive: list[str] = Field(default_factory=list)


class EmbedStats(BaseModel):
    chunks_embedded: int
    provider: str
    model: str
    duration_seconds: float


ChunkMap = dict[str, list[Chunk]]
"""file path → list of ``Chunk`` objects. Stored as ``chunk_map.json``."""

CommunityMap = dict[int, list[str]]
"""community_id → list of node IDs. Output of Leiden clustering."""


class HybridSearchResult(BaseModel):
    chunk_hash: str
    file: str
    start_line: int
    end_line: int
    content: str
    distance: float | None = None
    node_labels: list[str] = Field(default_factory=list)
    community_ids: list[int] = Field(default_factory=list)
    provenance: str = "vector"
    graph_path: list[str] | None = None
    slug: str | None = None


class HybridSearchResponse(BaseModel):
    """Response from a hybrid search query."""

    query: str
    vector_hits: int
    graph_expanded: int
    results: list[HybridSearchResult]


class RepoMetadata(BaseModel):
    github_ssh_url: str
    slug: str
    commit_hash: str
    built_at: str
    node_count: int
    edge_count: int
    community_count: int
    health: str | None = None
    build_status: str | None = None
    build_progress: int | None = None

    model_config = {"extra": "allow"}


class ListReposResponse(BaseModel):
    repos: list[RepoMetadata]
    total: int
    page: int
    page_size: int
    errors: list[dict[str, str]] = []
