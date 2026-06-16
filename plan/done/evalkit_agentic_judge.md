# Plan: evalkit — two agentic-search agents + 3-stage reference-free judge

## Goal

Refactor `evals/eval_word_press.py` so it compares **two agents** (same model,
different search tool) instead of pure hybrid retrieval vs a 2-turn agent-grep.
Build the 3-stage reference-free judge described in
`evals/JUDGE_PRINCIPLES.md` (grounding / faithfulness / consistency /
preference — no gold answers, no headline blend).

## Context (verified)

- `evals/eval_word_press.py` is **currently broken**: it imports from a `judge`
  module that was deleted in the working tree (uncommitted):
  `e2e/judge/` (`schemas.py`, `judge.py`, `__init__.py`, `test_judge.py`) and
  `e2e/test_graph/test_hybrid_vs_agent_grep.py`.
- The old `judge` was **single-stage** (one LLM call → blended `final_score`
  /100 + 5 subscales). The old "agent-grep" was a fixed **2-turn** pipeline
  (LLM generates `rg` commands → LLM ranks hits) and compared that against
  **pure hybrid retrieval** (no agent). Neither matches the spec.
- `JUDGE_PRINCIPLES.md` §10 explicitly lists the migration from old → new.
- Deps available (verified): `langchain-openai` (`ChatOpenAI`,
  OpenRouter-compatible), `langsmith` (transitively), `numpy 1.26.4`,
  `openai` SDK, `pydantic` v2. **Not** available: `scipy` (deferred).

## Decisions (locked)

- **Agent loop**: bounded ReAct, cap **3–4 search calls**. Both agents share
  the SAME loop; only the tool differs (`HybridSearchTool` vs `GrepTool`).
  This is what makes it a real "agentic eval" and yields meaningful
  `search_calls`, `llm_turns`, `steps_to_first_relevant`, `wall_clock_s`.
- **LLM calls**: direct `ChatOpenAI` → parse JSON → local JSONL + MD report.
  No LangSmith code (deferred — it is installed but unused in this cut).
- **Scope (first cut)**: **1 seed** per task/tool (Stage 3 consistency stays
  implemented but reports N/A when N<2). **No trap tasks** yet. **No scipy**:
  hand-roll Wilson CI + Spearman length-bias; McNemar/Wilcoxon/bootstrap left
  as TODO stubs.
- **Placement**: new top-level workspace package **`evalkit/`**.

## Status (updated)

| Area | State |
|---|---|
| `evalkit/` package scaffold + workspace wiring | ✅ done |
| `schemas.py` (Task, Cost, AgentRun, JudgeOutput, PairwiseJudgment) | ✅ done |
| `citations.py` (file:line extraction) | ✅ done |
| Judge Stage 0 (existence + snippets + Jaccard) | ✅ done |
| Judge Stage 1 (grounding + faithfulness, stubbed LLM) | ✅ done |
| Judge Stage 2 (pairwise double-swap, stubbed LLM) | ✅ done |
| Judge Stage 3 (consistency, stubbed LLM + embeddings) | ✅ done |
| `aggregate.py` (Wilson CI, Spearman, verbosity guard, profile) | ✅ done |
| `llm.py` (`parse_json_block`, `call_llm_json`, `make_llm_callable`, `JUDGE_LLM_` env prefix) | ✅ done |
| `judge.py` (`LLMJudge.judge_run` / `judge_all` orchestrator) | ✅ done |
| Agents (LangChain `create_agent` + tools + `CostCallback`) — **in the eval harness** | ✅ done |
| Refactor `evals/eval_word_press.py` to use evalkit | ✅ done |
| `llm_judge`-gated integration round-trip (real OpenRouter call) | ⏳ deferred |

**Quality gates:** 220 unit tests passing (no API key needed — all stage logic
uses stubbed `llm`/`embed_fn` callables), ruff clean, pyrefly 0 errors.

**Pivot from plan:** agents live in `evals/eval_word_press.py` (LangChain
`create_agent` + `@tool` + `CostCallback`), NOT in `evalkit/agents/`. evalkit
stays a pure judge library; the harness owns the agents. `evalkit/agents/` was
dropped from the architecture. The judge consumes `AgentRun` objects regardless
of source, so the separation is clean.

## Architecture — new package `evalkit/`

```
evalkit/
  pyproject.toml
  src/evalkit/
    __init__.py            # ✅ package docstring
    schemas.py             # ✅ Task, Cost, AgentRun, JudgeOutput, PairwiseJudgment
    llm.py                 # ✅ parse_json_block + call_llm_json + JudgeLLMConfig (JUDGE_LLM_ prefix)
    citations.py           # ✅ extract file:line from final_answer (regex)
    judge/
      prompts.py           # ✅ STAGE1/STAGE2/consistency prompts (from §6)
      stage0.py            # ✅ Stage0Result + citation_jaccard + verify_existence
                           #    + extract_snippet + stage0 orchestrator (pathlib handles abs+rel)
      stage1.py            # ✅ format_cited_code + stage1 (+ score clamping)
      stage2.py            # ✅ resolve_pairwise (double-swap) + stage2
      stage3.py            # ✅ cosine_sim + stage3 (LLM subset + embeddings; None N<2)
      aggregate.py         # ✅ wilson_ci + spearman_corr + verbosity_guard + build_profile
      judge.py             # ✅ LLMJudge.judge_run / judge_all + _majority
```
**Harness** (`evals/eval_word_press.py`):
```
evals/eval_word_press.py   # ✅ 10 Task objects (including wp-10 trap),
                           #    ensure_graph_indexed (cached graph + Chroma),
                           #    _build_tools (shared GraphSearcher + grep),
                           #    _make_chat_model (JUDGE_LLM_ config),
                           #    make_agent (LangChain create_agent),
                           #    CostCallback (tokens+search_calls capture),
                           #    run_one (agent invoke + AgentRun),
                           #    run_all (ThreadPool, parallel, JSONL stream),
                           #    print_summary (per-tool cost table),
                           #    write_report (profile MD),
                           #    judge_and_report (LLMJudge + build_profile wiring)
```

Wiring (root `pyproject.toml`): add `evalkit` to `[tool.uv.workspace] members`,
to `[tool.hatch.build.targets.editable] dev-mode-dirs`, and to root
`[project] dependencies` so `uv run python evals/...` imports it.
**No new third-party deps.**

## Data contract (`schemas.py`) — pydantic (codebase convention)

Mirrors `JUDGE_PRINCIPLES.md` §5:

- `Task {task_id, type, stratum, difficulty, prompt, trap}`
- `Cost {search_calls, llm_turns, tokens_in, tokens_out, wall_clock_s, steps_to_first_relevant}`
- `AgentRun {task_id, tool, seed, final_answer, cited_locations: list[str], cost: Cost}`
- `JudgeOutput {task_id, tool, seed, grounding(0-5), existence_rate(0-1), faithfulness(0-5), ungrounded_claims[], hallucinated_paths[], consistency_vs_other_seeds: float|None}` — consistency is `Optional` so a single seed reports honestly as **N/A** (not 0, not 1)
- `PairwiseJudgment {task_id, winner: hybrid|grep|Tie, confidence: high|medium|low, reasoning}`

## Agents (``evals/eval_word_press.py``, **not** in evalkit)

- **Tool interface**: `@tool` callables (LangChain) — `hybrid_search` wraps
  a shared `GraphSearcher` (built once with `store=` injected, reuse across
  all calls), `grep_search` runs `rg -C3 -n` with auto-derived patterns.
- **Loop**: LangChain `create_agent` (native tool-calling, recursion_limit=24).
  Cap is ~8 agent turns. Same LLM as judge (`JudgeLLMConfig`, deepseek-v4-pro).
- **System prompt** mandates citing sources as `path:line` so Stage 0 has
  real citations to verify.
- **Cost** captured via `CostCallback(BaseCallbackHandler)`: `llm_turns`,
  `tokens_in/out`, `search_calls`, `wall_clock_s`.
- Citations extracted via `evalkit.citations.extract_citations` (unified with
  the judge).

## Judge — 4 stages (cheap → expensive)

- **Stage 0 — deterministic (free)**: for each cited `file:line`, check
  existence in `repo_root`, extract ±5 lines; compute `existence_rate`; build
  snippets map fed to Stages 1 & 2; citation-set Jaccard across seeds.
- **Stage 1 — LLM per run**: one call scores grounding + faithfulness together
  (shared context), emits `ungrounded_claims[]`, `hallucinated_paths[]`.
- **Stage 2 — pairwise LLM, double-swapped**: run only on cross-tool pairs;
  two orderings (AB + BA); disagreement → `Tie` + `confidence: low`.
- **Stage 3 — consistency**: LLM-judge seed-pair semantic equivalence on a
  subset + embeddings cosine for the rest → agreement %. With 1 seed → N/A.
- **`aggregate.py`**: per-tool profile (grounding_mean, faithfulness_mean,
  consistency_pct, preference win-rate + hand-rolled Wilson CI, median cost).
  **No headline blend** (spec §7). McNemar/Wilcoxon = TODO stubs (no scipy).

## Refactor `evals/eval_word_press.py`

- Convert `_QUERIES` → `Task` objects (T-001..T-010).
- Pipeline + Chroma build in `main()` unchanged.
- Import becomes `from evalkit import ...` (drop the `judge` shim; keep e2e
  shim only for `check_services`).
- Loop: tasks × {hybrid, grep} × 1 seed → `AgentRun[]` →
  `LLMJudge.judge_all()` → write:
  - MD report: per-tool profile, pairwise winners, per-query detail, cost
    table.
  - Raw `evals/eval_word_press_runs.jsonl` for later analysis.
- Timing now comes from `Cost.wall_clock_s` (full agent run) — directly
  comparable across both tools (unlike today's split metric).

## Tests (TDD, judge complete — 60 unit tests)

Built strict red-green-blue. All stage logic tested with **stubbed**
`llm: Callable[[str], dict]` and `embed_fn` (dependency injection), so the
suite runs with no API key.

- `test_citations.py` (5) — regex, dedup, order, backticks, ranges.
- `test_schemas.py` (4) — Field validation, defaults, Literal constraints.
- `test_stage0.py` (14) — Jaccard, existence, snippet clamping, `stage0`.
- `test_stage1.py` (3) — `format_cited_code` (FILE NOT FOUND marker), JSON→output, score clamping.
- `test_stage2.py` (6) — `resolve_pairwise` (agree/disagree/tie), double-swap ordering.
- `test_stage3.py` (6) — `cosine_sim`, single-run None, LLM-vs-embed split.
- `test_aggregate.py` (13) — Wilson CI, Spearman (+ ties), verbosity guard, `build_profile`.
- `test_llm.py` (6) — `parse_json_block` (raw/fenced/prose/malformed).
- `test_judge.py` (3) — `LLMJudge.judge_run`, `judge_all` (single-seed + two-seed consistency).

**LLM-gated** (`@pytest.mark.llm_judge`, skip without key): stage1/stage2 JSON
round-trip on a real OpenRouter call + one full agent run on `code-test-small`
— deferred to next session (needs `OPENROUTER_API_KEY`, already in `e2e/.env.e2e.local`).

## Risks (flag during build)

- ✅ Resolved: JSON tolerance — `parse_json_block` handles fenced/` ```json `/bare/prose-wrapped; `call_llm_json` requests `response_format=json_object` then falls back to tolerant extraction.
- ✅ Resolved: single-seed consistency reports **N/A** (`Optional[float]=None`), filtered in `build_profile`.
- ✅ Resolved: pytest duplicate-package collision (`tests/__init__.py` in two trees) — evalkit tests collect rootless (no `__init__.py`).
- ✅ Resolved: `JudgeLLMConfig` now has `env_prefix="JUDGE_LLM_"` — the documented `JUDGE_LLM_MODEL`/`JUDGE_LLM_BASE_URL` env overrides in `e2e/.env.e2e` are actually honoured (they were silently ignored before).
- ✅ Resolved: path-format mismatch — pathlib's `repo_root / "/abs/path"` discards the left operand, so `verify_existence` handles both relative (grep) and absolute (hybrid) citations correctly. Regression test locks this in.
- ✅ Resolved: `GraphSearcher` per-call construction → shared singleton with `store=` injected; ~60 graph reloads eliminated.
- ✅ Resolved: citation extraction unified under `evalkit.citations.extract_citations` (removed local `_CITATION_RE`/`_extract_citations` duplication).
- ✅ Resolved: `steps_to_first_relevant` dropped from `Cost` schema, `build_profile` cost block, and all constructions (re-add trivially when the callback can detect first-relevant).
- pydantic v2 requires keyword args at construction (positional `BaseModel(...)` fails) — keep constructors keyword-only.

## Out of scope (follow-up)

- 3 seeds (unlocks Stage 3 + cross-seed pairwise).
- 10–15 trap tasks (calibration gate per §8).
- scipy stats: McNemar / paired Wilcoxon / bootstrap CI (currently None stubs in `build_profile["stats"]`).
- LangSmith tracing dashboard.
