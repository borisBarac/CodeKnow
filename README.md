# CodeKnow

Index GitHub repos into a searchable code knowledge graph. Ships a CLI, a FastAPI server, and a background daemon.

## Setup

- [Set up for development](docs/SetUp.md)
- [Install as a tool/dependency](docs/install.md)
- [Infrastructure setup (ChromaDB, Redis, embeddings)](docs/infra-setup.md)

## Quick start

The CLI connects to the API exposed by the Docker stack (`localhost:8080`) by default — no daemon to manage:

```bash
# 1. Start the full stack (API + ChromaDB + Redis + embeddings) — see docs/infra-setup.md
#    (run from the repo root; equivalent to: docker compose -f infra/docker-compose.yml up -d --build)
codeknow server start

# 2. Index a repo
codeknow add git@github.com:owner/repo.git

# 3. Search
codeknow search "how does auth work"

# 4. Stop the stack
codeknow server stop
```

To run the API as a local `codeknow-api` process the CLI manages, switch modes with `codeknow server mode daemon` (or `remote` to point at any other API). See [docs/usage.md](docs/usage.md).

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
| `codeknow server <subcommand>` | Manage the API server: `mode` (`docker` \| `remote` \| `daemon`), `start`, `stop`, `status` |

The CLI resolves its endpoint from the `mode` field in `~/.codeknow/config.jsonl`. The default mode is `docker`, which connects to the Docker stack at `localhost:8080`. Switch to `daemon` (CLI manages a local `codeknow-api` process) or `remote` (any other API URL) with `codeknow server mode <mode>`. See [docs/usage.md](docs/usage.md).

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

## Configuration

The CLI is **config-file driven**, not environment-variable driven. It reads a single-line JSON object from `~/.codeknow/config.jsonl`:

```json
{"mode":"docker","remote_url":"","host":"localhost","port":8080}
```

- `mode` — `docker` (default), `remote`, or `daemon`. Switch with `codeknow server mode <mode>`.
- `remote_url` — used only in `remote` mode.
- `host` / `port` — used only in `daemon` mode.

See [docs/usage.md](docs/usage.md) for the full reference. (The `codeknow-api` server itself still reads `CODEKNOW_API_HOST` / `CODEKNOW_API_PORT` — see the table above.)
