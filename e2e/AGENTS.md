# E2E Tests

## Running

```bash
# conftest.py auto-loads e2e/.env.e2e, so --env-file is optional.
uv run -- python -m pytest e2e/

# Explicit env-file (useful for layered overrides, see below).
uv run --env-file e2e/.env.e2e -- python -m pytest e2e/

# Run a single suite.
uv run -- python -m pytest e2e/test_embeddings.py
uv run -- python -m pytest e2e/graph_gen/test_hybrid_search.py -v

# Quick health-check (no pytest).
uv run --env-file e2e/.env.e2e -- python e2e/check_services.py
```

> Note: use `python -m pytest`, not bare `pytest` — the console script is
> not exposed in this workspace's venv.

## Environment Configuration

Tests read environment variables from `e2e/.env.e2e` (committed safe
defaults). `e2e/conftest.py` auto-loads this file at collection time via
`os.environ.setdefault`, so the `--env-file` flag is optional.

- **Edit defaults**: modify `e2e/.env.e2e` directly
- **Local overrides** (not committed): create `e2e/.env.e2e.local` and run:
  ```bash
  uv run --env-file e2e/.env.e2e --env-file e2e/.env.e2e.local -- python -m pytest e2e/
  ```

### Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `ollama` | Embedding backend (enables Ollama check) |
| `EMBEDDING_MODEL` | `qwen3-embedding:4b` | Embedding model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API base URL |
| `CHROMA_HOST` / `CHROMA_PORT` | `localhost` / `8018` | ChromaDB address |
| `JUDGE_LLM_MODEL` | `deepseek/deepseek-v4-pro` | LLM judge model |
| `JUDGE_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible judge API base |
| `JUDGE_LLM_API_KEY` | — | Judge API key (falls back to `OPENROUTER_API_KEY`). Set in `.env.e2e.local`. |
| `E2E_TRAVERSAL_DEPTH` | `3` | Graph BFS depth used by the judge gates |

## Service Health Checks

Before tests run, `check_services.py` verifies that required services are
reachable:

- **Ollama** (when `EMBEDDING_PROVIDER=ollama`) — pings `OLLAMA_BASE_URL/api/tags`
- **ChromaDB** — pings `CHROMA_HOST:CHROMA_PORT/api/v2/heartbeat`

If either service is unreachable the test session exits immediately with
instructions on how to start it.

### Starting services

```bash
# Ollama
ollama serve

# ChromaDB
in infra folder: 'docker compose up'
```

## Test Suites

| Path | What it tests | Services required |
| --- | --- | --- |
| `test_embeddings.py` | Embedding generation + ChromaDB lifecycle: store, search by text/vector, ranking, delete. | Ollama + ChromaDB |
| `graph_gen/test_graph_gen.py` | Pipeline on `graph_gen/code-test-small/`: discover → extract → build → cluster. | Ollama + ChromaDB |
| `graph_gen/test_hybrid_search.py` | Hybrid search (vector + graph traversal): smoke tests, deterministic retrieval-metric gates (P@5, R@10, F1@10 against ground truth), and an LLM-judge quality gate. | Ollama + ChromaDB (+ LLM key for judge) |
| `graph_gen/test_hybrid_vs_agent_grep.py` | Hybrid search vs an agent-grep (ripgrep) baseline, both LLM-judged. Reuses setup from `test_hybrid_search.py`. | Ollama + ChromaDB + LLM key |
| `judge/judge.py`, `judge/schemas.py` | Shared LLM judge library (LangChain structured output) used by the graph_gen suites. | — |
| `judge/test_judge.py` | Tests the judge itself with synthetic `JudgeInput` data. | LLM key |
| `api_cli_integration/test_cli_api_integration.py` | CLI commands through the real FastAPI server with `CODEKNOW_STUB=1` (`StubMiddleware` returns canned JSON). | **None** (fully stubbed, no network) |

## Selecting / Skipping LLM-judge Tests

Judge tests carry the `llm_judge` marker (registered in `pyproject.toml`)
and are skipped automatically when no `JUDGE_LLM_API_KEY` /
`OPENROUTER_API_KEY` is set.

```bash
# Skip all LLM-judged tests (fastest, no API key needed).
uv run -- python -m pytest e2e/ -m "not llm_judge"

# Run only the judged tests.
uv run -- python -m pytest e2e/ -m llm_judge
```

## Generated Reports

Some graph_gen tests write Markdown reports (rewritten each run):

- `graph_gen/test_hybrid_search_report.md` — per-query retrieval metrics and judge scores.
- `graph_gen/test_hybrid_vs_agent_grep_report.md` — hybrid vs agent-grep judge comparison.
