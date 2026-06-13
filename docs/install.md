# Installing CodeKnow

## Prerequisites

- **Python 3.10+** (up to 3.13)
- **[uv](https://docs.astral.sh/uv/)** — package and project manager
- **[Docker](https://docs.docker.com/get-docker/)** with the Compose plugin — for ChromaDB, Redis, and the embedding model

The CLI and API also need three backing services running: **ChromaDB**, **Redis**, and **Docker Model Runner** (embeddings). See [infra-setup.md](infra-setup.md) for the one-command setup. If you already have a `codeknow-api` instance running elsewhere, skip ahead to [Remote mode](#remote-mode).

Verify Python and uv are available:

```bash
python --version   # 3.10 or higher
uv --version
```

## Install from source (development)

```bash
git clone <repo-url> CodeKnow
cd CodeKnow
uv sync
```

Run commands via `uv run`:

```bash
uv run codeknow --help
uv run codeknow-api --help
```

## Install as a global tool

This puts two standalone executables on your `PATH`:

```bash
cd CodeKnow
uv tool install .
```

- **`codeknow`** — the CLI
- **`codeknow-api`** — the API server

Uninstall:

```bash
uv tool uninstall codeknow
```

## Quick start

By default the CLI connects to the API exposed by the Docker Compose stack (`localhost:8080`) — no daemon to manage:

```bash
# 1. Start the full stack (API + ChromaDB + Redis + embeddings) — see infra-setup.md
docker compose -f infra/docker-compose.yml up -d --build

# 2. Index a repo
codeknow add git@github.com:owner/repo.git

# 3. Search
codeknow search "how does auth work"

# 4. Stop the stack
docker compose -f infra/docker-compose.yml down
```

Prefer to have the CLI manage the API as a local background process? Opt into daemon mode:

```bash
export CODEKNOW_DAEMON=1
codeknow daemon start
```

## Remote mode

The default already *is* remote — the CLI talks to an API it didn't start (the Docker stack on `localhost:8080`). To point it at a different host, set `CODEKNOW_API_URL`:

```bash
export CODEKNOW_API_URL=https://api.example.com
codeknow add git@github.com:owner/repo.git
```

While `CODEKNOW_API_URL` is set, no local daemon is spawned. To run a local daemon instead, set `CODEKNOW_DAEMON=1`.

See [usage.md](usage.md) for the full command reference, endpoint-resolution precedence, daemon flags, and environment variables.

## Troubleshooting

### `uv: command not found`

Install uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your shell, then verify with `uv --version`.

### `python` not found or wrong version

On macOS:

```bash
brew install python@3.12
```

uv will automatically detect the right Python version from `pyproject.toml`. You can also set it explicitly:

```bash
uv python install 3.12
uv python pin 3.12
```

### Hatchling build errors on `uv tool install`

Make sure the working tree is clean (no uncommitted changes in `packages/`):

```bash
git status
git stash   # if needed
uv tool install .
git stash pop
```
