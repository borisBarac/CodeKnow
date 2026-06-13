# Infrastructure setup

CodeKnow relies on three local services:

- **ChromaDB** — vector store for embeddings (run via Docker Compose)
- **Redis** — search response cache (run via Docker Compose)
- **Ollama** — local embedding model server (`qwen3-embedding:4b`)

This guide brings all three up and connects the app to them. All infra lives under [`infra/`](../infra/).

---

## Prerequisites

- **[Docker](https://docs.docker.com/get-docker/)** with the Compose plugin (for ChromaDB + Redis)
- **[Homebrew](https://brew.sh/)** (macOS) — used by the Ollama and ripgrep install scripts
- **ripgrep** — required for e2e tests (see below)

---

## 1. Start ChromaDB + Redis

Both run from a single Compose file:

```bash
docker compose -f infra/docker-compose.yml up -d
```

This starts:

| Service | Image | Host port | Persistent data |
|---|---|---|---|
| ChromaDB | `chromadb/chroma:1.5.3` | `8018` → container `8000` | `infra/chroma-data/` |
| Redis | `redis:7-alpine` | `6379` | append-only in-container |

ChromaDB is configured by [`infra/chroma.local.yaml`](../infra/chroma.local.yaml) (persist path `/data`, listen on `127.0.0.1:8000`), mounted read-only into the container. Redis runs with `appendonly` and a 256 MB LRU memory cap.

Check they're up:

```bash
docker compose -f infra/docker-compose.yml ps
```

Stop them:

```bash
docker compose -f infra/docker-compose.yml down
```

---

## 2. Install Ollama + the embedding model

The [`infra/install-ollama-and-qwen3-embedding.sh`](../infra/install-ollama-and-qwen3-embedding.sh) script installs Ollama (if missing), starts the server, and pulls the embedding model:

```bash
bash infra/install-ollama-and-qwen3-embedding.sh
```

This will:

1. Install Ollama via Homebrew (`brew install --cask ollama`) if it's not already present.
2. Start the Ollama server (it waits up to 30s, then falls back to `ollama serve`).
3. Pull the `qwen3-embedding:4b` model.

Ollama listens on `http://127.0.0.1:11434`. Verify the model is available:

```bash
ollama list
```

You should see `qwen3-embedding:4b` in the list.

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

Copy `.env.example` to `.env` and point the app at these services. The defaults already match the infra above:

```bash
# Embeddings (Ollama)
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=qwen3-embedding:4b
# OLLAMA_BASE_URL=http://localhost:11434/v1

# ChromaDB
# CHROMA_HOST=localhost
# CHROMA_PORT=8018

# Redis cache
# CODEKNOW_REDIS_URL=redis://localhost:6379/0
```

All three connection values are commented out because the defaults already match the `infra/` setup — uncomment and edit only if you changed ports or hosts.

---

## Quick start (all services)

```bash
# 1. Vector store + cache
docker compose -f infra/docker-compose.yml up -d

# 2. Embedding server + model
bash infra/install-ollama-and-qwen3-embedding.sh

# 3. (Optional, for e2e) ripgrep
bash infra/ripGrep.sh

# 4. Start the CodeKnow daemon and index a repo
codeknow daemon start
codeknow add git@github.com:owner/repo.git
```

---

## Troubleshooting

### Port 8018 / 6379 already in use

Something else is bound to the port. Either stop the conflicting process or remap the port in `infra/docker-compose.yml` and update `CHROMA_PORT` / `CODEKNOW_REDIS_URL` in your `.env`.

### Ollama server not ready

The install script waits 30s then tries `ollama serve` directly. If it still fails, start it manually and re-check:

```bash
ollama serve &
curl http://127.0.0.1:11434/api/tags
```

### ChromaDB resets or loses data

The persistent volume is `infra/chroma-data/` (mapped to `/data` in the container). Make sure that directory is writable and wasn't deleted. `allow_reset: false` in `chroma.local.yaml` guards against accidental resets.

### Docker Compose command not found

Older Docker installs use `docker-compose` (hyphenated) instead of `docker compose`. Either upgrade Docker or substitute `docker-compose` in the commands above.
