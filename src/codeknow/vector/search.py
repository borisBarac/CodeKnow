from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from codeknow.graph.chunk_mapper import build_reverse_index
from codeknow.pipeline.io import load_graph
from codeknow.schemas import HybridSearchResponse, HybridSearchResult
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx

logger = logging.getLogger(__name__)

_SKIPPED_RELATIONS = frozenset({"imports", "imports_from", "contains", "method"})


def _bfs_seeds(
    graph: nx.Graph,
    seed_nodes: list[str],
    depth: int,
) -> dict[str, list[str]]:
    """BFS from seeds. Returns {discovered_node_id: path}.

    Path format: alternating node labels and edge arrows, e.g.:
      ["Session.login", "→calls→", "TokenStore.validate"]
    """
    seeds: list[str] = seed_nodes
    if graph.number_of_nodes() > 5000:
        seeds = seed_nodes[:50]

    discovered: dict[str, list[str]] = {}
    visited: set[str] = set(seeds)
    queue: deque[tuple[str, list[str]]] = deque()

    for seed in seeds:
        label = graph.nodes[seed].get("label", seed) if seed in graph.nodes else seed
        queue.append((seed, [label]))

    while queue:
        node_id, path = queue.popleft()
        if len(path) // 2 >= depth:
            continue

        for neighbor in graph.neighbors(node_id):
            edge_data = graph.edges[node_id, neighbor]
            relation = edge_data.get("relation", "")
            if relation in _SKIPPED_RELATIONS:
                continue

            new_path = [
                *path,
                f"→{relation}→",
                graph.nodes[neighbor].get("label", neighbor),
            ]

            if neighbor not in visited:
                visited.add(neighbor)
                if neighbor not in seeds:
                    discovered[neighbor] = new_path
                queue.append((neighbor, new_path))
            elif neighbor in discovered and len(new_path) < len(discovered[neighbor]):
                discovered[neighbor] = new_path

    return discovered


def _fetch_chunks_from_store(
    store: ChromaStore,
    chunk_hashes: list[str],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Fetch chunk content + metadata from ChromaDB via store.get_by_ids().

    Returns {chunk_hash: (document_content, metadata_dict)}.
    Skips hashes not found in ChromaDB (stale index), logs warning.
    """
    if not chunk_hashes:
        return {}

    results = store.get_by_ids(chunk_hashes)
    fetched: dict[str, tuple[str, dict[str, Any]]] = {}
    found_hashes: set[str] = set()

    for sr in results:
        if sr.document is not None and sr.metadata is not None:
            fetched[sr.hash] = (sr.document, sr.metadata)
            found_hashes.add(sr.hash)

    missing = set(chunk_hashes) - found_hashes
    if missing:
        logger.warning("Chunks not found in ChromaDB (stale index): %s", missing)

    return fetched


def hybrid_search(
    query: str,
    *,
    output_dir: Path,
    collection_name: str,
    n_results: int = 10,
    traversal_depth: int = 2,
    graph_filename: str = "graph.json",
    embed_config: EmbeddingConfig | None = None,
    chroma_config: ChromaConfig | None = None,
) -> HybridSearchResponse:
    e_config = embed_config or EmbeddingConfig()
    embeddings = create_embeddings(e_config)

    c_config = chroma_config or ChromaConfig(collection_name=collection_name)
    if c_config.collection_name != collection_name:
        c_config = ChromaConfig(
            host=c_config.host,
            port=c_config.port,
            ssl=c_config.ssl,
            collection_name=collection_name,
            tenant=c_config.tenant,
            database=c_config.database,
        )
    store = ChromaStore(config=c_config, embeddings=embeddings)

    graph: nx.Graph | None = None
    reverse_index: dict[str, list[str]] = {}
    try:
        graph = load_graph(output_dir / graph_filename)
        reverse_index = build_reverse_index(graph)
    except FileNotFoundError:
        logger.warning(
            "Graph not found at %s — falling back to pure vector search",
            output_dir / graph_filename,
        )

    vector_results = store.search(query, n_results=n_results)

    by_hash: dict[str, HybridSearchResult] = {}

    for sr in vector_results:
        meta = sr.metadata or {}
        node_labels_str = meta.get("node_labels", "")
        community_ids_str = meta.get("community_ids", "")

        by_hash[sr.hash] = HybridSearchResult(
            chunk_hash=sr.hash,
            file=meta.get("file", ""),
            start_line=int(meta.get("start_line", 1)),
            end_line=int(meta.get("end_line", 1)),
            content=sr.document or "",
            distance=sr.distance,
            node_labels=node_labels_str.split("|") if node_labels_str else [],
            community_ids=[int(c) for c in community_ids_str.split(",") if c],
            provenance="vector",
        )

    if graph is None or not reverse_index or not vector_results:
        return HybridSearchResponse(
            query=query,
            vector_hits=len(by_hash),
            graph_expanded=0,
            results=list(by_hash.values()),
        )

    vector_hashes = set(by_hash.keys())
    seed_nodes_set: set[str] = set()
    for h in vector_hashes:
        seed_nodes_set.update(reverse_index.get(h, []))
    seed_nodes = list(seed_nodes_set)

    if not seed_nodes:
        return HybridSearchResponse(
            query=query,
            vector_hits=len(by_hash),
            graph_expanded=0,
            results=list(by_hash.values()),
        )

    discovered = _bfs_seeds(graph, seed_nodes, traversal_depth)

    for node_id, path in discovered.items():
        node_data = graph.nodes[node_id]
        node_chunks = node_data.get("chunks", [])
        if not node_chunks:
            continue

        chunk_hashes = [c["hash"] for c in node_chunks if c.get("hash")]
        if not chunk_hashes:
            continue

        fetched = _fetch_chunks_from_store(store, chunk_hashes)

        node_label = node_data.get("label", node_id)
        for chunk_hash, (content, meta) in fetched.items():
            if chunk_hash in vector_hashes:
                continue

            by_hash[chunk_hash] = HybridSearchResult(
                chunk_hash=chunk_hash,
                file=meta.get("file", ""),
                start_line=int(meta.get("start_line", 1)),
                end_line=int(meta.get("end_line", 1)),
                content=content,
                provenance="graph",
                graph_path=path,
                node_labels=[node_label],
            )

    results = list(by_hash.values())

    def _sort_key(r: HybridSearchResult) -> tuple:
        provenance_order = {"vector": 0, "graph": 1}
        return (
            provenance_order.get(r.provenance, 2),
            r.distance if r.distance is not None else float("inf"),
            len(r.graph_path or []),
        )

    results.sort(key=_sort_key)

    vector_hits = sum(1 for r in results if r.provenance == "vector")
    graph_expanded = sum(1 for r in results if r.provenance == "graph")

    return HybridSearchResponse(
        query=query,
        vector_hits=vector_hits,
        graph_expanded=graph_expanded,
        results=results,
    )
