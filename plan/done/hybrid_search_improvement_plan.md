# Hybrid Search Improvement Plan

Based on analysis of the e2e test reports, search implementation (`vector/search.py`), judge system, and ground-truth evaluation.

**Current State:** Hybrid search combines vector similarity (ChromaDB) with graph BFS traversal using static relation weights. Evaluation shows decent P@5/R@10 but room for improvement in result ordering, query adaptability, and evaluation rigor.

## High Priority (Must Implement)

### 1. Add Sparse Retrieval (BM25) to the Vector Stage
- **File:** `vector/search.py:220`
- **Problem:** Pure dense embedding search misses exact symbol matches (e.g., `createChannel`, `trpc`, `DrizzleConfig`).
- **Fix:** 
  - Integrate `rank_bm25` library for keyword matching
  - Add BM25 stage before graph expansion: tokenize query + chunk content, compute BM25 scores
  - Use Reciprocal Rank Fusion (RRF) to merge dense (vector) + sparse (BM25) results: `score = alpha/(rank_dense + k) + beta/(rank_sparse + k)`
  - Default weights: `alpha=0.6, beta=0.4, k=60` (standard RRF)
- **Impact:** Improved precision on identifier-heavy queries; better handling of exact matches
- **Validation:** Should improve P@5 for queries with specific symbols by 15-25%

### 2. Query-Aware Edge Weights
- **File:** `vector/search.py:33-43`
- **Problem:** `RELATION_WEIGHTS` are static. Query about "auth flow" should boost `calls` edges; "schema" queries should boost `contains`.
- **Fix:**
  - Add lightweight query intent classifier (rule-based initially):
    - "flow", "process", "how.*work" → boost `calls`, `uses` (0.8 → 0.9)
    - "schema", "structure", "fields", "columns" → boost `contains` (0.15 → 0.5)
    - "inherit", "extend", "implement" → boost `inherits` (0.8 → 0.95)
    - "similar", "like", "compare" → boost `semantically_similar_to` (1.0 → 1.1)
  - Apply boosts before BFS traversal
- **Impact:** Better graph expansion relevance across diverse query types; 20-30% improvement in graph expansion value scores
- **Validation:** Judge scores for `kg_expansion_value` should increase by 10-15 points

### 3. Cross-Source Re-Ranking with Unified Relevance Score
- **File:** `vector/search.py:188-196`
- **Problem:** Sort key hardcodes vector before graph results. No unified relevance score across sources.
- **Fix:**
  - Replace static sort key with dynamic relevance scoring:
    - Vector results: `relevance = 1.0 - normalized_distance` 
    - Graph results: `relevance = min(0.95, cumulative_weight / max_possible_weight)`
    - Final score: `combined_score = relevance * (1.0 if vector else 0.95)` (slight vector preference)
  - Sort all results by `combined_score` descending
- **Impact:** Higher-quality result ordering; better MRR/NDCG; graph results can surface above vector results when highly relevant
- **Validation:** MRR should improve by 15-20%; judge `semantic_relevance` scores should increase

## Medium Priority (Should Implement)

### 4. Use Edge `confidence_score` During BFS
- **File:** `vector/search.py:148`, `schemas.py:81`
- **Problem:** BFS only uses `RELATION_WEIGHTS[relation]`, ignoring `confidence_score` (0.0–1.0) on edges. INFERRED and EXTRACTED edges within the same relation type are treated equally.
- **Fix:**
  - Modify `_bfs_seeds()` to use `effective_weight = base_weight * (confidence_score or 1.0)`
  - For EXTRACTED edges: `confidence_score` typically 0.8-1.0
  - For INFERRED edges: `confidence_score` typically 0.3-0.7  
  - For AMBIGUOUS edges: `confidence_score` typically 0.1-0.5
  - This naturally downweights lower-confidence relationships
- **Impact:** Higher-quality graph paths; EXTRACTED edges preferred over INFERRED; more reliable graph expansion
- **Validation:** Graph paths should contain fewer speculative/inferred connections; judge `kg_expansion_value` should improve

### 5. Community-Aware Boosting
- **Files:** `vector/search.py`, `graph/cluster.py`
- **Problem:** Communities are computed and assigned to nodes but never used during search.
- **Fix:**
  - After vector search, identify dominant community(ies) from top-k vector hits
  - During BFS, boost edges within the same community by factor `(1.0 + 0.2 * cohesion_score)`
  - Penalize cross-community edges by factor `0.8` (unless query explicitly asks for cross-cutting concerns)
  - Use community cohesion scores from clustering to modulate boost strength
- **Impact:** Improved `coverage` judge scores; more coherent result sets; better handling of modular codebases
- **Validation:** Results should be more focused within relevant modules; coverage scores should increase by 5-10 points

### 6. Distance Estimates for Graph Results
- **File:** `vector/search.py:291-301`
- **Problem:** Graph-expanded results have `distance=None`, always sorted after vector hits (`float("inf")`).
- **Fix:**
  - For graph results, estimate distance as: `estimated_distance = seed_distance * (1.0 - min(0.8, cumulative_weight / max_cumulative_weight))`
  - Where `seed_distance` is the vector distance of the originating seed node
  - This ensures graph results with high cumulative weight get good distance estimates
  - Cap maximum improvement at 20% better than seed (conservative)
- **Impact:** Better result interleaving; graph results compete on equal footing; improved result diversity
- **Validation:** Graph results should appear throughout the top-10, not just at the end; MRR should improve

### 7. Deduplication of Overlapping Chunks
- **File:** `vector/search.py:249-301`
- **Problem:** Vector and graph hits from the same file with overlapping line ranges both appear in results.
- **Fix:**
  - After merging results, group by file path
  - For each file, detect overlapping line ranges (chunks where `max(start1, start2) <= min(end1, end2)`)
  - Keep only the higher-ranked result (based on relevance score) from each overlapping group
  - Preserve both results only if line ranges are disjoint
- **Impact:** Improved `noise_control` judge scores; cleaner result sets; reduced redundancy
- **Validation:** Average result set size should decrease by 10-15% with no loss in coverage; noise_control scores should improve by 10+ points

## Evaluation Improvements (Critical for Measuring Progress)

### 8. Add Ranking-Aware Metrics (MRR + NDCG)
- **File:** `test_hybrid_search.py:278-315`
- **Problem:** P@5, R@10, F1@10 ignore result ordering. A relevant result at position 1 vs position 5 is treated the same.
- **Fix:**
  - Implement Mean Reciprocal Rank (MRR): `MRR = (1/|Q|) * Σ(1/rank_i)` where `rank_i` is position of first relevant result
  - Implement Normalized Discounted Cumulative Gain (NDCG@10): `DCG = Σ(rel_i / log2(i+1))`, normalized by ideal DCG
  - Add to existing metrics suite; report alongside P/R/F1
  - Set baseline thresholds: MRR ≥ 0.65, NDCG@10 ≥ 0.70
- **Impact:** Better signal for ranking improvements; captures user experience more accurately
- **Validation:** Should correlate strongly with judge `semantic_relevance` scores

### 9. Finer-Grained Ground Truth
- **File:** `test_hybrid_search.py:274-275`
- **Problem:** `_is_relevant()` checks file suffix only — any chunk from a relevant file counts, even if unrelated to query.
- **Fix:**
  - Extend ground truth to include line ranges or symbol names:
    - `"src/server/db/schema.ts:L15-L45"` for specific code sections
    - `"src/server/routers/post.ts:createPost"` for specific functions
  - Update `_is_relevant()` to check both file AND line range/symbol match
  - For symbol matching, use simple substring matching in chunk content initially
- **Impact:** More accurate quality measurement; fewer false positives in metrics; better correlation with actual usefulness
- **Validation:** P@5 should decrease slightly (more realistic), but judge scores should correlate better with metrics

### 10. Track Metrics Over Time with Historical Reporting
- **File:** `test_hybrid_search.py:52-56`
- **Problem:** Reports overwritten each run. Empty reports suggest incomplete runs. No regression detection.
- **Fix:**
  - Modify report generation to append with timestamps: `## Run: 2026-06-12 21:05:39 UTC`
  - Add summary section showing trends: "P@5: 0.52 → 0.58 (+11.5%)"
  - Include judge scores in historical tracking
  - Add simple regression detection: flag if any metric drops >5% from previous run
- **Impact:** Visibility into regressions; data-driven improvement tracking; better debugging of performance changes
- **Validation:** Historical reports should show clear improvement trends; regressions should be immediately visible

### 11. Evaluate on Multiple Repositories
- **File:** `test_hybrid_search.py:44`
- **Problem:** All testing against one small Next.js repo (`code-test-small`). Results may not generalize.
- **Fix:**
  - Add 2-3 additional test repositories:
    - Python backend (Django/Flask with ORM)
    - TypeScript monorepo (multiple packages, complex dependencies)  
    - Java/Spring Boot application
  - Create ground truth for each new repo
  - Run same evaluation suite across all repos
  - Report aggregate metrics across repos
- **Impact:** Confidence that improvements aren't overfit to one codebase; better generalization testing
- **Validation:** Improvements should show consistent gains across different language ecosystems and codebase sizes

## Implementation Roadmap

### Phase 1: Foundation (Week 1)
- [ ] Add BM25 sparse retrieval with RRF fusion (Item #1)
- [ ] Implement MRR/NDCG metrics (Item #8)  
- [ ] Create historical reporting infrastructure (Item #10)

### Phase 2: Core Search Improvements (Week 2-3)
- [ ] Add query-aware edge weights (Item #2)
- [ ] Implement cross-source re-ranking (Item #3)
- [ ] Add confidence_score weighting in BFS (Item #4)

### Phase 3: Refinements & Evaluation (Week 4)
- [ ] Implement community-aware boosting (Item #5)
- [ ] Add distance estimates for graph results (Item #6)
- [ ] Implement chunk deduplication (Item #7)
- [ ] Extend ground truth to line ranges/symbols (Item #9)

### Phase 4: Generalization (Week 5+)
- [ ] Add 2-3 additional test repositories (Item #11)
- [ ] Run full evaluation suite across all repos
- [ ] Iterate based on cross-repo performance data

**Success Criteria:**
- Aggregate P@5 ≥ 0.60 (from current 0.50 baseline)
- Aggregate MRR ≥ 0.70 (new metric)
- Judge scores ≥ 75/100 for all queries (from current 60 baseline)
- All improvements validated across multiple repository types