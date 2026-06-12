# Plan: Write test results to Markdown files

## Goal

Update `e2e/graph_gen/test_hybrid_search.py` and `e2e/graph_gen/test_hybrid_vs_agent_grep.py` so that test results are also written to `.md` files alongside the test files. Existing `print()` calls are preserved (dual output).

## Files to modify

### 1. `e2e/graph_gen/test_hybrid_search.py`

- Add `RESULT_MD = Path(__file__).parent / "test_hybrid_search_report.md"`
- Add `_write_md_report(text: str)` helper that appends to `RESULT_MD`
- At module level, clear the file and write a header (`# Hybrid Search E2E Results`)
- Modify `_print_retrieval_report()` to also emit a Markdown section with a `| K | P | R | F1 |` table
- Modify `_print_judge_report()` to also emit a Markdown section (score, subscores table, strengths/weaknesses lists, rationale blockquote)
- Add MD output to `test_retrieval_metrics_summary` for the aggregate table
- Keep all existing `print()` calls

### 2. `e2e/graph_gen/test_hybrid_vs_agent_grep.py`

- Add `RESULT_MD = Path(__file__).parent / "test_hybrid_vs_agent_grep_report.md"`
- Add `_write_md_report(text: str)` helper that appends to `RESULT_MD`
- At module level, clear file and write header (`# Hybrid vs Agent-Grep E2E Results`)
- In `test_judge_hybrid_vs_agent_grep_baseline`, write:
  - Agent-grep judge report section
  - Hybrid search judge report section
  - Comparison table (scores + delta)
- Keep all existing `print()` calls

## MD structure

### `test_hybrid_search_report.md`

1. Title + timestamp
2. Per-query sections: retrieval metrics table + judge report (if LLM judge ran)
3. Final aggregate retrieval quality table

### `test_hybrid_vs_agent_grep_report.md`

1. Title + timestamp
2. Agent-grep judge report section
3. Hybrid search judge report section
4. Comparison table (scores + delta)