# CodeKnow

Index GitHub repos into a searchable code knowledge graph. Ships a CLI, a FastAPI server, and a background daemon.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## Install

### From source (development)

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

### As a global tool

```bash
cd CodeKnow
uv tool install .
```

Installs two standalone executables on your PATH:

- `codeknow` — the CLI
- `codeknow-api` — the API server

Uninstall with:

```bash
uv tool uninstall codeknow
```

## Quick start

```bash
# 1. Start the background daemon
codeknow daemon start

# 2. Index a repo
codeknow add git@github.com:owner/repo.git

# 3. Search
codeknow search "how does auth work"

# 4. Stop the daemon
codeknow daemon stop
```

## Commands

| Command | Description |
|---|---|
| `codeknow daemon start` | Start the background API service |
| `codeknow daemon stop` | Stop the background API service |
| `codeknow daemon status` | Check if the daemon is running |
| `codeknow add <ssh-url>` | Index a GitHub repo |
| `codeknow remove <slug>` | Remove an indexed repo |
| `codeknow search <query>` | Search the knowledge graph |
| `codeknow info` | Show daemon status and indexed repos |
| `codeknow clean` | Remove cached repos, graph output, and temp files |

Use `--slug` to scope search to specific repos (repeatable):

```bash
codeknow search "database connection" --slug owner-repo --slug other-repo
```

## API server

```bash
codeknow-api                    # production
codeknow-api --debug            # auto-reload + debug logging
```

| Flag | Default | Env var |
|---|---|---|
| `--host` | `127.0.0.1` | `CODEKNOW_API_HOST` |
| `--port` | `8080` | `CODEKNOW_API_PORT` |
| `--debug` | off | — |

## Package structure

```
packages/
  codeknow-lib/     Core library — knowledge graph pipeline, tree-sitter parsing, embeddings
  codeknow-api/     FastAPI server
  codeknow-cli/     User-facing CLI client
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CODEKNOW_HOST` | `localhost` | CLI → API host |
| `CODEKNOW_API_PORT` | `8080` | API server port |
| `CODEKNOW_API_HOST` | `127.0.0.1` | API server bind host |

## Development

```bash
uv run pytest                        # run tests
uv run project-scripts.py dev-check  # ruff + mypy
uv run project-scripts.py pipeline   # run the pipeline on a repo
```
