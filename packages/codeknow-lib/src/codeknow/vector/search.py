from __future__ import annotations

import heapq
import logging
from typing import TYPE_CHECKING, Any

from codeknow.chunking.index import build_reverse_index
from codeknow.pipeline.io import load_graph
from codeknow.schemas import HybridSearchResponse, HybridSearchResult
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx
    from langchain_core.embeddings import Embeddings

from codeknow.vector.weights import DEFAULT_RELATION_WEIGHT, RELATION_WEIGHTS

logger = logging.getLogger(__name__)

_MAX_GRAPH_RESULTS = 50


def _bfs_seeds(
    graph: nx.Graph,
    seed_nodes: list[str],
    depth: int,
    max_results: int = _MAX_GRAPH_RESULTS,
) -> dict[str, tuple[list[str], float]]:
    """Weighted BFS from seeds using heapq priority queue.

    Explores higher-weight edges first (Dijkstra-like). Edges with weight
    <= 0.0 are not traversed. Unknown relations default to weight 0.0.

    Returns {discovered_node_id: (path, cumulative_weight)} where path format
    is alternating node labels and edge arrows, e.g.:
      ["Session.login", "→calls→", "TokenStore.validate"]
    and cumulative_weight is the sum of edge weights along the highest-weight path.
    """
    seeds: list[str] = seed_nodes
    if graph.number_of_nodes() > 5000:
        seeds = seed_nodes[:50]

    discovered: dict[str, tuple[list[str], float]] = {}
    visited: set[str] = set()
    counter = 0
    heap: list[tuple[float, int, str, list[str]]] = []

    for seed in seeds:
        label = graph.nodes[seed].get("label", seed) if seed in graph.nodes else seed
        heapq.heappush(heap, (0.0, counter, seed, [label]))
        counter += 1

    while heap:
        neg_cum, _, node_id, path = heapq.heappop(heap)

        if node_id in visited:
            continue
        visited.add(node_id)

        if node_id not in seeds:
            discovered[node_id] = (path, -neg_cum)
            if len(discovered) >= max_results:
                return discovered

        if len(path) // 2 >= depth:
            continue

        for neighbor in graph.neighbors(node_id):
            edge_data = graph.edges[node_id, neighbor]
            relation = edge_data.get("relation", "")
            weight = RELATION_WEIGHTS.get(relation, DEFAULT_RELATION_WEIGHT)
            if weight <= 0.0:
                continue

            new_cum = -neg_cum + weight
            new_path = [
                *path,
                f"→{relation}→",
                graph.nodes[neighbor].get("label", neighbor),
            ]
            heapq.heappush(heap, (-new_cum, counter, neighbor, new_path))
            counter += 1

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
    graph_dir: Path,
    collection_name: str,
    n_results: int = 10,
    traversal_depth: int = 2,
    graph_filename: str = "graph.json",
    embed_config: EmbeddingConfig | None = None,
    chroma_config: ChromaConfig | None = None,
    embeddings: Embeddings | None = None,
    store: ChromaStore | None = None,
) -> HybridSearchResponse:
    if embeddings is None:
        e_config = embed_config or EmbeddingConfig()
        embeddings = create_embeddings(e_config)

    if store is None:
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
        graph = load_graph(graph_dir / graph_filename)
        reverse_index = build_reverse_index(graph)
    except FileNotFoundError:
        logger.warning(
            "Graph not found at %s — falling back to pure vector search",
            graph_dir / graph_filename,
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

    node_chunk_map: dict[str, tuple[list[str], list[str], str, float]] = {}
    all_new_hashes: set[str] = set()

    for node_id, (path, cum_weight) in discovered.items():
        node_data = graph.nodes[node_id]
        node_chunks = node_data.get("chunks", [])
        if not node_chunks:
            continue

        chunk_hashes = [c["hash"] for c in node_chunks if c.get("hash")]
        new_hashes = [h for h in chunk_hashes if h not in vector_hashes]
        if not new_hashes:
            continue

        node_label = node_data.get("label", node_id)
        node_chunk_map[node_id] = (new_hashes, path, node_label, cum_weight)
        all_new_hashes.update(new_hashes)

    if all_new_hashes:
        fetched = _fetch_chunks_from_store(store, list(all_new_hashes))

        for new_hashes, path, node_label, cum_weight in node_chunk_map.values():
            for chunk_hash in new_hashes:
                if chunk_hash not in fetched:
                    continue
                content, meta = fetched[chunk_hash]
                by_hash[chunk_hash] = HybridSearchResult(
                    chunk_hash=chunk_hash,
                    file=meta.get("file", ""),
                    start_line=int(meta.get("start_line", 1)),
                    end_line=int(meta.get("end_line", 1)),
                    content=content,
                    provenance="graph",
                    graph_path=path,
                    node_labels=[node_label],
                    cumulative_weight=cum_weight,
                )

    results = list(by_hash.values())

    results.sort(key=sort_key)

    vector_hits = sum(1 for r in results if r.provenance == "vector")
    graph_expanded = sum(1 for r in results if r.provenance == "graph")

    return HybridSearchResponse(
        query=query,
        vector_hits=vector_hits,
        graph_expanded=graph_expanded,
        results=results,
    )


def sort_key(r: HybridSearchResult) -> tuple:
    provenance_order = {"vector": 0, "graph": 1}
    return (
        provenance_order.get(r.provenance, 2),
        r.distance if r.distance is not None else float("inf"),
        -(r.cumulative_weight or 0.0),
        len(r.graph_path or []),
    )
