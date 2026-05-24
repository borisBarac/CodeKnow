"""Reverse index: chunk hash → graph node IDs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx


def build_reverse_index(graph: nx.Graph) -> dict[str, list[str]]:
    """Build hash → [node_ids] reverse index from the graph's node chunks.

    Used for vector search → graph node lookup.
    """
    index: dict[str, list[str]] = {}
    for nid, data in graph.nodes(data=True):
        for chunk in data.get("chunks", []):
            h = chunk.get("hash")
            if h:
                index.setdefault(h, []).append(nid)
    return index
