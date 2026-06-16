# Fastify agentic-search eval

An evaluation that measures whether CodeKnow's **hybrid search** (vector + graph) helps an agent answer code questions more accurately than plain **ripgrep**. Both agents are identical except for their single search tool — same LLM, same system prompt — so any score difference is attributable to the tool.

The target codebase is Fastify (`./fastify-main`). Ten natural-language questions are run through each agent, then a 3-stage LLM judge scores grounding, faithfulness, and pairwise preference.

## Layout

| Path | Purpose |
|---|---|
| `eval_fastify.py` | Main eval: defines the 10 task items, builds the two agents, runs them in parallel, invokes the judge, writes the report |
| `build_fastify_graph.py` | Builds (or reuses) the Fastify knowledge graph + Chroma embeddings index |
| `fastify_eval_support.py` | Shared paths, `.env` loading, Chroma store factory, and index-health checks |
| `test_fastify_eval_integration.py` | Unit tests (index health, tool wiring, output formatting) |
| `.env.example` | Eval-local env defaults |
| `fastify-main/` | The repo under test |
| `results/fastify/` | Outputs: `runs.jsonl` + `profile.md` |

## Prerequisites

- **ChromaDB** running and reachable (host/port in `.env`).
- An **embedding provider** for indexing — one of:
  - Docker Model Runner (`EMBEDDING_PROVIDER=docker`)
  - Ollama (`EMBEDDING_PROVIDER=ollama`)
  - OpenRouter (`EMBEDDING_PROVIDER=openrouter`)
- A **judge LLM endpoint** (OpenAI-compatible) for both the agents and the judge. Defaults to DeepSeek; see `JUDGE_LLM_*` below.

## Quick start

```bash
# 1. Configure env (fill in JUDGE_LLM_API_KEY and any provider overrides)
cp .env.example .env

# 2. Build the index (reused on later runs; rebuild with FORCE_REINDEX=1)
uv run python evals/build_fastify_graph.py

# 3. Run the eval (agents + judge + report)
uv run python evals/eval_fastify.py
```

A fast sanity check before the full run:

```bash
SMOKE=1 uv run python evals/eval_fastify.py   # runs the first item only
```

## How it works

### The two agents

- **hybrid** — calls CodeKnow's `GraphSearcher` (vector similarity expanded by the knowledge graph) with `top_k=10`.
- **grep** — calls LangChain's `FilesystemFileSearchMiddleware` (ripgrep) over the raw source tree.

Both are built with `langchain.agents.create_agent`, share `AGENT_SYSTEM_PROMPT`, and are capped at a tool-call budget via `ToolCallLimitMiddleware(exit_behavior="continue")` — on the (N+1)th attempt the agent is told to stop searching and answer, so `invoke()` returns normally instead of raising `GraphRecursionError`.

### The 10 task items

Defined in `SEARCH_ITEMS` (`eval_fastify.py`), each tagged with:

- **type** — `locate`, `reasoning`, `aggregation`, or `trap` (the trap item is designed to catch fabrication).
- **stratum** — `single-hop` or `multi-hop`.
- **difficulty** — `easy`, `medium`, or `hard`.

### The runner

`run_all` dispatches every `(item, tool, seed)` combination in parallel (`MAX_CONCURRENCY=8`) via a thread pool and streams each finished run as a JSONL row to `results/fastify/runs.jsonl`. A `CostCallback` accumulates token usage and search-call counts per run.

If an agent ends with an empty final answer, its gathered tool outputs are fed back through `_synthesize_answer` so the run still yields a grounded, non-empty answer rather than being discarded.

### Index health

`assert_prebuilt_index_ready` runs before any LLM call and fails fast if the index is missing or incomplete. It requires:

- `graph.json` + `chunk_map.json` present in `.cache/fastify-graph/`
- at least `MIN_EXPECTED_CHUNKS` (200) chunks, in both the chunk map and Chroma
- the five `REQUIRED_INDEX_FILES` (core Fastify `lib/*.js` files) indexed
- at least one `.js` and one `.ts` file in the map

## The 3-stage judge

Run by `evalkit.LLMJudge` (see [`../evalkit/`](../evalkit/) for internals):

| Stage | What it does |
|---|---|
| **Stage 0** | Deterministic: resolves each cited `file:line` against the repo, extracts snippets, computes an existence rate |
| **Stage 1** | LLM scores each run on grounding (/5) and faithfulness (/5); emits ungrounded claims and hallucinated paths. Hallucinations are reconciled against Stage 0's existence verdict |
| **Stage 2** | Pairwise double-swap preference across tools per task — picks a `winner` (`hybrid` / `grep` / `Tie`) with a confidence level |
| **Stage 3** | Consistency across seeds (only meaningful when `EVAL_SEEDS >= 2`) |

Results are aggregated by `evalkit.judge.aggregate.build_profile` into a per-tool profile with preference win-rate, Wilson 95% CI, median cost, and a length-bias check.

## Outputs

- **`results/fastify/runs.jsonl`** — one `AgentRun` row per `(item, tool, seed)`, including the final answer, extracted citations, and cost counters.
- **`results/fastify/profile.md`** — the human-readable report: per-tool profile table, pairwise winners, per-task detail (grounding/faithfulness/existence + ungrounded claims / hallucinated paths), and the bias & significance section.

## Environment variables

All read via `evals/.env` (use `os.environ.setdefault`, so shell values win). The key ones:

| Variable | Default | Effect |
|---|---|---|
| `SMOKE` / `SMOKE_TEST` | off | Run the first task item only |
| `EVAL_SEEDS` | `1` | Seeds per `(item, tool)`; set `3` for the real eval so pairwise double-swap and consistency stages are meaningful |
| `AGENT_MAX_ITERATIONS` | `6` | Hard cap on search-tool calls per run |
| `EVAL_AGENT_MODEL` | judge model | Run agents on a cheaper/faster model while the judge keeps its configured model |
| `FORCE_REINDEX` | off | Rebuild the graph + embeddings even if the index is healthy |
| `SKIP_INDEX_SANITY` | off | Skip the pre-run index sanity check |
| `EMBEDDING_PROVIDER` | `docker` | `docker` / `ollama` / `openrouter` |
| `EMBEDDING_MODEL` | `ai/qwen3-embedding:4B` | Embedding model id |
| `DOCKER_MODEL_RUNNER_URL` | `http://localhost:12434/engines/v1` | Docker Model Runner endpoint |
| `CHROMA_HOST` / `CHROMA_PORT` | `localhost` / `8018` | ChromaDB endpoint |
| `JUDGE_LLM_MODEL` | `deepseek-v4-pro` | Model for both agents and the judge |
| `JUDGE_LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible endpoint |
| `JUDGE_LLM_API_KEY` | — | Required API key |

## Tests

```bash
uv run pytest evals/test_fastify_eval_integration.py
```

Covers index-health detection, the rebuild preflight (services checked before any cache is dropped), the grep tool wiring, and hybrid result formatting.
