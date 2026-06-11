# Plan 19: Hybrid Search vs Grep Baseline — Judge Comparison Test

## Goal

Add a single e2e test that proves our hybrid search (vector + graph) beats a naive grep baseline, as judged by the LLM judge.

## Test: `test_judge_hybrid_vs_grep_baseline`

### Flow

1. **Pick one query** — `"how does user authentication work"`
2. **Baseline (grep)**:
   - Split query into keywords
   - `rg` the `code-test-small` folder for those keywords
   - Collect top 10 matching snippets (file + surrounding lines)
   - Format as `JudgeInput` (all as `semantic_hits`, no `graph_hits`)
   - Run `LLMJudge().judge()` → `baseline_score`
3. **Our system (hybrid search)**:
   - `_search(query, n_results=10, traversal_depth=3)`
   - Format via `from_hybrid_response()` → run judge → `hybrid_score`
4. **Compare**: print both scores side by side, assert `hybrid_score >= baseline_score`

### New helper: `_grep_baseline_search(query, repo_dir, n_results=10)`

- Uses `subprocess.run(["rg", ...])` to keyword-search the repo
- Returns a `JudgeInput` with all hits as `semantic_hits`, empty `graph_hits`
- Snippets are the grep context lines (no embeddings, no graph)

### Placement

- Section 9 (Judge LLM gate) in `e2e/graph_gen/test_hybrid_search.py`
- Marked `@pytest.mark.llm_judge`
- Uses the existing `LLMJudge`, `_print_judge_report`, `_enforce_semantic_saturation`
- Single test, not parametrized (search just once)

### Output example

```
======================================================================
BASELINE (grep)  —  how does user authentication work
FINAL SCORE: 52.0/100  |  winner: semantic_only

HYBRID SEARCH   —  how does user authentication work
FINAL SCORE: 78.0/100  |  winner: hybrid

DELTA: +26.0 points
======================================================================
```

### Assertions

- `hybrid_score >= baseline_score` — our system should never lose to raw grep
- Both scores >= 40 — even grep should find something for a reasonable query

## Implementation Steps

1. Add `_grep_baseline_search()` helper that:
   - Splits query into keywords (filter stop words)
   - Runs `rg -C 3 -n <keywords>` on `CODE_TEST_SMALL`
   - Parses output into `(file_path, start_line, snippet)` tuples
   - Groups by file, deduplicates, takes top N
   - Converts to `JudgeInput` with all hits as `semantic_hits`, empty `graph_hits`

2. Add `test_judge_hybrid_vs_grep_baseline` test that:
   - Calls `_grep_baseline_search()` → judge → baseline output
   - Calls `_search()` → `from_hybrid_response()` → judge → hybrid output
   - Applies `_enforce_semantic_saturation` on both
   - Prints comparison report
   - Asserts hybrid >= baseline and both >= 40

## Files Changed

- `e2e/graph_gen/test_hybrid_search.py` — add helper + test
