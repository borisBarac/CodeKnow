from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

    from codeknow.pipeline.config import PipelineConfig
    from codeknow.schemas import ChunkMap, CommunityMap, ExtractionResult, FileDiscovery


@dataclass(frozen=True)
class PipelineResult:
    """Container for the full pipeline output."""

    graph: nx.Graph
    communities: CommunityMap
    chunk_map: ChunkMap
    discovery: dict
    stats: dict[str, Any]
    config: PipelineConfig
    embed_stats: dict | None = None
    graph_path: Path | None = None


class ResolveFn(Protocol):
    def __call__(self, config: PipelineConfig, **kwargs: Any) -> Path: ...


class DetectFn(Protocol):
    def __call__(self, root: Path, **kwargs: Any) -> FileDiscovery: ...


class ExtractAstFn(Protocol):
    def __call__(
        self, files: dict[str, list[str]], **kwargs: Any
    ) -> ExtractionResult: ...


class ExtractSemanticFn(Protocol):
    def __call__(
        self, files: dict[str, list[str]], **kwargs: Any
    ) -> ExtractionResult: ...


class BuildGraphFn(Protocol):
    def __call__(self, extractions: list[dict], **kwargs: Any) -> nx.Graph: ...


class MapChunksFn(Protocol):
    def __call__(
        self, graph: nx.Graph, files: dict[str, list[str]], **kwargs: Any
    ) -> tuple[nx.Graph, ChunkMap]: ...


class ClusterFn(Protocol):
    def __call__(self, graph: nx.Graph, **kwargs: Any) -> CommunityMap: ...


class EmbedFn(Protocol):
    def __call__(self, result: PipelineResult, **kwargs: Any) -> PipelineResult: ...


STAGES = [
    "resolve",
    "detect",
    "extract_ast",
    "extract_semantic",
    "build_graph",
    "map_chunks",
    "cluster",
    "embed",
    "serve",
]

STAGE_IO: dict[str, dict[str, str]] = {
    "resolve": {
        "input": "PipelineConfig (GitHub repo URL)",
        "output": "Path (local repo root)",
    },
    "detect": {
        "input": "Path (corpus root)",
        "output": "FileDiscovery",
    },
    "extract_ast": {
        "input": "FileDiscovery.files",
        "output": (
            "ExtractionResult (structural entities via"
            " tree-sitter, confidence=EXTRACTED)"
        ),
    },
    "extract_semantic": {
        "input": "FileDiscovery.files",
        "output": (
            "ExtractionResult (conceptual entities via"
            " LangChain, confidence=INFERRED|AMBIGUOUS)"
        ),
    },
    "build_graph": {
        "input": "[ExtractionResult]",
        "output": "NetworkX Graph (merged AST + semantic, deduplicated nodes)",
    },
    "map_chunks": {
        "input": "NetworkX Graph + FileDiscovery.files",
        "output": "(enriched NetworkX Graph with chunks[], ChunkMap)",
    },
    "cluster": {
        "input": "NetworkX Graph",
        "output": "CommunityMap (Leiden community ID → [node_ids])",
    },
    "embed": {
        "input": "PipelineResult (graph + chunk_map + communities)",
        "output": "PipelineResult (with embed_stats, side effect: upsert to ChromaDB)",
    },
    "serve": {
        "input": "NetworkX Graph + CommunityMap",
        "output": "QueryEngine (BFS/DFS/shortest_path/explain)",
    },
}
