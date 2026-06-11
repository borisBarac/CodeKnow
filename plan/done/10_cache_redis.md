# `cache.py` Overhaul — Redis Backend

## Context

Companion to `09_cache_overhaul.md` (file-based plan). Redis eliminates 4 items entirely (eviction/TTL, locking, async wrappers, cache index) and simplifies 3 more (namespacing, stats, memory cap). Chunked hashing and typed returns are storage-agnostic and needed regardless.

This plan assumes a `CacheStore` protocol so the file-based and Redis backends are interchangeable.

---

## Phase 0 — Abstract the cache interface

**Goal:** Decouple callers from storage backend.

- [ ] **0a. Define `CacheStore` protocol**
  - Methods: `get(path, root) -> ExtractionResult | None`, `set(path, result, root)`, `has(path, root) -> bool`, `delete(path, root)`, `stats() -> CacheStats`, `evict(active_files)`, `close()`
  - File at `src/codeknow/cache.py` or new `src/codeknow/cache/` package.

- [ ] **0b. Refactor existing file-based code into `FileCacheStore`**
  - Move current `load_cached`, `save_cached`, etc. into a class implementing `CacheStore`.
  - Keep existing top-level functions as thin wrappers around a module-level `FileCacheStore` instance — zero breakage for existing callers.

- [ ] **0c. Factory function**
  - `get_cache_store(backend="file", **kwargs) -> CacheStore`
  - Reads config or env var (`CODEKNOW_CACHE_BACKEND=file|redis`).

---

## Phase 1 — Redis backend implementation

**Goal:** Full `CacheStore` backed by Redis. Covers improvements #1, #2, #5, #6, #7, #8, #9 from the original list.

- [ ] **1a. Add `redis[hiredis]` dependency**
  - Optional dependency group: `pip install codeknow[redis]`
  - Import guarded — `FileCacheStore` works without Redis installed.

- [ ] **1b. `RedisCacheStore` class**
  - Constructor: `RedisCacheStore(url="redis://localhost:6379/0", graph_id="default", ttl: int | None = None, max_result_bytes: int = 10 * 1024 * 1024)`
  - **Key scheme:**
    - Data: `ck:cache:{graph_id}:{hash}` → JSON string
    - Index: `ck:index:{graph_id}` → Redis HASH mapping `relative_path` → `hash`
  - **`set(path, result, root)`**
    - Compute hash (chunked).
    - Serialize to JSON bytes. Check `len` against `max_result_bytes`. Skip + warn if exceeded.
    - `SET` with optional `EX=ttl` (native TTL — eliminates manual eviction).
    - `HSET ck:index:{graph_id} {relative_path} {hash}` (replaces sidecar index file).
  - **`get(path, root)`**
    - Check index: `HGET ck:index:{graph_id} {relative_path}`.
    - If missing → return `None`.
    - If hash present → `GET ck:cache:{graph_id}:{hash}`.
    - If missing (evicted by Redis) → clean index entry, return `None`.
    - Deserialize → validate through `ExtractionResult` → return.
  - **`has(path, root) -> bool`**
    - `HEXISTS ck:index:{graph_id} {relative_path}` — O(1), no hashing needed for cache-hit check (still hash for consistency, or trust index for speed — configurable).
  - **`delete(path, root)`**
    - `HGET` to find hash, `DEL` the data key, `HDEL` from index.
  - **`close()`**
    - Close connection pool.

- [ ] **1c. Namespace support (`graph_id`) — free with key prefix**
  - Every key starts with `ck:cache:{graph_id}:`. Multi-graph is just different `graph_id` values.
  - No directory creation, no path management.

- [ ] **1d. Eviction — free with Redis TTL**
  - `ttl` parameter on constructor. If set, every `SET` uses `EX=ttl`.
  - Redis auto-evicts expired keys. Index entries cleaned lazily on `get()` misses.
  - `evict(active_files)` still useful for explicit cleanup: `HSCAN` the index, `HDEL` entries not in `active_files`, `DEL` their data keys.

- [ ] **1e. Thread safety — free**
  - Redis is single-threaded and atomic. `GET`/`SET`/`HSET` are inherently safe.
  - No `filelock`, no `fcntl`, no `.lock` files. Zero code.

- [ ] **1f. Async support — free with `redis.asyncio`**
  - `AsyncRedisCacheStore` subclass using `redis.asyncio.Redis`.
  - Same methods as `RedisCacheStore` but `async def`.
  - `get_async_cache_store()` factory.
  - No thread executor needed.

- [ ] **1g. Stats API — near-free**
  - `stats() -> CacheStats`:
    - Entry count: `HLEN ck:index:{graph_id}`
    - Total bytes: maintain a counter key `ck:stats:{graph_id}:bytes` incremented on `SET`, decremented on `DEL`.
    - TTL info: `TTL` on a sample key if TTL is configured.

---

## Phase 2 — Storage-agnostic improvements (needed for both backends)

**Goal:** Improvements that are independent of storage choice.

- [ ] **2a. Chunked file hashing** (bounded memory)
  - Replace `p.read_bytes()` in `file_hash()` with 64 KB chunked reads.
  - Shared by both `FileCacheStore` and `RedisCacheStore`.

- [ ] **2b. Typed returns — `ExtractionResult` validation**
  - Both backends return `ExtractionResult | None` instead of `dict | None`.
  - `raw: bool = False` param for backward compat.

- [ ] **2c. Memory-bounded loading**
  - `check_semantic_cache(files, root, max_cache_bytes=None)` — track cumulative bytes.
  - Works the same way regardless of backend (Redis `STRLEN` to check size before `GET`, file `stat().st_size` for disk).

- [ ] **2d. Update callers**
  - `extract/ast.py` uses `get_cache_store()` or accepts a `CacheStore` instance via dependency injection.

---

## What this plan does NOT need (vs. file-based plan)

| File-based item | Redis status | Reason |
|-----------------|-------------|--------|
| Sidecar `cache_index.json` (#5) | **Eliminated** | Redis HASH is the index |
| `evict_stale()` TTL logic (#2) | **Eliminated** | Native `SETEX` |
| `filelock` / `fcntl` (#7) | **Eliminated** | Redis is atomic |
| `anyio`/`aiofiles` wrappers (#8) | **Eliminated** | `redis.asyncio` is native async |
| Directory creation / path management | **Eliminated** | Keys, not files |

---

## Execution Notes

| Item | Risk | Effort |
|------|------|--------|
| 0a CacheStore protocol | Low | Small |
| 0b Refactor to FileCacheStore | Low — wrapper pattern | Medium |
| 0c Factory + env var | Low | Small |
| 1a Add redis dependency | Low — optional extra | Small |
| 1b RedisCacheStore class | Low | Medium |
| 1c graph_id namespace | Zero — key prefix | None |
| 1d TTL eviction | Zero — SETEX | None |
| 1e Thread safety | Zero — Redis atomic | None |
| 1f Async variant | Low — redis.asyncio | Small |
| 1g Stats | Low — counter key | Small |
| 2a Chunked hashing | Low | Small |
| 2b Typed returns | Low | Small |
| 2c Memory budget | Medium | Medium |
| 2d Update callers | Low | Small |

**Recommended order:** Phase 0 → Phase 1 (Redis backend) → Phase 2 (shared improvements). Phase 0 unblocks both backends. Phase 1 is lean because Redis eliminates most of the complexity.
