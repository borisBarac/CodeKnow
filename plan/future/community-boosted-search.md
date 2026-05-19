# Plan: Community-Boosted Re-Ranking

## Goal

After vector search returns initial hits, identify the **most relevant communities** (those with the most/best vector hits) and **boost results** from those communities in the final sort — so results from a coherent module the user is querying about rank higher than disconnected hits.

## Current Sort (no community awareness)

`vector/_utils.py:26-33` — `_sort_key()` sorts by:
1. `provenance` (vector first, graph second)
2. `distance` (lower cosine distance = better)
3. `cumulative_weight` (higher = better graph path)
4. `len(graph_path)` (tiebreaker)

Communities are **ignored**.

## Algorithm

After `hybrid_search()` collects all results (vector + graph expansion), compute a **community relevance score** from vector hits, then add it to the sort key:

```
1. Collect vector hits → tally per-community: community_score[cid] = Σ (1 - distance) for each vector hit in that community
2. For every result (vector + graph), look up its community_ids and take the max community_score
3. Sort by: provenance → distance → community_boost → cumulative_weight → path_len
```

This means: if vector search returns 3 hits in community 5 (e.g., the "auth" module), then community 5 gets a high boost. Any graph-expanded result that also belongs to community 5 gets boosted above graph results from unrelated communities.

## Files to Change

| # | File | Change |
|---|------|--------|
| 1 | `vector/_utils.py` | Add `compute_community_scores(results) → dict[int, float]` and update `sort_key()` to accept and use a community boost |
| 2 | `vector/search.py` | Call `compute_community_scores()` after results are collected, wire boost into sort |
| 3 | `vector/multi_search.py` | Same — after merging results from multiple graphs, compute community scores and sort with boost |

## Detailed Changes

### 1. `vector/_utils.py`

```python
def compute_community_scores(
    results: list[HybridSearchResult],
) -> dict[int, float]:
    """Score each community based on vector-hit relevance.

    For each vector hit, its community_ids each accumulate (1 - distance).
    Communities with more/better vector hits get higher scores.
    """
    scores: dict[int, float] = {}
    for r in results:
        if r.provenance != "vector" or r.distance is None:
            continue
        contribution = 1.0 - r.distance  # higher = closer vector match
        for cid in r.community_ids:
            scores[cid] = scores.get(cid, 0.0) + contribution
    return scores
```

Update `sort_key` signature to accept optional `community_scores`:

```python
def sort_key(
    r: HybridSearchResult,
    community_scores: dict[int, float] | None = None,
) -> tuple:
    provenance_order = {"vector": 0, "graph": 1}

    community_boost = 0.0
    if community_scores and r.community_ids:
        community_boost = max(
            community_scores.get(cid, 0.0) for cid in r.community_ids
        )

    return (
        provenance_order.get(r.provenance, 2),
        r.distance if r.distance is not None else float("inf"),
        -community_boost,          # NEW: boost results from relevant communities
        -(r.cumulative_weight or 0.0),
        len(r.graph_path or []),
    )
```

### 2. `vector/search.py` — `hybrid_search()` lines 243-245

```python
# Before:
results.sort(key=_sort_key)

# After:
from codeknow.vector._utils import compute_community_scores
community_scores = compute_community_scores(results)
results.sort(key=lambda r: _sort_key(r, community_scores))
```

### 3. `vector/multi_search.py` — line 133

Same pattern — compute community scores from the merged result set before sorting:

```python
# Before:
all_results.sort(key=_sort_key)

# After:
from codeknow.vector._utils import compute_community_scores
community_scores = compute_community_scores(all_results)
all_results.sort(key=lambda r: _sort_key(r, community_scores))
```

## Why This Works

- **No schema changes needed** — `community_ids` is already on every `HybridSearchResult` (populated for vector hits from ChromaDB metadata, and for graph-expanded results from node data).
- **No pipeline changes** — community metadata is already embedded in ChromaDB chunks.
- **Purely additive** — the sort just gets an additional community-boost dimension inserted between `distance` and `cumulative_weight`.
- **Graph-expanded results also benefit** — they inherit community_ids from the node they were discovered via, so if a graph result belongs to a community that had vector hits, it gets boosted too.
- **No effect when communities are absent** — `community_scores` is an empty dict, boost is 0.0 for everything, sort behaves exactly as before.

## Edge Cases

| Case | Behavior |
|------|----------|
| No communities on graph | `community_ids` is `[]` on all results → boost = 0.0 → identical to current sort |
| All vector hits in one community | That community dominates the boost; graph results from it rank higher |
| Vector hits spread across N communities | Each community gets proportional boost; no single community dominates |
| Pure vector search (no graph) | Community boost still re-ranks vector hits by community coherence |
| Multi-graph search | Community scores computed per-graph automatically (different graphs have independent community IDs) |

## Testing

- Unit test `compute_community_scores()` with mock results
- Unit test `sort_key()` with community_scores vs without — verify identical ordering without scores, boosted ordering with scores
- Integration test: vector hits in community 5 → graph result from community 5 should rank above graph result from community 99 (all else equal)
