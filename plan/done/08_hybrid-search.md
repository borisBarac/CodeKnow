# Hybrid Search Implementation Plan

[implemented]

Vector + Graph Traversal (Option 2) — Library-only, no CLI entry point.

---

##  [x] Phase 1: Pipeline Save/Load + E2E Tests

**Full details:** [`plan/08.1_pipeline-save-load.md`](08.1_pipeline-save-load.md)

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
| `src/codeknow/schemas.py` | Add `HybridSearchResult`, `HybridSearchResponse` after `CommunityMap` (line 121) |

```python
class HybridSearchResult(BaseModel):
    """A single result from hybrid (vector + graph) search."""

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
    """Response from a hybrid search query."""

    query: str
    vector_hits: int
    graph_expanded: int
    results: list[HybridSearchResult]
```

No new imports needed — `BaseModel` and `Field` already imported at line 16.

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
    output_dir=Path.home() / ".codeknow" / "graph",
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
# In src/codeknow/vector/chroma.py — add to ChromaStore class (after search(), ~line 252):

def get_by_ids(self, chunk_hashes: list[str]) -> list[SearchResult]:
    """Fetch chunk content + metadata by hash. Returns SearchResult for each found chunk."""
    collection = self._get_or_create_collection()
    results = collection.get(ids=chunk_hashes, include=["documents", "metadatas"])
    search_results: list[SearchResult] = []
    ids = results.get("ids", [])
    documents = results.get("documents", [])
    metadatas = results.get("metadatas", [])
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

> **NOTE:** ChromaDB `.get()` returns **flat lists** (`{"ids": [...], "documents": [...]}`),
> unlike `.query()` which returns nested lists (`{"ids": [[...]], "documents": [[...]]}`).
> Do NOT index `[0]` on the results.

### Internal helpers

```python
_SKIPPED_RELATIONS = frozenset({"imports", "imports_from", "contains", "method"})

def _bfs_seeds(
    graph: nx.Graph,
    seed_nodes: list[str],
    depth: int,
) -> dict[str, list[str]]:
    """BFS from seeds. Returns {discovered_node_id: path}

    Path format: alternating node labels and edge arrows, e.g.:
      ["Session.login", "→calls→", "TokenStore.validate", "→uses→", "Crypto.hash"]

    Uses graph.nodes[node_id].get("label", node_id) — label is guaranteed on all nodes.
    Skips edges where relation attr is in _SKIPPED_RELATIONS.
    For graphs >5000 nodes, limits to first 50 seed_nodes.
    """

def _fetch_chunks_from_store(
    store: ChromaStore,
    chunk_hashes: list[str],
) -> dict[str, tuple[str, dict]]:
    """Fetch chunk content + metadata from ChromaDB via store.get_by_ids().

    Returns {chunk_hash: (document_content, metadata_dict)}.
    Skips hashes not found in ChromaDB (stale index), logs warning.
    """
```

### Logic (5 steps)

1. **Load graph** — Call `load_graph(output_dir / graph_filename)` from `pipeline/io.py`. Build reverse index via `build_reverse_index(graph)` → `{chunk_hash: [node_ids]}`. If graph not found (`FileNotFoundError`), catch and fall back to pure vector search (skip steps 3-4), log warning.

2. **Vector search** — Create `ChromaStore` + `EmbeddingConfig`. Call `store.search(query, n_results=n_results)`. Build initial `HybridSearchResult` list. Parse metadata:
   - `file` → `metadata["file"]`
   - `start_line` → `metadata["start_line"]`
   - `end_line` → `metadata["end_line"]`
   - `node_labels` → `metadata.get("node_labels", "").split("|")` (pipe-delimited, stored by `build_chunk_metadata()`)
   - `community_ids` → `[int(c) for c in metadata.get("community_ids", "").split(",") if c]` (comma-delimited, stored by `build_chunk_metadata()`)
   - `content` → `SearchResult.document`
   - `provenance="vector"`, `distance` from search result

3. **Seed nodes via reverse index** — For each vector hit, look up its `hash` in the reverse index to get graph node IDs. Collect into **deduplicated** list of seed node IDs.

4. **Graph traversal (BFS depth 2) + neighbor → chunks** — BFS from seed nodes, configurable `depth` (default 2). Skip edges with relation in `_SKIPPED_RELATIONS`. Track edge path from seed → discovered node. For each newly discovered node (not already in vector hits):
   - Read chunks via `graph.nodes[node_id].get("chunks", [])`
   - **If chunks list is empty, skip the node** (some nodes like concepts/file-level nodes have no chunk mapping)
   - Fetch content from ChromaDB via `_fetch_chunks_from_store(store, [c["hash"] for c in chunks])`
   - **If a chunk hash is not found in ChromaDB, skip it and log warning** (stale index)
   - Build `HybridSearchResult` with `provenance="graph"` and `graph_path`
   - Get `file`/`start_line`/`end_line` from ChromaDB metadata on the fetched chunk
   - Get `node_labels` and `community_ids` from the discovered node's attributes

5. **Merge + dedup** — Combine vector and graph results into single list. Dedup by `chunk_hash`:
   - If hit by both vector and graph → set `provenance="both"`, keep vector's `distance`
   - Sort: `"both"` first → `"vector"` (by distance ascending) → `"graph"` (by `len(graph_path or [])` ascending — shorter paths = closer to seeds = more relevant)
   - Return `HybridSearchResponse(query, vector_hits=N, graph_expanded=M, results=[...])`
   - `vector_hits` = count of results with `provenance in ("vector", "both")`
   - `graph_expanded` = count of results with `provenance in ("graph", "both")`

### Edge cases

- **Graph not found**: `load_graph` raises `FileNotFoundError` — catch and fall back to pure vector search (steps 1-2 only)
- **Chunk hash in graph but not in ChromaDB**: Skip (stale index), log warning
- **Empty graph (no edges)**: Graph expansion returns nothing, return vector-only results
- **Very large graphs**: BFS depth=2 is bounded; for graphs >5000 nodes, limit traversal to 50 seed nodes max
- **Nodes with no chunks**: Skip discovered nodes whose `chunks` list is empty (concepts, file-level nodes)
- **Empty vector results**: Return `HybridSearchResponse(query, vector_hits=0, graph_expanded=0, results=[])`
- **Duplicate seed nodes**: Deduplicate before passing to `_bfs_seeds()` — multiple vector hits may map to the same graph node

### Imports for `search.py`

```python
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

import networkx as nx

from codeknow.graph.chunk_mapper import build_reverse_index
from codeknow.pipeline.io import load_graph
from codeknow.schemas import HybridSearchResult, HybridSearchResponse
from codeknow.vector.chroma import ChromaConfig, ChromaStore
from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

logger = logging.getLogger(__name__)
```

---

## Phase 5: Wire Up Exports

| File | Change |
|---|---|
| `src/codeknow/vector/__init__.py` | Add `hybrid_search`, `HybridSearchResult`, `HybridSearchResponse` to imports and `__all__` |

```python
# Add to imports (inside existing with contextlib.suppress(ImportError) block):
from .search import HybridSearchResult, HybridSearchResponse, hybrid_search

# Add to __all__:
"hybrid_search",
"HybridSearchResult",
"HybridSearchResponse",
```

---

## File Summary

### Phase 1 (see [`08.1_pipeline-save-load.md`](08.1_pipeline-save-load.md))

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
    output_dir=Path.home() / ".codeknow" / "graph",
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
