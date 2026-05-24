"""Pipeline stage: link graph nodes to code chunk hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from codeknow.chunking.chunker import build_chunk_map

if TYPE_CHECKING:
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
            overlapping.append(chunk.hash)
    return overlapping


def map_chunks(
    graph: nx.Graph,
    files: dict[str, list[str]],
    *,
    chunk_size: int = 100,
    overlap: int = 20,
) -> tuple[nx.Graph, ChunkMap]:
    """Pipeline stage: link graph nodes to code chunk hashes.

    1. Chunk all source files → build ChunkMap
    2. For each node, find overlapping chunks
    3. Write ``chunks`` list onto each node

    Returns the enriched graph and the chunk_map.
    """
    chunk_map = build_chunk_map(files, chunk_size, overlap)

    for _nid, data in graph.nodes(data=True):
        hashes = resolve_node_chunks(data, chunk_map)
        data["chunks"] = [{"hash": h} for h in hashes]

    return graph, chunk_map
