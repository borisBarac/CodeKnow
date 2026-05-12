# Hybrid Search Implementation Plan

Vector + Graph Traversal (Option 2) — Library-only, no CLI entry point.

---

##  [x] Phase 1: Pipeline Save/Load + E2E Tests

**Full details:** [`plan/phase1-pipeline-save-load.md`](phase1-pipeline-save-load.md)

Summary of changes:
- Add `load_graph()` and `communities_from_graph()` to `pipeline/io.py`
- Move `save_pipeline_result()` into `run_pipeline()` (guarantees disk output)
- Remove redundant save call from `cli.py`
- Add roundtrip e2e tests to `e2e/graph_gen/test_graph_gen.py`
- Export new functions from `pipeline/__init__.py`

---

## [x] Phase 2: Remove `serve` from Pipeline Stages

| File | Change |
|---|---|
| `src/codeknow/pipeline/types.py` | Remove `"serve"` from `STAGES` list and `STAGE_IO` dict |

- Remove `"serve"` from `STAGES` list (line 76)
- Remove `"serve"` entry from `STAGE_IO` dict (lines 117-121)

---

## Phase 3: Add Schemas

| File | Change |
|---|---|
| `src/codeknow/schemas.py` | Add `HybridSearchResult`, `HybridSearchResponse` |

```python
class HybridSearchResult(BaseModel):
    chunk_hash: str
    file: str
    start_line: int
    end_line: int
    content: str
    distance: float | None = None
    node_labels: list[str] = Field(default_factory=list)
    community_ids: list[int] = Field(default_factory=list)
    provenance: str = "vector"  # "vector" | "graph" | "both"
    graph_path: list[str] | None = None  # e.g. ["Session.login", "→calls→", "TokenStore.validate"]


class HybridSearchResponse(BaseModel):
    query: str
    vector_hits: int
    graph_expanded: int
    results: list[HybridSearchResult]
```

---

## Phase 4: Create `src/codeknow/vector/search.py` — Core Hybrid Search

| File | Action |
|---|---|
| `src/codeknow/vector/chroma.py` | Modify — add `get_by_ids()` public method |
| `src/codeknow/vector/search.py` | **New file** — `hybrid_search()` + internal helpers (~150 lines) |

### Public API

```python
from codeknow.vector import hybrid_search
from codeknow.vector.search import HybridSearchResult, HybridSearchResponse

response: HybridSearchResponse = hybrid_search(
    "authentication middleware",
    output_dir=Path("./codeknow-out"),
    collection_name="codeknow_owner-repo",
    n_results=10,
    traversal_depth=2,
)
for hit in response.results:
    print(hit.chunk_hash, hit.provenance, hit.distance, hit.graph_path)
```

### Function signature

```python
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
```

### ChromaStore addition

```python
# In src/codeknow/vector/chroma.py — add to ChromaStore class:

def get_by_ids(self, chunk_hashes: list[str]) -> list[SearchResult]:
    """Fetch chunk content + metadata by hash. Returns SearchResult for each found chunk."""
    collection = self._get_or_create_collection()
    results = collection.get(ids=chunk_hashes, include=["documents", "metadatas"])
    search_results: list[SearchResult] = []
    ids = results.get("ids", [])
    documents = results.get("documents", [[]])[0] if results.get("documents") else []
    metadatas = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
    for i, chunk_hash in enumerate(ids):
        search_results.append(
            SearchResult(
                hash=chunk_hash,
                document=documents[i] if i < len(documents) else None,
                metadata=metadatas[i] if i < len(metadatas) else None,
            )
        )
    return search_results
```

### Internal helpers

```python
_SKIPPED_RELATIONS = frozenset({"imports", "imports_from", "contains", "method"})

def _bfs_seeds(
    graph: nx.Graph,
    seed_nodes: list[str],
    depth: int,
) -> dict[str, list[str]]:
    """BFS from seeds. Returns {discovered_node_id: [path_labels]}"""

def _fetch_chunks_from_store(
    store: ChromaStore,
    chunk_hashes: list[str],
) -> dict[str, tuple[str, dict]]:
    """Fetch chunk content + metadata from ChromaDB via store.get_by_ids()."""
```

### Logic (5 steps)

1. **Load graph** — Call `load_graph(output_dir / graph_filename)` from `pipeline/io.py`. Build reverse index via `build_reverse_index(graph)` → `{chunk_hash: [node_ids]}`. If graph not found, fall back to pure vector search (skip steps 3-4).

2. **Vector search** — Create `ChromaStore` + `EmbeddingConfig`. Call `store.search(query, n_results=n_results)`. Build initial `HybridSearchResult` list. Parse metadata (`node_labels` split on `|`, `community_ids` split on `,`) as display info.

3. **Seed nodes via reverse index** — For each vector hit, look up its `hash` in the reverse index to get graph node IDs. These are the BFS seeds.

4. **Graph traversal (BFS depth 2) + neighbor → chunks** — BFS from seed nodes, configurable `depth` (default 2). Skip edges with relation in `_SKIPPED_RELATIONS`. Track edge path from seed → discovered node. For each newly discovered node (not already in vector hits), read its chunks directly via `graph.nodes[node_id].get("chunks", [])`. Fetch content from ChromaDB via `store.get_by_ids([hash])`. Build `HybridSearchResult` with `provenance="graph"` and `graph_path`.

5. **Merge + dedup** — Combine vector and graph results. Dedup by `chunk_hash` — if hit by both, set `provenance="both"`. Sort: `both` first → `vector` (by distance ascending) → `graph` (by path length ascending). Return `HybridSearchResponse`.

### Edge cases

- **Graph not found**: `load_graph` raises `FileNotFoundError` — catch and fall back to pure vector search (steps 1-2 only)
- **Chunk hash in graph but not in ChromaDB**: Skip (stale index), log warning
- **Empty graph (no edges)**: Graph expansion returns nothing, return vector-only results
- **Very large graphs**: BFS depth=2 is bounded; for graphs >5000 nodes, limit traversal to 50 seed nodes max

---

## Phase 5: Wire Up Exports

| File | Change |
|---|---|
| `src/codeknow/vector/__init__.py` | Add `hybrid_search`, `HybridSearchResult`, `HybridSearchResponse` to imports and `__all__` |

---

## File Summary

### Phase 1 (see [`phase1-pipeline-save-load.md`](phase1-pipeline-save-load.md))

| # | File | Action |
|---|---|---|
| 1 | `src/codeknow/pipeline/io.py` | Modify — add `load_graph()`, `communities_from_graph()` |
| 2 | `src/codeknow/pipeline/runner.py` | Modify — add save call |
| 3 | `src/codeknow/cli.py` | Modify — remove save from pipeline CLI |
| 4 | `src/codeknow/pipeline/__init__.py` | Modify — add `load_graph`, `communities_from_graph` exports |
| 5 | `e2e/graph_gen/test_graph_gen.py` | Modify — add roundtrip tests |

### Phases 2-5

| # | File | Action |
|---|---|---|
| 6 | `src/codeknow/pipeline/types.py` | Modify — remove `serve` stage |
| 7 | `src/codeknow/schemas.py` | Modify — add `HybridSearchResult`, `HybridSearchResponse` |
| 8 | `src/codeknow/vector/chroma.py` | Modify — add `get_by_ids()` |
| 9 | `src/codeknow/vector/search.py` | **Create** — hybrid search (~150 lines) |
| 10 | `src/codeknow/vector/__init__.py` | Modify — add exports |

No `pyproject.toml` changes needed.

---

## Verification

### Phase 1 verification

```bash
uv run pytest e2e/graph_gen/test_graph_gen.py -v
```

### Phases 2-5 verification — hybrid search

```python
from pathlib import Path
from codeknow.vector import hybrid_search

response = hybrid_search(
    "authentication middleware",
    output_dir=Path("./codeknow-out"),
    collection_name="codeknow_owner-repo",
    n_results=10,
    traversal_depth=2,
)
print(f"Vector hits: {response.vector_hits}, Graph expanded: {response.graph_expanded}")
for hit in response.results:
    print(f"  [{hit.provenance}] {hit.file}:{hit.start_line}-{hit.end_line} ({hit.distance})")
```

### Lint/typecheck

```bash
dev-check
```
