# E2E Tests

## Running

```bash
# conftest.py auto-loads e2e/.env.e2e, so --env-file is optional.
uv run -- python -m pytest e2e/

# Explicit env-file (useful for layered overrides, see below).
uv run --env-file e2e/.env.e2e -- python -m pytest e2e/

# Run a single suite.
uv run -- python -m pytest e2e/test_embeddings.py
uv run -- python -m pytest e2e/test_graph/test_hybrid_search.py -v

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
| `EMBEDDING_PROVIDER` | `docker` | Embedding backend (enables DMR check) |
| `EMBEDDING_MODEL` | `ai/qwen3-embedding:4B` | Embedding model name |
| `DOCKER_MODEL_RUNNER_URL` | `http://localhost:12434/engines/v1` | DMR API base URL |
| `CHROMA_HOST` / `CHROMA_PORT` | `localhost` / `8018` | ChromaDB address |

## Service Health Checks

Test setup (inside fixtures, or at module level in `test_embeddings.py`)
calls helpers from `check_services.py` to verify that required services are
reachable:

- **Docker Model Runner** (when `EMBEDDING_PROVIDER=docker`) — pings `DOCKER_MODEL_RUNNER_URL/engines/v1/models`
- **Ollama** (when `EMBEDDING_PROVIDER=ollama`) — pings `OLLAMA_BASE_URL/api/tags`
- **ChromaDB** — pings `CHROMA_HOST:CHROMA_PORT/api/v2/heartbeat`

If a required service is unreachable the affected tests fail fast with
instructions on how to start it.

### Starting services

```bash
# ChromaDB + Redis + embedding model (all via Docker Compose)
in infra folder: 'docker compose up'

# Docker Model Runner (ensure enabled with TCP access)
docker desktop enable model-runner --tcp 12434
```

## Test Suites

| Path | What it tests | Services required |
| --- | --- | --- |
| `test_embeddings.py` | Embedding generation + ChromaDB lifecycle: store, search by text/vector, ranking, delete. | DMR + ChromaDB |
| `test_graph/test_graph_gen.py` | Pipeline on `test_graph/code-test-small/`: discover → extract → build → cluster. Verifies nodes/edges/communities and graph + chunk-map save/load round-trips. | **None** (pure graph building) |
| `test_graph/test_hybrid_search.py` | Hybrid search (vector + graph traversal): smoke tests verifying real data is returned (response shape, vector hits, required result fields, relevance sort, graph expansion). | DMR + ChromaDB |
| `api_cli_integration/test_cli_api_integration.py` | CLI commands through the real FastAPI server with `CODEKNOW_STUB=1` (`StubMiddleware` returns canned JSON). | **None** (fully stubbed, no network) |

> Both `test_graph/` suites use module-scoped pytest fixtures with `yield`
> teardown. Setup (pipeline build, embeddings, Chroma collection) runs lazily
> on first request, and the Chroma collection is dropped in fixture teardown.
