# E2E Tests

## Running

```bash
# Load e2e env vars only (recommended)
uv run --env-file e2e/.env.e2e -- pytest e2e/

# Layer root .env with e2e overrides
uv run --env-file .env --env-file e2e/.env.e2e -- pytest e2e/

# Quick health-check (no pytest)
uv run --env-file e2e/.env.e2e -- python e2e/check_services.py
```

## Environment Configuration

Tests read environment variables from `e2e/.env.e2e` (committed safe defaults).

- **Edit defaults**: modify `e2e/.env.e2e` directly
- **Local overrides** (not committed): create `e2e/.env.e2e.local` and run:
  ```bash
  uv run --env-file e2e/.env.e2e --env-file e2e/.env.e2e.local -- pytest e2e/
  ```

See `e2e/.env.example` for all available variables.

## Service Health Checks

Before tests run, `check_services.py` verifies that required services are reachable:

- **Ollama** (when `EMBEDDING_PROVIDER=ollama`) — pings `OLLAMA_BASE_URL/api/tags`
- **ChromaDB** — pings `CHROMA_HOST:CHROMA_PORT/api/v2/heartbeat`

If either service is unreachable the test session exits immediately with instructions on how to start it.

### Starting services

```bash
# Ollama
ollama serve

# ChromaDB
in infra folder: 'docker compose up'
```
