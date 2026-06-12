# Plan 19: Hybrid Search vs Agent-Grep Baseline — Judge Comparison Test

## Goal

Add a single e2e test that proves our hybrid search (vector + graph) beats an intelligent agent that uses grep as its tool, as judged by the LLM judge.

## Test: `test_judge_hybrid_vs_agent_grep_baseline`

### Flow

1. **Pick one query** — `"how does user authentication work"`
2. **Baseline (agent-grep)**:
   - Agent intelligently processes query to extract relevant keywords and patterns
   - Agent uses `rg` with contextual understanding to search the `code-test-small` folder
   - Agent ranks and filters results based on relevance
   - Collect top 10 matching snippets (file + surrounding lines)
   - Format as `JudgeInput` (all as `semantic_hits`, no `graph_hits`)
   - Run `LLMJudge().judge()` → `baseline_score`
3. **Our system (hybrid search)**:
   - `_search(query, n_results=10, traversal_depth=3)`
   - Format via `from_hybrid_response()` → run judge → `hybrid_score`
4. **Compare**: print both scores side by side, assert `hybrid_score >= baseline_score`

### New helper: `_agent_grep_search(query, repo_dir, n_results=10)`

- Implements intelligent agent that uses `subprocess.run(["rg", ...])` with contextual query understanding
- Agent extracts keywords, generates patterns, and processes results intelligently
- Returns a `JudgeInput` with all hits as `semantic_hits`, empty `graph_hits`
- Snippets are the grep context lines (no embeddings, no graph)

### Placement

- New file: `e2e/graph_gen/test_hybrid_vs_agent_grep.py`
- Section 9 (Judge LLM gate) in the new test file
- Marked `@pytest.mark.llm_judge`
- Reuses existing `LLMJudge`, `_print_judge_report`, `_enforce_semantic_saturation` from `test_hybrid_search.py`
- Single test, not parametrized (search just once)

### Output example

```
======================================================================
AGENT-GREP BASELINE —  how does user authentication work
FINAL SCORE: 52.0/100  |  winner: semantic_only

HYBRID SEARCH   —  how does user authentication work
FINAL SCORE: 78.0/100  |  winner: hybrid

DELTA: +26.0 points
======================================================================
```

### Assertions

- `hybrid_score >= baseline_score` — our system should never lose to agent-grep
- Both scores >= 40 — even agent-grep should find something for a reasonable query

## Implementation Steps

1. Create new test file `e2e/graph_gen/test_hybrid_vs_agent_grep.py`
2. Add `_agent_grep_search()` helper that:
   - Implements intelligent agent with contextual query understanding
   - Extracts keywords and generates appropriate grep patterns
   - Runs `rg -C 3 -n <patterns>` on `CODE_TEST_SMALL` 
   - Parses output into `(file_path, start_line, snippet)` tuples
   - Groups by file, deduplicates, ranks by relevance, takes top N
   - Converts to `JudgeInput` with all hits as `semantic_hits`, empty `graph_hits`
3. Add `_from_grep_results()` conversion function similar to `from_hybrid_response()`
4. Add `_synthesize_grep_analysis()` to generate agent analysis for judge
5. Add `test_judge_hybrid_vs_agent_grep_baseline` test that:
   - Calls `_agent_grep_search()` → judge → baseline output
   - Calls `_search()` → `from_hybrid_response()` → judge → hybrid output
   - Applies `_enforce_semantic_saturation` on both
   - Prints comparison report
   - Asserts hybrid >= baseline and both >= 40

## Files Changed

- `e2e/graph_gen/test_hybrid_vs_agent_grep.py` — new test file with helper + test
