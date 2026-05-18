# Plan: Fix `multi_graph_search` Issues

> Addresses review findings from `multi_graph_search` / `search.py` / `app.py`.

## Step 1 — Extract shared `_sort_key` (P2)

- [ ] Create a shared sort key function (keep in `multi_search.py` or extract to `vector/_util.py`)
- [ ] Import shared sort key in `search.py`, delete the duplicate at `search.py:213-219`

## Step 2 — Add optional pre-built resources to `hybrid_search` (P1)

- [ ] Add optional `embeddings` param to `hybrid_search()` signature
- [ ] Add optional `store` param to `hybrid_search()` signature
- [ ] Skip `create_embeddings()` / `ChromaStore()` construction when pre-built args are provided
- [ ] Verify backward compatibility — existing callers without these args still work

## Step 3 — Share resources + parallelize in `multi_graph_search` (P1)

- [ ] Create `embeddings` once at the top of `multi_graph_search` using `embed_config`
- [ ] Determine shared store strategy — one `ChromaStore` per slug (collections differ), but reuse the embeddings client
- [ ] Replace sequential `for` loop with `concurrent.futures.ThreadPoolExecutor.map()`
- [ ] Pass shared `embeddings` to each `hybrid_search` call
- [ ] Aggregate results from parallel threads into `all_results`

## Step 4 — Fix in-place mutation (P3)

- [ ] Replace `r.slug = slug` mutation in `multi_search.py:80` with immutable pattern (e.g. `r.model_copy(update={"slug": slug})`)
- [ ] Verify no other mutation of `hybrid_search` results elsewhere

## Step 5 — Add input validation (P2)

- [ ] Validate `query` is non-empty — raise `ValueError` if blank
- [ ] Clamp `total_limit` to `max(1, total_limit)`
- [ ] Clamp `n_results_per_graph` to `max(1, n_results_per_graph)`
- [ ] Log a warning if `graph_base_dir` does not exist

## Step 6 — Fix event-loop blocking in API handler (P0)

- [ ] Wrap `multi_graph_search(...)` call in `app.py:113` with `await asyncio.to_thread(multi_graph_search, ...)`
- [ ] Verify the `/v1/search` endpoint still returns correct responses

## Files touched

| File | Steps |
|------|-------|
| `packages/codeknow-lib/src/codeknow/vector/multi_search.py` | 1, 3, 4, 5 |
| `packages/codeknow-lib/src/codeknow/vector/search.py` | 1, 2 |
| `packages/codeknow-api/src/codeknow_api/app.py` | 6 |

## Execution order

1 → 2 → 3 → 4 → 5 → 6 (incremental, testable at each step)
