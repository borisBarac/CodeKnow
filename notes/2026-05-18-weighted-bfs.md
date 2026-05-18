# 2026-05-18: Weighted BFS for Hybrid Search

## Goal
Replace binary `_SKIPPED_RELATIONS` in hybrid search BFS with weighted relation traversal that prioritizes semantically valuable edges.

## What Changed

### `packages/codeknow-lib/src/codeknow/vector/search.py`
- Replaced `_SKIPPED_RELATIONS` frozenset + `collections.deque` BFS with `_RELATION_WEIGHTS` dict + `heapq` priority BFS
- New constants: `_RELATION_WEIGHTS`, `_DEFAULT_RELATION_WEIGHT` (0.0), `_MAX_GRAPH_RESULTS` (50)
- Removed `from collections import deque` import
- `_bfs_seeds` now uses Dijkstra-like "mark-visited-on-pop" pattern

### `packages/codeknow-lib/tests/test_weighted_bfs.py`
- 8 tests total: 4 regression (existing behavior preserved), 4 new weighted behavior tests

## Weight Taxonomy
| Relation | Weight | Rationale |
|---|---|---|
| imports, imports_from, contains, method | 0.0 | Structural/syntactic — skip |
| calls | 0.7 | Partial semantic signal |
| uses | 0.7 | Partial semantic signal |
| inherits | 0.8 | Strong structural relationship |
| rationale_for | 0.9 | High semantic value |
| semantically_similar_to | 1.0 | Highest semantic value |
| Unknown/default | 0.0 | Must explicitly opt-in |

## Key Decisions
- **Mark-visited-on-pop** (not on-push): ensures highest-cumulative-weight path wins for each node
- **Seeds NOT pre-added to `visited`**: pushed to heap at weight 0.0, marked visited when popped
- **Removed shorter-path fallback**: old code had `elif neighbor in discovered and len(new_path) < len(discovered[neighbor])` — heapq guarantees highest-weight path is always first
- **`_DEFAULT_RELATION_WEIGHT = 0.0`**: new/unknown relations are silently skipped (must explicitly opt-in)

## Results
- All 8 weighted BFS tests pass
- Full suite regression: 91/91 passed, 0 failures
- Lint clean: `ruff check` + `ruff format` pass

## Code Review (self-review via task agent)
- **No bugs found**
- **Two intentional behavioral changes**:
  1. Paths are now "highest-weight" not "shortest-hop"
  2. Seeds visited on pop, not on push (no practical impact due to `if node_id not in seeds` guard)
- **Note**: `nx.Graph` is undirected — `calls` edges traverse both A→B and B→A (same as before)

## Architecture Notes
- `engine.py` has its own separate `_bfs` (simple frontier-based, for MCP queries) — **untouched**
- `search.py` `_bfs_seeds` is for internal hybrid search pipeline — **this is what changed**
- `analyze.py` has parallel skip sets at lines 211 and 312 — candidate for future dedup with `_RELATION_WEIGHTS`

## 2026-05-18 (update): Batch Expansion Optimization

### Problem
When graph expansion returned no new chunks beyond what vector search already found, the code still:
1. Ran full BFS traversal (unavoidable)
2. Made a ChromaDB fetch **per discovered node** (N round trips)
3. Fetched already-known chunks from ChromaDB, only to discard them at line 223

### What Changed

#### `packages/codeknow-lib/src/codeknow/vector/search.py` (lines 207-244)
Replaced single per-node loop with two-phase batch approach:

**Phase 1 — Collect & pre-filter** (lines 209-225):
- `node_chunk_map: dict[str, tuple[list[str], list[str], str]]` — maps `node_id → (new_hashes, path, node_label)`
- `all_new_hashes: set[str]` — union of all new chunk hashes across nodes
- Pre-filters `vector_hashes` before any ChromaDB call: `new_hashes = [h for h in chunk_hashes if h not in vector_hashes]`

**Phase 2 — Batch fetch & distribute** (lines 227-244):
- Single `_fetch_chunks_from_store(store, list(all_new_hashes))` call instead of one per node
- Distributes fetched results back to nodes via `node_chunk_map`

### Impact
- **No wasted ChromaDB fetches** for already-known chunks
- **N round trips → 1 round trip** to ChromaDB
- When expansion yields nothing new: BFS still runs but ChromaDB is never called (`all_new_hashes` stays empty)

## 2026-05-19 (update): Relation Weight Tuning

### Problem
For TypeScript/Next.js repos (e.g. code-test-small), the AST extractor only emits
`imports_from`, `contains`, `method`, and a single `calls` edge. With all structural
relations at weight 0.0, BFS could not traverse any import or containment chains —
graph expansion produced zero new chunks for 114 `imports_from` edges.

### What Changed

#### `packages/codeknow-lib/src/codeknow/vector/weights.py`
Updated weights for previously-zero relations:

| Relation | Old Weight | New Weight | Rationale |
|---|---|---|---|
| imports | 0.0 | 0.3 | Weak but enables cross-file dependency tracing |
| imports_from | 0.0 | 0.3 | Same — dominant relation in TS repos (114 edges in code-test-small) |
| method | 0.0 | 0.3 | Class→method membership is meaningful |
| contains | 0.0 | 0.15 | Very broad, low signal — but provides file-level context |

Updated weight hierarchy:
```
semantically_similar_to (1.0) > rationale_for (0.9) > inherits (0.8)
  > calls/uses (0.7) > imports/imports_from/method (0.3) > contains (0.15)
  > unknown/default (0.0, skipped)
```

#### `packages/codeknow-lib/tests/test_weighted_bfs.py`
- `test_zero_weight_edges_not_traversed`: changed from `imports`/`contains` to
  `unknown_structural`/`untyped_edge` (unlisted relations, default weight 0.0)
- All 9 tests pass

#### `e2e/judge/judge.py`
- Updated system prompt to document the new weight taxonomy
- Updated judging rules to reflect that `imports_from` (0.3) > `contains` (0.15)

#### `e2e/graph_gen/test_hybrid_search.py`
Three optimizations to judge integration:

1. **`_synthesize_analysis(resp)`**: generates a brief structured analysis from
   search results (files found, line ranges, graph paths). Provides `agent_analysis`
   to the judge so groundedness (15% weight) is evaluable instead of always 0.

2. **`_enforce_semantic_saturation(output, graph_hit_count)`**: post-processes
   judge output — when `graph_expanded == 0`, forces `kg_expansion_value = null`
   and recalculates `final_score` using the saturation formula (50 placeholder).
   Fixes the LLM sometimes giving 0 instead of null.

3. **`_print_judge_report`**: fixed `{val:3d}` format string crash when
   `kg_expansion_value` is `None` — now prints `"N/A"`.

### Design Rationale
- `imports_from` at 0.3 (not higher): import chains are noisy (stdlib, framework
  imports), but following them discovers files that vector search may miss
- `contains` at 0.15 (lowest positive): a file "contains" many functions, so this
  is very broad — low weight ensures BFS prefers tighter relationships first
- BFS guardrails prevent noise explosion: priority queue favors higher-weight edges,
  `_MAX_GRAPH_RESULTS=50` caps expansion, cumulative weight diminishes for longer paths

## Follow-ups (out of scope)
- Wire `graph_score` into `HybridSearchResult` for final sort order
- Deduplicate relation weights with `analyze.py` (lines 211, 312) into single source of truth
- Consider relation directionality if `nx.Graph` undirected traversal becomes a problem
