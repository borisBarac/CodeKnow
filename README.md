# CodeKnow

Index GitHub repos into a searchable code knowledge graph. Ships a CLI, a FastAPI server, and a background daemon.

## Setup

- [Set up for development](docs/SetUp.md)
- [Install as a tool/dependency](docs/install.md)
- [Infrastructure setup (ChromaDB, Redis, embeddings)](docs/infra-setup.md)

## Quick start

By default the CLI connects to the API exposed by the Docker stack (`localhost:8080`) — no daemon to manage:

```bash
# 1. Start the full stack (API + ChromaDB + Redis + embeddings) — see docs/infra-setup.md
docker compose -f infra/docker-compose.yml up -d --build

# 2. Index a repo
codeknow add git@github.com:owner/repo.git

# 3. Search
codeknow search "how does auth work"

# 4. Stop the stack
docker compose -f infra/docker-compose.yml down
```

To have the CLI manage a local `codeknow-api` process instead, opt into daemon mode with `CODEKNOW_DAEMON=1`. See [docs/usage.md](docs/usage.md).

## How search works

CodeKnow uses **hybrid search** — vector similarity expanded by a knowledge graph — to find relevant code across one or more indexed repositories.

### Indexing

When you run `codeknow add`, the pipeline processes the repo through seven stages:

1. **Resolve** — clone or locate the repository locally
2. **Detect** — discover source files using tree-sitter
3. **Extract AST** — parse files into an abstract syntax tree
4. **Build graph** — construct a knowledge graph where nodes are code entities (functions, classes, modules) and edges represent relationships (`imports`, `calls`, `inherits`, `uses`, etc.)
5. **Map chunks** — split source files into overlapping text chunks and link each graph node to its overlapping chunks
6. **Cluster** — detect communities of tightly-connected nodes
7. **Embed** — generate vector embeddings for each chunk and store them in ChromaDB

Each indexed repo gets its own graph (`~/.codeknow/graph/<slug>/`) and its own ChromaDB collection.

### Searching

1. **Vector search** — the query is embedded and matched against chunk embeddings in ChromaDB, returning the closest code snippets
2. **Graph expansion** — matched chunks are mapped back to graph nodes via a reverse index. A weighted BFS traversal expands from these seed nodes, following edges like `calls` (0.7), `inherits` (0.8), and `semantically_similar_to` (1.0) — stronger relations carry more weight
3. **Hybrid merge** — additional chunks discovered through graph traversal are fetched from ChromaDB and merged with the original vector results, then sorted by provenance (vector hits first) and relevance

### Multi-repo search

Multiple repos can be indexed and searched simultaneously. `multi_search` queries each repo's graph and vector store in parallel, then merges and ranks results across all repos. Use `--slug` to scope a search to specific repos:

```bash
codeknow search "database connection" --slug owner-repo --slug other-repo
```

## Commands

| Command | Description |
|---|---|
| `codeknow add <ssh-url>` | Index a GitHub repo |
| `codeknow remove <slug>` | Remove an indexed repo |
| `codeknow search <query>` | Search the knowledge graph |
| `codeknow info` | Show API status and indexed repos |
| `codeknow clean` | Remove cached repos, graph output, and temp files |
| `codeknow daemon start/stop/status` | Manage a local API process (**opt-in**: `CODEKNOW_DAEMON=1`) |

By default the CLI connects to the API exposed by the Docker stack at `localhost:8080`. The `daemon` subcommands appear only when `CODEKNOW_DAEMON=1` is set. See [docs/usage.md](docs/usage.md).

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
| `CODEKNOW_API_URL` | *(unset)* | Explicit remote API URL; takes priority over everything else |
| `CODEKNOW_DAEMON` | *(unset)* | Set to `1` to enable local daemon mode (CLI manages the API process) |
| `CODEKNOW_HOST` | `localhost` | API server host (daemon mode) |
| `CODEKNOW_API_PORT` | `8080` | API server port |
| `CODEKNOW_API_HOST` | `127.0.0.1` | API server bind host |
