# codeknow-cli

User-facing CLI client for the CodeKnow API. Indexes GitHub repos and searches the code knowledge graph.

The CLI talks to a CodeKnow API server. How it connects — and whether it manages the server's lifecycle — is controlled by a **mode** stored in `~/.codeknow/config.jsonl`. There are no endpoint environment variables; the config file is the single source of truth.

## Installation

```bash
# From the workspace root
uv sync
```

This installs the `codeknow` command.

## Running with uv

If you prefer not to activate the virtual environment, prefix all commands with `uv run`:

```bash
uv run codeknow add git@github.com:owner/repo.git
uv run codeknow remove owner-repo
uv run codeknow search "how does auth work"
```

## Configuration

The CLI reads a single-line JSON object from `~/.codeknow/config.jsonl`:

```json
{"mode":"docker","remote_url":"","host":"localhost","port":8080}
```

- `mode` — one of `docker` (default), `remote`, or `daemon`. If the file is missing or malformed, mode defaults to `docker`.
- `remote_url` — used only in `remote` mode (the API base URL).
- `host` / `port` — used only in `daemon` mode (the bind address); defaults `localhost` / `8080`.

### The `server` command group

```bash
codeknow server mode              # print the current mode
codeknow server mode docker       # set the mode (docker | remote | daemon)
codeknow server start             # start the server for the current mode
codeknow server stop              # stop the server
codeknow server status            # show server status
```

What each subcommand does depends on the current mode:

| Mode | `start` | `stop` | `status` |
|---|---|---|---|
| **docker** (default) | `docker compose -f infra/docker-compose.yml up -d` (from the current working directory — **run it from the repository root**) | `... down` | `... ps` |
| **remote** | Prints "nothing to start" (CLI does not manage a remote server) | Prints "nothing to stop" | Pings `remote_url` and reports reachable/unreachable |
| **daemon** | Spawns the local `codeknow-api` process, prints its PID | Stops the process via its pid file | Reports running/not-running + PID |

Notes:

- **docker** requires `docker` on `PATH` and `infra/docker-compose.yml` present (i.e. run from the repo root). The CLI connects to the API at `http://localhost:8080`.
- **remote** requires `remote_url` to be set in `~/.codeknow/config.jsonl` — there is no CLI subcommand for it yet. If it's missing, API commands raise a clear error. Switch back any time with `codeknow server mode docker`.
- **daemon** requires `codeknow-api` on `PATH` (i.e. `uv sync` in the workspace). The CLI binds on `host`:`port`.

## Commands

### Add a repo

Indexes a GitHub repository by its SSH URL.

```bash
codeknow add git@github.com:owner/repo.git
```

Output includes the generated slug, node count, and edge count.

### Remove a repo

Removes a previously indexed repo by its slug.

```bash
codeknow remove owner-repo
```

### Search the index

Searches all indexed repos by default. Use `--slug` to filter (repeatable).
Use `--full` to print full chunk content instead of a preview.

```bash
codeknow search "how does auth work"
codeknow search "database connection" --slug owner-repo --slug other-repo
codeknow search "database connection" --full
```

Results include file location, line range, provenance (vector or graph-expanded), and a content preview.

### Info

```bash
codeknow info
```

Shows the API endpoint status and the available repo slugs.

### Clean

Removes cached repos, graph output, and temp files. Stops the daemon first if in daemon mode.

```bash
codeknow clean        # prompts for confirmation
codeknow clean -y     # skip confirmation
```

## Prerequisites

- Python 3.10+
- An accessible CodeKnow API, via one of:
  - **docker** mode — Docker on `PATH` and `infra/docker-compose.yml` (run from the repo root).
  - **remote** mode — a reachable `remote_url`.
  - **daemon** mode — the `codeknow-api` binary on `PATH` (`uv sync` in the workspace provides it).
