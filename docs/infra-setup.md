# Infrastructure setup

CodeKnow relies on three local services:

- **ChromaDB** — vector store for embeddings (run via Docker Compose)
- **Redis** — search response cache (run via Docker Compose)
- **Docker Model Runner (DMR)** — local embedding model server (`ai/qwen3-embedding`)

This guide brings all three up and connects the app to them. All infra lives under [`infra/`](../infra/).

---

## Prerequisites

- **[Docker](https://docs.docker.com/get-docker/)** with the Compose plugin (for ChromaDB + Redis + DMR)
- **Docker Model Runner** enabled — run `docker desktop enable model-runner --tcp 12434` once
- **ripgrep** — required for e2e tests (see below)

---

## 1. Start the full stack (ChromaDB + Redis + API + model)

All services run from a single Compose file:

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

`--build` is only needed the first time (it builds the `codeknow-api` image). This starts:

| Service | Image | Host port | Persistent data |
|---|---|---|---|
| ChromaDB | `chromadb/chroma:1.5.3` | `8018` → container `8000` | `infra/chroma-data/` |
| Redis | `redis:7-alpine` | `6379` | append-only in-container |
| CodeKnow API | `codeknow-api` (built locally) | `8080` | `infra/api-data/` |

On first start, the top-level `models:` block in `infra/docker-compose.yml` also provisions the `ai/qwen3-embedding:4B` embedding model via Docker Model Runner — so step 2 below is optional when you bring the stack up this way.

**How the containers reach each other:** the `api` service talks to its siblings by compose hostname on the *internal* ports (`chromadb:8000`, `redis:6379`), and reaches the host's Docker Model Runner via `host.docker.internal:12434` (enabled by the `extra_hosts` block). The CLI then talks to the API on the host-published port `localhost:8080`.

ChromaDB is configured by [`infra/chroma.local.yaml`](../infra/chroma.local.yaml) (persist path `/data`, listen on `127.0.0.1:8000`), mounted read-only into the container. Redis runs with `appendonly` and a 256 MB LRU memory cap. The API image is built from [`packages/codeknow-api/Dockerfile`](../packages/codeknow-api/Dockerfile).

Check the stack:

```bash
docker compose -f infra/docker-compose.yml ps
```

Stop it:

```bash
docker compose -f infra/docker-compose.yml down
```

> **Two ways to run the API.** This step runs `codeknow-api` *inside* a container as part of the stack (the CLI's default target). You can instead run it as a local process by opting into daemon mode with `CODEKNOW_DAEMON=1` — see [usage.md](usage.md). Both modes share the same ChromaDB / Redis / model services.

---

## 2. Pull the embedding model via Docker Model Runner

The [`infra/setup-embedding-model.sh`](../infra/setup-embedding-model.sh) script verifies DMR is reachable and pulls the embedding model:

```bash
bash infra/setup-embedding-model.sh
```

This will:

1. Verify Docker Model Runner is reachable on `http://localhost:12434`.
2. Pull the `ai/qwen3-embedding:4B` model (if not already present).
3. Smoke-test the embeddings endpoint.

DMR listens on `http://localhost:12434`. Verify the model is available:

```bash
docker model list
```

You should see `ai/qwen3-embedding:4B` in the list. The model is also declared in `infra/docker-compose.yml` under the `models:` block, so `docker compose up` will provision it automatically.

---

## 3. Install ripgrep (for e2e tests)

ripgrep is required by the e2e test suite. Install it with the provided script (macOS/Homebrew):

```bash
bash infra/ripGrep.sh
```

Verify:

```bash
rg --version
```

---

## Connect the app

How you connect the CLI depends on whether the API runs in a container or as a local process.

### API in Docker (full-stack mode, default)

If you started the whole stack in step 1, the API is already running on `localhost:8080` — which is exactly where the CLI points **by default**, so nothing to configure:

```bash
codeknow info   # should report "API: http://localhost:8080 (remote)"
```

No `CODEKNOW_API_URL` is needed; the CLI is in remote mode by default. (Set it only if the API isn't on `localhost:8080`.)

### API as a local process (daemon mode)

If you'd rather run the API as a host process that the CLI manages, opt into daemon mode and copy `.env.example` to `.env` (the defaults match the host ports above):

```bash
export CODEKNOW_DAEMON=1   # expose and enable the `daemon` subcommand
```

```bash
# Embeddings (Docker Model Runner)
EMBEDDING_PROVIDER=docker
EMBEDDING_MODEL=ai/qwen3-embedding
# DOCKER_MODEL_RUNNER_URL=http://localhost:12434/engines/v1

# ChromaDB
# CHROMA_HOST=localhost
# CHROMA_PORT=8018

# Redis cache
# CODEKNOW_REDIS_URL=redis://localhost:6379/0
```

All three connection values are commented out because the defaults already match the `infra/` setup — uncomment and edit only if you changed ports or hosts.

---

## Quick start (all services)

**Option A — full stack in Docker (simplest, no uv/Python needed on the host):**

```bash
# 1. ChromaDB + Redis + API + embedding model (provisioned automatically)
docker compose -f infra/docker-compose.yml up -d --build

# 2. The CLI targets localhost:8080 by default — just use it
codeknow add git@github.com:owner/repo.git
codeknow search "how does auth work"
```

**Option B — local daemon (API runs as a host process):**

```bash
# 1. Bring up only ChromaDB + Redis (+ provision the model)
docker compose -f infra/docker-compose.yml up -d chromadb redis
bash infra/setup-embedding-model.sh

# 2. Opt into daemon mode, run the API, and index a repo
export CODEKNOW_DAEMON=1
codeknow daemon start
codeknow add git@github.com:owner/repo.git
```

```bash
# (Optional, for e2e) ripgrep
bash infra/ripGrep.sh
```

---

## Troubleshooting

### Port 8018 / 6379 already in use

Something else is bound to the port. Either stop the conflicting process or remap the port in `infra/docker-compose.yml` and update `CHROMA_PORT` / `CODEKNOW_REDIS_URL` in your `.env`.

### Docker Model Runner not reachable

Ensure DMR is enabled with TCP host access:

```bash
docker desktop enable model-runner --tcp 12434
curl http://localhost:12434/engines/v1/models
```

### ChromaDB resets or loses data

The persistent volume is `infra/chroma-data/` (mapped to `/data` in the container). Make sure that directory is writable and wasn't deleted. `allow_reset: false` in `chroma.local.yaml` guards against accidental resets.

### Docker Compose command not found

Older Docker installs use `docker-compose` (hyphenated) instead of `docker compose`. Either upgrade Docker or substitute `docker-compose` in the commands above.
