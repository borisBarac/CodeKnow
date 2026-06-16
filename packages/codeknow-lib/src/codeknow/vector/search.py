from __future__ import annotations

import heapq
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, ClassVar

from codeknow.chunking.index import build_reverse_index
from codeknow.pipeline.io import load_graph
from codeknow.schemas import HybridSearchResponse, HybridSearchResult
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

if TYPE_CHECKING:
    from pathlib import Path

    import networkx as nx
    from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)


def _simple_tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


def _is_test_or_docs_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = set(normalized.split("/"))
    return "test" in parts or "tests" in parts or "docs" in parts


class GraphSearcher:
    """Collapsed hybrid search interface.

    Owns the graph, reverse index, vector store, and embeddings as internal
    state.  Callers use ``search()`` for single-graph queries and
    ``multi_search()`` for multi-graph queries.

    Enhanced with:
    - BM25 sparse retrieval + RRF for hybrid dense/sparse search
    - Query-aware edge weights for graph traversal
    - Cross-source re-ranking with unified relevance scoring
    - Edge confidence_score weighting during BFS
    - Community-aware boosting during graph expansion
    - Distance estimates for graph-expanded results
    - Deduplication of overlapping chunks
    """

    _MAX_GRAPH_RESULTS: ClassVar[int] = 50

    RELATION_WEIGHTS: ClassVar[dict[str, float]] = {
        "imports": 0.3,
        "imports_from": 0.3,
        "contains": 0.15,
        "method": 0.3,
        "calls": 0.7,
        "uses": 0.7,
        "inherits": 0.8,
        "rationale_for": 0.9,
        "semantically_similar_to": 1.0,
    }

    DEFAULT_RELATION_WEIGHT: ClassVar[float] = 0.0

    RRF_K: ClassVar[int] = 60
    RRF_ALPHA: ClassVar[float] = 0.6
    RRF_BETA: ClassVar[float] = 0.4

    _INTENT_RULES: ClassVar[list[tuple[list[str], dict[str, float]]]] = [
        (["flow", "process", "work", "how"], {"calls": 0.9, "uses": 0.9}),
        (["schema", "structure", "fields", "columns", "table"], {"contains": 0.5}),
        (["inherit", "extend", "implement", "subclass"], {"inherits": 0.95}),
        (["similar", "like", "compare", "related"], {"semantically_similar_to": 1.1}),
    ]

    def __init__(
        self,
        graph_dir: Path,
        *,
        collection_name: str = "codeknow_chunks",
        embed_config: EmbeddingConfig | None = None,
        chroma_config: ChromaConfig | None = None,
        graph_filename: str = "graph.json",
        traversal_depth: int = 2,
        embeddings: Embeddings | None = None,
        store: ChromaStore | None = None,
    ) -> None:
        self._traversal_depth = traversal_depth
        self._collection_name = collection_name
        self._embed_config = embed_config
        self._chroma_config = chroma_config
        self._embeddings = embeddings
        self._store = store

        self._graph: nx.Graph | None = None
        self._reverse_index: dict[str, list[str]] = {}
        try:
            self._graph = load_graph(graph_dir / graph_filename)
            self._reverse_index = build_reverse_index(self._graph)
        except FileNotFoundError:
            logger.warning(
                "Graph not found at %s — falling back to pure vector search",
                graph_dir / graph_filename,
            )

    def _get_store(self) -> ChromaStore:
        if self._store is None:
            embeddings = self._embeddings
            if embeddings is None:
                e_config = self._embed_config or EmbeddingConfig()
                embeddings = create_embeddings(e_config)
                self._embeddings = embeddings

            c_config = self._chroma_config or ChromaConfig(
                collection_name=self._collection_name,
            )
            if c_config.collection_name != self._collection_name:
                c_config = ChromaConfig(
                    host=c_config.host,
                    port=c_config.port,
                    ssl=c_config.ssl,
                    collection_name=self._collection_name,
                    tenant=c_config.tenant,
                    database=c_config.database,
                )
            self._store = ChromaStore(config=c_config, embeddings=embeddings)
        return self._store

    def _classify_query_intent(self, query: str) -> dict[str, float]:
        boosts: dict[str, float] = {}
        query_lower = query.lower()
        for keywords, weight_overrides in self._INTENT_RULES:
            if any(kw in query_lower for kw in keywords):
                boosts.update(weight_overrides)
        return boosts

    def _get_effective_weights(self, query: str) -> dict[str, float]:
        base = dict(self.RELATION_WEIGHTS)
        boosts = self._classify_query_intent(query)
        for relation, weight in boosts.items():
            if relation in base:
                base[relation] = weight
        return base

    def _bfs_seeds(
        self,
        seed_nodes: list[str],
        depth: int,
        max_results: int | None = None,
        effective_weights: dict[str, float] | None = None,
        dominant_communities: set[int] | None = None,
    ) -> dict[str, tuple[list[str], float, str]]:
        if max_results is None:
            max_results = self._MAX_GRAPH_RESULTS

        graph = self._graph
        if graph is None:
            msg = "Graph not loaded"
            raise ValueError(msg)

        seeds: list[str] = seed_nodes
        if graph.number_of_nodes() > 5000:
            seeds = seed_nodes[:50]

        weights = effective_weights or self.RELATION_WEIGHTS

        discovered: dict[str, tuple[list[str], float, str]] = {}
        visited: set[str] = set()
        counter = 0
        heap: list[tuple[float, int, str, list[str], str]] = []

        for seed in seeds:
            label = (
                graph.nodes[seed].get("label", seed) if seed in graph.nodes else seed
            )
            heapq.heappush(heap, (0.0, counter, seed, [label], seed))
            counter += 1

        while heap:
            neg_cum, _, node_id, path, origin_seed = heapq.heappop(heap)

            if node_id in visited:
                continue
            visited.add(node_id)

            if node_id not in seeds:
                discovered[node_id] = (path, -neg_cum, origin_seed)
                if len(discovered) >= max_results:
                    return discovered

            if len(path) // 2 >= depth:
                continue

            for neighbor in graph.neighbors(node_id):
                edge_data = graph.edges[node_id, neighbor]
                relation = edge_data.get("relation", "")
                base_weight = weights.get(relation, self.DEFAULT_RELATION_WEIGHT)
                if base_weight <= 0.0:
                    continue

                confidence_score = edge_data.get("confidence_score")
                if confidence_score is not None:
                    base_weight = base_weight * confidence_score

                if dominant_communities is not None:
                    src_community = graph.nodes[node_id].get("community")
                    tgt_community = graph.nodes[neighbor].get("community")
                    if src_community is not None and tgt_community is not None:
                        same = src_community == tgt_community
                        if same and src_community in dominant_communities:
                            base_weight *= 1.2
                        elif not same:
                            base_weight *= 0.8

                new_cum = -neg_cum + base_weight
                new_path = [
                    *path,
                    f"\u2192{relation}\u2192",
                    graph.nodes[neighbor].get("label", neighbor),
                ]
                heapq.heappush(
                    heap, (-new_cum, counter, neighbor, new_path, origin_seed)
                )
                counter += 1

        return discovered

    @staticmethod
    def _fetch_chunks_from_store(
        store: ChromaStore,
        chunk_hashes: list[str],
    ) -> dict[str, tuple[str, dict[str, Any]]]:
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

    @staticmethod
    def _compute_relevance_score(r: HybridSearchResult) -> float:
        if r.provenance in {"vector", "sparse"} and r.distance is not None:
            normalized = min(1.0, max(0.0, r.distance))
            relevance = 1.0 - normalized
            return relevance * 1.0
        if r.provenance == "graph":
            if r.cumulative_weight is not None and r.cumulative_weight > 0:
                relevance = min(0.95, r.cumulative_weight / 3.0)
            else:
                relevance = 0.3
            return relevance * 0.95
        return 0.0

    @staticmethod
    def _sort_key(r: HybridSearchResult) -> tuple:
        relevance = GraphSearcher._compute_relevance_score(r)
        return (-relevance, r.distance if r.distance is not None else float("inf"))

    @staticmethod
    def _discover_graph_dirs(
        graph_base_dir: Path,
        slugs: list[str] | None = None,
    ) -> list[tuple[str, Path]]:
        if slugs is not None:
            return [
                (s, graph_base_dir / s)
                for s in slugs
                if (graph_base_dir / s / "metadata.json").exists()
            ]

        dirs: list[tuple[str, Path]] = []
        if not graph_base_dir.is_dir():
            return dirs
        for child in sorted(graph_base_dir.iterdir()):
            if child.is_dir() and (child / "metadata.json").exists():
                dirs.append((child.name, child))
        return dirs

    @staticmethod
    def _parse_labels(meta: dict[str, Any]) -> tuple[list[str], list[int]]:
        nl = meta.get("node_labels", "")
        ci = meta.get("community_ids", "")
        labels = nl.split("|") if nl else []
        ids = [int(c) for c in ci.split(",") if c]
        return labels, ids

    def _make_vector_result(self, sr: Any) -> HybridSearchResult:
        meta = sr.metadata or {}
        labels, ids = self._parse_labels(meta)
        return HybridSearchResult(
            chunk_hash=sr.hash,
            file=meta.get("file", ""),
            start_line=int(meta.get("start_line", 1)),
            end_line=int(meta.get("end_line", 1)),
            content=sr.document or "",
            distance=sr.distance,
            node_labels=labels,
            community_ids=ids,
            provenance="vector",
        )

    def _make_bm25_result(
        self,
        hash_val: str,
        doc: str,
        meta: dict[str, Any],
        *,
        score: float | None = None,
        max_score: float | None = None,
    ) -> HybridSearchResult:
        labels, ids = self._parse_labels(meta)
        distance = 0.5
        if score is not None and max_score and max_score > 0:
            distance = max(0.05, min(0.95, 1.0 - (score / max_score)))
        return HybridSearchResult(
            chunk_hash=hash_val,
            file=meta.get("file", ""),
            start_line=int(meta.get("start_line", 1)),
            end_line=int(meta.get("end_line", 1)),
            content=doc,
            distance=distance,
            node_labels=labels,
            community_ids=ids,
            provenance="sparse",
        )

    def _bm25_search(
        self,
        query: str,
        store: ChromaStore,
        n_results: int,
    ) -> list[tuple[str, float, str, dict[str, Any]]]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning("rank_bm25 not installed, skipping BM25 stage")
            return []

        collection = store._get_or_create_collection()  # noqa: SLF001
        all_results = collection.get(include=["documents", "metadatas"])
        ids: list[str] = all_results.get("ids", []) or []
        documents: list[str] = all_results.get("documents", []) or []
        metadatas_raw = all_results.get("metadatas", []) or []
        metadatas: list[dict[str, Any]] = [
            dict(m) if m is not None else {} for m in metadatas_raw
        ]

        if not ids or not documents:
            return []

        tokenized_corpus = [_simple_tokenize(doc) for doc in documents]
        if not tokenized_corpus:
            return []
        bm25 = BM25Okapi(tokenized_corpus)
        tokenized_query = _simple_tokenize(query)
        scores = bm25.get_scores(tokenized_query)

        scored: list[tuple[int, float]] = [
            (i, s) for i, s in enumerate(scores) if s > 0
        ]
        scored.sort(key=lambda x: -x[1])
        top_indices = scored[:n_results]

        return [
            (
                ids[i],
                float(scores[i]),
                documents[i] if i < len(documents) else "",
                metadatas[i] if i < len(metadatas) else {},
            )
            for i, _ in top_indices
        ]

    def _rrf_merge(
        self,
        dense_results: list[tuple[str, int]],
        sparse_results: list[tuple[str, int]],
    ) -> dict[str, float]:
        k = self.RRF_K
        alpha = self.RRF_ALPHA
        beta = self.RRF_BETA

        rrf_scores: dict[str, float] = {}

        for hash_val, rank in dense_results:
            rrf_scores[hash_val] = rrf_scores.get(hash_val, 0.0) + alpha / (rank + k)

        for hash_val, rank in sparse_results:
            rrf_scores[hash_val] = rrf_scores.get(hash_val, 0.0) + beta / (rank + k)

        return rrf_scores

    def _deduplicate_overlapping(
        self, results: list[HybridSearchResult]
    ) -> list[HybridSearchResult]:
        by_file: dict[str, list[int]] = {}
        for i, r in enumerate(results):
            by_file.setdefault(r.file, []).append(i)

        remove_indices: set[int] = set()

        for indices in by_file.values():
            if len(indices) <= 1:
                continue
            for a_idx in range(len(indices)):
                if indices[a_idx] in remove_indices:
                    continue
                for b_idx in range(a_idx + 1, len(indices)):
                    if indices[b_idx] in remove_indices:
                        continue
                    ra = results[indices[a_idx]]
                    rb = results[indices[b_idx]]
                    if ra.start_line <= rb.end_line and rb.start_line <= ra.end_line:
                        score_a = self._compute_relevance_score(ra)
                        score_b = self._compute_relevance_score(rb)
                        if score_a >= score_b:
                            remove_indices.add(indices[b_idx])
                        else:
                            remove_indices.add(indices[a_idx])

        return [r for i, r in enumerate(results) if i not in remove_indices]

    def _get_dominant_communities(
        self, results: dict[str, HybridSearchResult]
    ) -> set[int]:
        community_counts: Counter[int] = Counter()
        for r in list(results.values())[:5]:
            for cid in r.community_ids:
                community_counts[cid] += 1
        if not community_counts:
            return set()
        threshold = max(1, len(list(results.values())[:5]) // 2)
        return {cid for cid, count in community_counts.items() if count >= threshold}

    def search(self, query: str, top_k: int = 10) -> HybridSearchResponse:
        store = self._get_store()
        vector_results = store.search(query, n_results=top_k)

        bm25_results = self._bm25_search(query, store, n_results=top_k * 5)

        by_hash: dict[str, HybridSearchResult] = {}

        if bm25_results:
            dense_ranks = [(sr.hash, rank) for rank, sr in enumerate(vector_results)]
            sparse_ranks = [(h, rank) for rank, (h, _, _, _) in enumerate(bm25_results)]
            rrf_scores = self._rrf_merge(dense_ranks, sparse_ranks)

            all_hashes_by_rrf = sorted(rrf_scores.keys(), key=lambda h: -rrf_scores[h])

            vector_hash_to_sr = {sr.hash: sr for sr in vector_results}
            bm25_map = {h: (sc, doc, meta) for h, sc, doc, meta in bm25_results}

            candidate_hashes = list(dict.fromkeys(all_hashes_by_rrf[:top_k]))
            for hash_val, *_ in bm25_results[:top_k]:
                if hash_val not in candidate_hashes:
                    candidate_hashes.append(hash_val)
            source_sparse = [
                hash_val
                for hash_val, _, _, meta in bm25_results
                if not _is_test_or_docs_path(str(meta.get("file", "")))
            ][:top_k]
            for hash_val in source_sparse:
                if hash_val not in candidate_hashes:
                    candidate_hashes.append(hash_val)

            for hash_val in candidate_hashes:
                if hash_val in vector_hash_to_sr:
                    sr = vector_hash_to_sr[hash_val]
                    by_hash[sr.hash] = self._make_vector_result(sr)
                elif hash_val in bm25_map:
                    score, doc, meta = bm25_map[hash_val]
                    max_score = max((s for s, _, _ in bm25_map.values()), default=score)
                    by_hash[hash_val] = self._make_bm25_result(
                        hash_val,
                        doc,
                        meta,
                        score=score,
                        max_score=max_score,
                    )
        else:
            for sr in vector_results:
                by_hash[sr.hash] = self._make_vector_result(sr)

        if self._graph is None or not self._reverse_index or not by_hash:
            vector_hits = sum(1 for r in by_hash.values() if r.provenance == "vector")
            return HybridSearchResponse(
                query=query,
                vector_hits=vector_hits,
                graph_expanded=0,
                results=list(by_hash.values()),
            )

        vector_hashes = set(by_hash.keys())
        seed_nodes_set: set[str] = set()
        for h in vector_hashes:
            seed_nodes_set.update(self._reverse_index.get(h, []))
        seed_nodes = list(seed_nodes_set)

        if not seed_nodes:
            vector_hits = sum(1 for r in by_hash.values() if r.provenance == "vector")
            return HybridSearchResponse(
                query=query,
                vector_hits=vector_hits,
                graph_expanded=0,
                results=list(by_hash.values()),
            )

        effective_weights = self._get_effective_weights(query)
        dominant_communities = self._get_dominant_communities(by_hash)

        discovered = self._bfs_seeds(
            seed_nodes,
            self._traversal_depth,
            effective_weights=effective_weights,
            dominant_communities=dominant_communities,
        )

        seed_distances: dict[str, float] = {}
        for h, r in by_hash.items():
            if r.distance is not None:
                seed_distances[h] = r.distance

        node_seed_map: dict[str, str] = {}
        for h in vector_hashes:
            for node_id in self._reverse_index.get(h, []):
                if node_id not in node_seed_map:
                    node_seed_map[node_id] = h

        max_cum_weight = max((cw for _, (_, cw, _) in discovered.items()), default=1.0)
        if max_cum_weight <= 0:
            max_cum_weight = 1.0

        node_chunk_map: dict[str, tuple[list[str], list[str], str, float, str]] = {}
        all_new_hashes: set[str] = set()

        for node_id, (path, cum_weight, origin_seed) in discovered.items():
            node_data = self._graph.nodes[node_id]
            node_chunks = node_data.get("chunks", [])
            if not node_chunks:
                continue

            chunk_hashes = [c["hash"] for c in node_chunks if c.get("hash")]
            new_hashes = [h for h in chunk_hashes if h not in vector_hashes]
            if not new_hashes:
                continue

            node_label = node_data.get("label", node_id)
            node_chunk_map[node_id] = (
                new_hashes,
                path,
                node_label,
                cum_weight,
                origin_seed,
            )
            all_new_hashes.update(new_hashes)

        if all_new_hashes:
            fetched = self._fetch_chunks_from_store(store, list(all_new_hashes))

            for (
                new_hashes,
                path,
                node_label,
                cum_weight,
                origin_seed,
            ) in node_chunk_map.values():
                seed_hash = node_seed_map.get(origin_seed)
                seed_dist = seed_distances.get(seed_hash, 0.8) if seed_hash else 0.8

                for chunk_hash in new_hashes:
                    if chunk_hash not in fetched:
                        continue
                    content, meta = fetched[chunk_hash]

                    weight_ratio = min(0.8, cum_weight / max_cum_weight)
                    estimated_distance = max(
                        0.1, min(seed_dist * 0.8, seed_dist * (1.0 - weight_ratio))
                    )

                    by_hash[chunk_hash] = HybridSearchResult(
                        chunk_hash=chunk_hash,
                        file=meta.get("file", ""),
                        start_line=int(meta.get("start_line", 1)),
                        end_line=int(meta.get("end_line", 1)),
                        content=content,
                        distance=estimated_distance,
                        provenance="graph",
                        graph_path=path,
                        node_labels=[node_label],
                        cumulative_weight=cum_weight,
                    )

        results = list(by_hash.values())
        results = self._deduplicate_overlapping(results)
        results.sort(key=self._sort_key)

        vector_hits = sum(1 for r in results if r.provenance == "vector")
        graph_expanded = sum(1 for r in results if r.provenance == "graph")

        return HybridSearchResponse(
            query=query,
            vector_hits=vector_hits,
            graph_expanded=graph_expanded,
            results=results,
        )

    @classmethod
    def multi_search(
        cls,
        base_dir: Path,
        query: str,
        *,
        top_k: int = 20,
        n_results_per_graph: int = 5,
        traversal_depth: int = 2,
        slugs: list[str] | None = None,
        embed_config: EmbeddingConfig | None = None,
        chroma_config: ChromaConfig | None = None,
    ) -> HybridSearchResponse:
        if not query or not query.strip():
            msg = "query must be a non-empty string"
            raise ValueError(msg)

        top_k = max(1, top_k)
        n_results_per_graph = max(1, n_results_per_graph)

        if not base_dir.is_dir():
            logger.warning("base_dir does not exist: %s", base_dir)

        graph_dirs = cls._discover_graph_dirs(base_dir, slugs)

        if not graph_dirs:
            return HybridSearchResponse(
                query=query,
                vector_hits=0,
                graph_expanded=0,
                results=[],
            )

        embeddings = create_embeddings(embed_config or EmbeddingConfig())

        def _search_single(
            item: tuple[str, Path],
        ) -> tuple[str, HybridSearchResponse] | None:
            slug, graph_dir = item
            try:
                searcher = cls(
                    graph_dir,
                    collection_name=f"codeknow_{slug}",
                    traversal_depth=traversal_depth,
                    embeddings=embeddings,
                    chroma_config=chroma_config,
                )
                resp = searcher.search(query, top_k=n_results_per_graph)
            except Exception:
                logger.warning("Search failed for slug '%s'", slug, exc_info=True)
                return None
            else:
                return (slug, resp)

        all_results: list[HybridSearchResult] = []
        total_vector = 0
        total_graph = 0

        with ThreadPoolExecutor() as executor:
            results = list(executor.map(_search_single, graph_dirs))

        for item in results:
            if item is None:
                continue
            slug, resp = item
            tagged = [r.model_copy(update={"slug": slug}) for r in resp.results]
            all_results.extend(tagged)
            total_vector += resp.vector_hits
            total_graph += resp.graph_expanded

        all_results.sort(key=cls._sort_key)
        all_results = all_results[:top_k]

        return HybridSearchResponse(
            query=query,
            vector_hits=total_vector,
            graph_expanded=total_graph,
            results=all_results,
        )
