# Plan: Fix `multi_graph_search` Issues

> Addresses review findings from `multi_graph_search` / `search.py` / `app.py`.

## Step 1 — Extract shared `_sort_key` (P2)

- [x] Create a shared sort key function (keep in `multi_search.py` or extract to `vector/_util.py`)
- [x] Import shared sort key in `search.py`, delete the duplicate at `search.py:213-219`

## Step 2 — Add optional pre-built resources to `hybrid_search` (P1)

- [x] Add optional `embeddings` param to `hybrid_search()` signature
- [x] Add optional `store` param to `hybrid_search()` signature
- [x] Skip `create_embeddings()` / `ChromaStore()` construction when pre-built args are provided
- [x] Verify backward compatibility — existing callers without these args still work

## Step 3 — Share resources + parallelize in `multi_graph_search` (P1)

- [x] Create `embeddings` once at the top of `multi_graph_search` using `embed_config`
- [x] Determine shared store strategy — one `ChromaStore` per slug (collections differ), but reuse the embeddings client
- [x] Replace sequential `for` loop with `concurrent.futures.ThreadPoolExecutor.map()`
- [x] Pass shared `embeddings` to each `hybrid_search` call
- [x] Aggregate results from parallel threads into `all_results`

## Step 4 — Fix in-place mutation (P3)

- [x] Replace `r.slug = slug` mutation in `multi_search.py:80` with immutable pattern (e.g. `r.model_copy(update={"slug": slug})`)
- [x] Verify no other mutation of `hybrid_search` results elsewhere

## Step 5 — Add input validation (P2)

- [x] Validate `query` is non-empty — raise `ValueError` if blank
- [x] Clamp `total_limit` to `max(1, total_limit)`
- [x] Clamp `n_results_per_graph` to `max(1, n_results_per_graph)`
- [x] Log a warning if `graph_base_dir` does not exist

## Step 6 — Fix event-loop blocking in API handler (P0)

- [x] Wrap `multi_graph_search(...)` call in `app.py:113` with `await asyncio.to_thread(multi_graph_search, ...)`
- [x] Verify the `/v1/search` endpoint still returns correct responses

## Files touched

| File | Steps |
|------|-------|
| `packages/codeknow-lib/src/codeknow/vector/multi_search.py` | 1, 3, 4, 5 |
| `packages/codeknow-lib/src/codeknow/vector/search.py` | 1, 2 |
| `packages/codeknow-api/src/codeknow_api/app.py` | 6 |

## Execution order

1 → 2 → 3 → 4 → 5 → 6 (incremental, testable at each step)
