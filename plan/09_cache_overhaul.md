# `cache.py` Overhaul Plan — Multi-Graph, Memory-Conscious Backend

## Surface area (callers to update)
- `src/codeknow/extract/ast.py:14` — imports `load_cached`, `save_cached` (lines 921, 927)
- `src/codeknow/cache.py` — `check_semantic_cache`, `save_semantic_cache` (currently unused externally, but part of the public API)

---

## Phase 1 — Foundation (blocking for multi-graph + correctness)

**Goal:** Namespace support, chunked hashing, cache index. All callers updated.

- [ ] **1a. `graph_id` parameter on every public function**
  - `cache_dir(root, graph_id="default")` → `graph-out/cache/{graph_id}/`
  - `load_cached(path, root, graph_id="default")`
  - `save_cached(path, result, root, graph_id="default")`
  - `cached_files(root, graph_id="default")`
  - `clear_cache(root, graph_id=None)` (None = all graphs)
  - `check_semantic_cache(files, root, graph_id="default")`
  - `save_semantic_cache(nodes, edges, hyperedges, root, graph_id="default")`
  - Default `"default"` keeps backward compat — existing callers with no `graph_id` work unchanged.

- [ ] **1b. Chunked file hashing** (bounded memory)
  - Replace `p.read_bytes()` in `file_hash()` with 64 KB chunked reads via `p.open('rb')`.
  - Strip frontmatter for `.md` still works — read raw first 4 bytes to detect `---`, then stream body if needed (or keep current approach for `.md` since frontmatter is tiny).

- [ ] **1c. Sidecar cache index (`cache_index.json`)**
  - Per-graph index file at `graph-out/cache/{graph_id}/cache_index.json`
  - Schema: `{ "relative_path": str -> "hash": str, "mtime": float, "size": int }`
  - `save_cached()` appends/updates the index entry.
  - `load_cached()` checks index first — if `relative_path` not in index, skip. If present, verify hash matches (cheap string compare). Only re-hash on mismatch.
  - `cached_files()` reads the index keys (instant) instead of globbing + re-hashing.
  - `evict_stale()` uses the index to find orphaned entries.
  - Add `_write_index()` / `_read_index()` helpers with tmp-rename atomicity.

- [ ] **1d. Update caller — `extract/ast.py`**
  - Pass `graph_id` through to `load_cached` / `save_cached` (default `"default"` keeps current behavior).
  - This is a one-line change at the call sites.

---

## Phase 2 — Memory Safety + Eviction (blocking for production)

**Goal:** Bounded memory, stale entry cleanup.

- [ ] **2a. `max_result_bytes` guard on `save_cached()`**
  - Serialize to JSON bytes first, check len against limit (default 10 MB).
  - Log warning and skip if exceeded. Return `bool` indicating success.

- [ ] **2b. `max_cache_bytes` budget on `check_semantic_cache()`**
  - Track cumulative bytes loaded. Stop loading entries once budget exceeded.
  - Return partially-loaded results + remaining files in `uncached`.
  - Caller decides whether to re-extract the remainder.

- [ ] **2c. `evict_stale(graph_id, active_files: set[Path])`**
  - Reads index, compares keys against `active_files` (relative paths).
  - Deletes JSON files whose source no longer exists.
  - Removes stale entries from index.
  - Optional `ttl_hours` parameter — evict entries older than N hours regardless.

- [ ] **2d. `CachedResult` lazy wrapper**
  - Instead of returning raw `dict` from `load_cached()`, return a `CachedResult` that holds the path and only deserializes on property access (`.nodes`, `.edges`, `.hyperedges`).
  - Keeps `check_semantic_cache()` from materializing everything at once.

---

## Phase 3 — Concurrency + Async (blocking for multi-worker backend)

**Goal:** Thread-safe, async-compatible.

- [ ] **3a. File locking on load/save**
  - Add `filelock` (or `fcntl.flock` on Unix) around read/write in `load_cached()` and `save_cached()`.
  - Lock file at `graph-out/cache/{graph_id}/.lock`.
  - Keep tmp-rename pattern for atomicity.

- [ ] **3b. Async variants**
  - `async_load_cached()`, `async_save_cached()`, `async_check_semantic_cache()` using `anyio.Path` + `anyio.to_thread.run_sync()` for hashing.
  - Keep sync versions as the primary API; async wrappers delegate to thread pool.

---

## Phase 4 — Observability + Type Safety (nice-to-haves)

**Goal:** Monitoring, typed returns.

- [ ] **4a. `CacheStats` dataclass + `cache_stats(graph_id)` function**
  - Returns: `entry_count`, `total_bytes`, `oldest_mtime`, `newest_mtime`, `index_size`.
  - Computed from index file + directory walk (or purely from index for speed).

- [ ] **4b. Typed returns — validate through `ExtractionResult`**
  - `load_cached()` returns `ExtractionResult | None` instead of `dict | None`.
  - `check_semantic_cache()` returns typed lists (`list[Node]`, `list[Edge]`, etc.).
  - Add a `raw: bool = False` param for backward compat (return dict when True).

---

## Execution Notes

| Item | Risk | Effort |
|------|------|--------|
| 1a graph_id | Low — additive param with default | Small |
| 1b chunked hash | Low — same output, different I/O | Small |
| 1c cache index | Medium — new file format, migration | Medium |
| 1d update caller | Low — default param, no logic change | Small |
| 2a size guard | Low | Small |
| 2b memory budget | Medium — changes return contract | Medium |
| 2c eviction | Low | Small |
| 2d lazy wrapper | Medium — changes downstream access pattern | Medium |
| 3a file locking | Low — well-understood pattern | Small |
| 3b async variants | Medium — new dependency (`anyio`/`aiofiles`) | Medium |
| 4a stats | Low | Small |
| 4b typed returns | Low — Pydantic already imported | Small |

**Recommended test coverage:** each phase should include tests for the new functionality before moving to the next phase. Phase 1 is the highest priority since everything else depends on it.
