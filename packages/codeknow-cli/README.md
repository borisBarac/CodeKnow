# codeknow-cli

User-facing CLI client for the CodeKnow API. Manages a background daemon, indexes GitHub repos, and searches the code knowledge graph.

## Installation

```bash
# From the workspace root
uv sync
```

This installs the `codeknow` command and its dependency `codeknow-api`.

## Running with uv

If you prefer not to activate the virtual environment, prefix all commands with `uv run`:

```bash
uv run codeknow daemon start
uv run codeknow add git@github.com:owner/repo.git
uv run codeknow remove owner-repo
uv run codeknow search "how does auth work"
```

## Commands

### Daemon management

The CLI communicates with a local API server (daemon). Start it before using other commands.

```bash
codeknow daemon start    # Start the background service
codeknow daemon stop     # Stop the background service
codeknow daemon status   # Check if the daemon is running
```

The daemon runs `codeknow-api` on `localhost:9999` by default.

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

```bash
codeknow search "how does auth work"
codeknow search "database connection" --slug owner-repo --slug other-repo
```

Results include file location, line range, provenance (vector or graph-expanded), and a content preview.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CODEKNOW_HOST` | `localhost` | API server host |
| `CODEKNOW_API_PORT` | `8080` | API server port |

## Prerequisites

- Python 3.10+
- `codeknow-api` must be resolvable on `$PATH` (handled by `uv sync`)
- The daemon must be running (`codeknow daemon start`) before using `add`, `remove`, or `search`
