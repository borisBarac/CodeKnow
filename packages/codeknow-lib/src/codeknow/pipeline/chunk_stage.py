"""Pipeline stage: link graph nodes to code chunk hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from codeknow.chunking.chunker import build_chunk_map
from codeknow.paths import repository_path

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

    from codeknow.schemas import ChunkMap


def _parse_source_location(loc: str) -> int | None:
    if isinstance(loc, str) and loc.startswith("L"):
        try:
            return int(loc[1:])
        except ValueError:
            return None
    return None


def resolve_node_chunks(
    node_data: dict,
    chunk_map: ChunkMap,
) -> list[str]:
    """Find overlapping chunk hashes for a node.

    Given a node with ``source_file`` + ``source_location`` (start line),
    find all chunks whose line range overlaps with the node.
    """
    source_file = node_data.get("source_file", "")
    if not source_file:
        return []

    start_line = _parse_source_location(node_data.get("source_location", ""))
    if start_line is None:
        return []

    end_line = node_data.get("end_line", start_line)

    chunks = chunk_map.get(source_file, [])
    overlapping: list[str] = []
    for chunk in chunks:
        if chunk.start_line <= end_line and chunk.end_line >= start_line:
            overlapping.append(chunk.vector_id)
    return overlapping


def map_chunks(
    graph: nx.Graph,
    files: dict[str, list[str]],
    *,
    chunk_size: int = 100,
    overlap: int = 20,
    repo_root: Path | None = None,
    prior_chunk_map: ChunkMap | None = None,
    changed_paths: set[str] | frozenset[str] | None = None,
) -> tuple[nx.Graph, ChunkMap]:
    """Pipeline stage: link graph nodes to code chunk hashes.

    1. Chunk all source files → build ChunkMap
    2. For each node, find overlapping chunks
    3. Write ``chunks`` list onto each node

    Returns the enriched graph and the chunk_map.
    """
    root = repo_root
    if prior_chunk_map is None or changed_paths is None or root is None:
        chunk_map = build_chunk_map(files, chunk_size, overlap, repo_root=root)
    else:
        discovered = {
            repository_path(path, root) for paths in files.values() for path in paths
        }
        chunk_map = {
            path: chunks
            for path, chunks in prior_chunk_map.items()
            if path in discovered and path not in changed_paths
        }
        changed_files = {
            category: [
                path for path in paths if repository_path(path, root) in changed_paths
            ]
            for category, paths in files.items()
        }
        chunk_map.update(
            build_chunk_map(changed_files, chunk_size, overlap, repo_root=root)
        )

    for _nid, data in graph.nodes(data=True):
        vector_ids = resolve_node_chunks(data, chunk_map)
        chunks_by_id = {
            chunk.vector_id: chunk for chunks in chunk_map.values() for chunk in chunks
        }
        data["chunks"] = [
            {"hash": chunks_by_id[vector_id].hash, "vector_id": vector_id}
            for vector_id in vector_ids
        ]

    return graph, chunk_map
