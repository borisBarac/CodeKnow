# Candidate 1: Collapse the hybrid search god module

**Strength:** Strong
**Dependency category:** in-process

## Files involved

| File | Lines | Role |
|---|---|---|
| `codeknow/vector/search.py` | 264 | Entry point — the god module |
| `codeknow/vector/multi_search.py` | 141 | Multi-graph wrapper |
| `codeknow/vector/weights.py` | 23 | Relation weight constants |
| `codeknow/vector/_utils.py` | 23 | Single helper: `read_chunk_content()` |
| `codeknow/vector/store.py` | 83 | `VectorStore` Protocol + `SearchResult` model |
| `codeknow/vector/chroma.py` | 323 | ChromaDB adapter (only VectorStore implementation) |
| `codeknow/vector/embeddings.py` | 128 | Embeddings factory (Ollama/OpenRouter) |
| `codeknow/vector/__init__.py` | 50 | Re-exports with ImportError suppression |
| `codeknow/chunking/index.py` | 22 | Single function: `build_reverse_index()` |
| `codeknow/pipeline/io.py` | 107 | `load_graph()` — imported by search.py |
| `codeknow/schemas.py` | 168 | `HybridSearchResult`, `HybridSearchResponse` |

## Problem

`hybrid_search()` in `vector/search.py` is a ~100-line function that imports from 7 modules across 4 sub-packages. It is simultaneously responsible for:

1. Loading a NetworkX graph from disk (`load_graph`)
2. Building a reverse index from chunk hashes to graph node IDs (`build_reverse_index`)
3. Creating an embeddings instance (`create_embeddings`)
4. Running vector similarity search via ChromaStore
5. Running weighted BFS graph expansion from seed nodes (`_bfs_seeds`)
6. Fetching additional chunks from ChromaDB via graph traversal (`_fetch_chunks_from_store`)
7. Merging and sorting results (`sort_key`)

Callers (the API handler in `app.py`, e2e tests) must supply correctly-wired `ChromaStore` + `Embeddings` + graph path. The wiring is done by the caller, not owned by the search module.

**Shallow modules that orbit around search.py:**

- **`vector/_utils.py`** (23 lines): Contains a single function `read_chunk_content()` that reads lines from a file. Could be inlined.
- **`vector/store.py`** (83 lines): `VectorStore` Protocol mirrors `ChromaStore`'s method signatures almost exactly. Only one implementation exists (`ChromaStore`), so the abstraction adds indirection without payoff.
- **`vector/weights.py`** (23 lines): A constants dict. Trivially inlinable.
- **`chunking/index.py`** (22 lines): A single 6-line function `build_reverse_index()`. Used by `search.py` and `metadata.py`.

## Current dependency graph

```
hybrid_search()
├── load_graph()                    from pipeline/io.py
├── build_reverse_index()           from chunking/index.py
├── create_embeddings()             from vector/embeddings.py
├── ChromaStore.search()            from vector/chroma.py
├── _bfs_seeds()                    internal to search.py
├── _fetch_chunks_from_store()      internal to search.py
├── RELATION_WEIGHTS                from vector/weights.py
├── sort_key()                      internal to search.py
└── read_chunk_content()            from vector/_utils.py (transitive via chroma.py)
```

`multi_search.py` wraps `hybrid_search` for multiple graph directories:
```
multi_graph_search()
├── _discover_graph_dirs()          walks filesystem for graph dirs
├── _search_single_graph()          calls hybrid_search per graph
├── ThreadPoolExecutor              parallel execution
└── sort_key                        re-imported for merging
```

## Testing gaps

- `hybrid_search()` itself has **zero tests**. Only the internal `_bfs_seeds()` function is tested (9 tests in `test_weighted_bfs.py`).
- The integration point where vector results become seed nodes and the reverse index joins them is completely untested.
- `multi_graph_search()` has zero tests.
- `vector/embeddings.py` has zero tests.
- Only `ChromaStore.store_chunks` extra metadata and `delete_by_slug` are tested (in `test_embed_stage.py`). Core search and connection management are untested without a running ChromaDB.

## Proposed solution

Deepen into a **`GraphSearcher`** class that owns the graph, reverse index, vector store, and embeddings as internal state.

### Interface

```python
class GraphSearcher:
    def __init__(self, graph_dir: Path, embedding_config: EmbeddingConfig, chroma_config: ChromaConfig): ...

    def search(self, query: str, top_k: int = 10) -> HybridSearchResponse: ...

    @classmethod
    def multi_search(cls, base_dir: Path, query: str, top_k: int = 10, slugs: list[str] | None = None) -> HybridSearchResponse: ...
```

### What gets absorbed internally

- `load_graph()` — called in `__init__`
- `build_reverse_index()` — called in `__init__`
- `create_embeddings()` — called in `__init__`
- `ChromaStore` construction — called in `__init__`
- `_bfs_seeds()` — private method
- `_fetch_chunks_from_store()` — private method
- `RELATION_WEIGHTS` — internal constant
- `sort_key` — private function
- `read_chunk_content()` — internal helper (delete _utils.py)
- `multi_graph_search()` + `_discover_graph_dirs()` — folded into `multi_search` classmethod

### What stays at the seam

- `search(query, top_k) → HybridSearchResponse`
- `multi_search(base_dir, query, top_k, slugs) → HybridSearchResponse`

### Callers after

```python
# In app.py search handler
searcher = GraphSearcher(graph_dir, embed_config, chroma_config)
result = searcher.search(query, top_k)

# Or multi-graph:
result = GraphSearcher.multi_search(Path(GRAPH_DIR), query, top_k, slugs)
```

## Wins

- **leverage**: one interface, N call sites (API, CLI, e2e)
- **locality**: graph wiring bugs concentrate in one module
- **interface shrinks**; implementation absorbs 5 shallow files
- **delete 2 pass-through modules** (_utils.py, index.py)
- **testability**: tests exercise `search()` against a real fixture graph + ChromaDB, not internal helpers

## Risks / considerations

- `build_reverse_index()` is also used by `pipeline/metadata.py`. If it's absorbed, metadata.py needs to call into the searcher or have its own copy. The function is 6 lines, so a copy is fine, or it can be kept as a shared internal utility.
- `VectorStore` Protocol currently has only one adapter (`ChromaStore`). If a second adapter is added later, the seam is justified. For now, the Protocol adds indirection. Consider absorbing it and re-introducing the Protocol only when a second adapter is needed ("one adapter = hypothetical seam, two adapters = real seam").
- The `__init__` cost of loading graph + building reverse index + creating embeddings + connecting to ChromaDB is non-trivial. The class should support lazy initialization or the caller should be able to reuse instances.

## Implementation status

**Overall: ~60% done.** `GraphSearcher` class created and wired as the primary call path, but internals not yet absorbed into the class.

### Done

- `GraphSearcher` class exists at `vector/search.py:134` with `search()` and `multi_search()` classmethod
- API layer rewired: `app.py` → `PipelineFacade.search()` → `GraphSearcher.multi_search()` (no longer calls `hybrid_search()` or `multi_graph_search()` directly)
- `_bfs_seeds()` tests exist (9 tests in `test_weighted_bfs.py`)
- `GraphSearcher` integration tested (`test_graph_searcher.py`)

### Remaining cleanup

| # | Task | Effort | Blockers |
|---|------|--------|----------|
| 1 | **Delete `vector/multi_search.py`** — zero live importers, dead code | Trivial | None |
| 2 | **Absorb 4 module-level functions into `GraphSearcher`**: `_bfs_seeds` (L27), `_fetch_chunks_from_store` (L81), `sort_key` (L104), `_discover_graph_dirs` (L114). Move `_MAX_GRAPH_RESULTS` to class attr. | Small | None |
| 3 | **Inline `read_chunk_content()` and delete `vector/_utils.py`** — only 2 importers: `chroma.py:18`, `embeddings.py:29`. Update patch path in `test_embed_stage.py:261`. | Small | None |
| 4 | **Absorb `weights.py` constants into `GraphSearcher`** as class attrs. Update 1 external importer: `e2e/judge/judge.py:19`. | Small | None |
| 5 | **Remove `hybrid_search()` backward-compat wrapper** (search.py:374). Migrate 2 callers: `e2e/graph_gen/test_hybrid_search.py:36` and `test_graph_searcher.py:157`, then delete the function. | Small | None |
| 6 | **Clean `__init__.py`** — remove `hybrid_search` from `__all__` once deleted | Trivial | Depends on #5 |
| — | **Skip `vector/store.py`** — `VectorStore` Protocol and `SearchResult` serve independent roles beyond search | — | — |
| — | **Skip `chunking/index.py`** — `build_reverse_index()` used by `pipeline/metadata.py` independently | — | — |
