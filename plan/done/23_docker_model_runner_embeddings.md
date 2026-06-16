# Plan: Switch embeddings from Ollama to Docker Model Runner

## Goal

Replace the standalone Ollama server (serving `qwen3-embedding:4b`) with
**Docker Model Runner (DMR)** so the whole stack comes up via a single
`docker compose up`. The embedding model becomes a first-class Compose
dependency declared under the `models:` top-level element.

## Decisions (locked)

- **Model**: `ai/qwen3-embedding:4B` — the exact same Q4_K_M GGUF model as the
  current Ollama `qwen3-embedding:4b`. Same 2560-dim vectors, so existing
  `infra/chroma-data/` is compatible (no re-index required).
- **Scope**: full — infra + core code + `.env` + tests + docs + e2e.

## Key facts (verified)

- DMR is installed on this machine (Apple Silicon `arm64`); Docker Compose
  v5.1.3 (well above the v2.38 requirement).
- **TCP host access on `:12434` is already enabled** — `GET /engines/v1/models`
  responds from the host. No extra enable step needed.
- DMR OpenAI-compatible base URL (for `OpenAIEmbeddings`):
  `http://localhost:12434/engines/v1` → `/embeddings`.
- **No API key required** — DMR ignores the `Authorization` header.
- Model name in requests: `ai/qwen3-embedding` (full namespace).
- Embedding models require the `--embeddings` runtime flag.
- Models load into memory lazily on first request, unload when idle.
- `ai/qwen3-embedding:4B` `latest`→4B; ~2.3 GB download, ~3.75 GiB VRAM.

## Files to modify

### A. `infra/docker-compose.yml`

Add a top-level `models:` block and bind it to `chromadb` (the embedding
consumer) so `docker compose up` pulls + provisions the model. DMR serves it
lazily on the host on first request.

```yaml
services:
  chromadb:
    image: chromadb/chroma:1.5.3
    ...existing...
    models:
      - qwen3-embedding
  redis: ...unchanged...

models:
  qwen3-embedding:
    model: ai/qwen3-embedding:4B
    context_size: 2048
    runtime_flags:
      - "--embeddings"
```

> Note: the host app (runs via `uv run`, not a compose service) reads its URL
> from `.env` (`DOCKER_MODEL_RUNNER_URL`). The compose `models:` binding's
> injected env vars go to `chromadb` and are harmless/unused; its real purpose
> here is to drive the model's pull + lifecycle from `docker compose up`.

### B. `packages/codeknow-lib/src/codeknow/vector/embeddings.py`

- `:65` — extend the `provider` Literal:
  `Literal["ollama", "openrouter", "docker"]` (active value comes from `.env`).
- Add field:
  `docker_base_url: str = Field(default="http://localhost:12434/engines/v1", alias="DOCKER_MODEL_RUNNER_URL")`.
- `resolved_base_url()` — add `docker` branch returning `docker_base_url`.
- `resolved_api_key()` — `docker` returns `"not-needed"` (DMR ignores header).
- `create_embeddings()` `:106` — apply `check_embedding_ctx_length=False` for
  both `ollama` and `docker`.

### C. `packages/codeknow-lib/src/codeknow/pipeline/config.py`

- `:35` — `embed_provider: Literal["ollama", "openrouter", "docker"]`.
- `:36` — `embed_model: str = "ai/qwen3-embedding"`.

### D. `packages/codeknow-lib/src/codeknow/service_checks.py`

- Add `check_docker_model_runner(base_url=None)` → `GET {base}/engines/v1/models`,
  returns `ServiceStatus`. Default base `http://localhost:12434` derived from env
  `DOCKER_MODEL_RUNNER_URL` (strip any trailing `/engines/v1`).
  - Rationale: the existing `check_ollama()` strips `/v1` or `/v2` then appends
    `/api/tags`, which would corrupt the DMR `/engines/v1` path — so a dedicated
    DMR check is needed rather than reusing `check_ollama`.
- Export `check_docker_model_runner` from `src/codeknow/__init__.py` (alongside
  `check_ollama`).

### E. Config files

- `.env`, `.env.example`, `e2e/.env.e2e`:
  ```
  EMBEDDING_PROVIDER=docker
  EMBEDDING_MODEL=ai/qwen3-embedding
  DOCKER_MODEL_RUNNER_URL=http://localhost:12434/engines/v1
  ```
- `.env.example` — fix the stale `# ollama default: nomic-embed-text` comment →
  `# Docker Model Runner default: ai/qwen3-embedding`; comment out the legacy
  `OLLAMA_*` lines (kept as reference).

### F. `infra/install-ollama-and-qwen3-embedding.sh` → replaced

Delete the Ollama script; add `infra/setup-embedding-model.sh` that:
1. Verifies DMR is reachable on `http://localhost:12434` (TCP host access).
2. Runs `docker model pull ai/qwen3-embedding:4B` if not already present.
3. Smoke-tests the endpoint:
   `curl http://localhost:12434/engines/v1/embeddings -d '{"model":"ai/qwen3-embedding","input":"test"}'`.

### G. Tests

- `packages/codeknow-lib/tests/test_embed_stage.py:120-126` — update provider
  assertion to `"docker"`, model to `"ai/qwen3-embedding"`.
- `packages/codeknow-lib/tests/test_service_checks.py` — add a test for
  `check_docker_model_runner()` (mock `/engines/v1/models`).

### H. Docs

- `docs/infra-setup.md:7,52-72,96-100,121,139-145` — replace the "Install Ollama
  + model" section with DMR setup (`docker model pull ai/qwen3-embedding:4B`,
  endpoint `http://localhost:12434/engines/v1`, `docker compose up` provisions it).
- `e2e/AGENTS.md:39-41,52-53,63,73` — update env table + service-check section
  ("Ollama when provider=ollama" → "DMR when provider=docker").
- `e2e/check_services.py:50-56` — call `check_docker_model_runner()` when
  `EMBEDDING_PROVIDER == "docker"`.
- `.vscode/settings.json:16` — add `"docker"`, `"modelrunner"` to the
  spell-check dictionary.

## Notes

- **No data loss**: `ai/qwen3-embedding:4B` is dimensionally identical to the
  current model (2560-dim), so `infra/chroma-data/` keeps working. Vectors may
  differ slightly at the byte level (llama.cpp vs Ollama's gguf handler); a
  re-embed is optional if retrieval quality ever needs a refresh.
- **First run**: model pulls on first `docker compose up` / first embeddings
  request (~2.3 GB), then caches locally. `docker model ps` shows it loaded.

## Verification

After changes:

1. Static checks: `uv run project-scripts.py dev-check`
2. Tests: `uv run pytest`
3. Live smoke test:
   ```
   curl http://localhost:12434/engines/v1/embeddings \
     -H "Content-Type: application/json" \
     -d '{"model":"ai/qwen3-embedding","input":"A dog is an animal"}'
   ```
4. End-to-end: run the pipeline / search against the existing chroma-data and
   confirm embeddings + retrieval work unchanged.
