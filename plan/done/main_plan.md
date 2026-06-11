# Knowledge Graph Service — Architecture Proposal

## Goal
Build a containerized knowledge graph service that:
1. Extracts entities and relationships from code + docs (AST + LLM)
2. Maps each graph node to the code chunks it references (file + line range + hash)
3. Exposes traversal APIs (BFS/DFS, shortest path, explain)
4. Designed to combine with a separate vector search pipeline for hybrid RAG

The graph provides structural retrieval (how concepts connect); vector search provides semantic retrieval (which chunks are similar). The join key is the `chunk.hash` field — both systems reference the same code chunks.

---
## Pipeline
`ingest() → detect() → extract_ast() → extract_semantic(LangChain) → build_graph() → map_chunks() → cluster() → serve()`

| Stage | Input | Output |
|---|---|---|
| `detect()` | raw files | classified by type (code, document, paper) |
| `extract_ast()` | code files | structural entities via tree-sitter |
| `extract_semantic()` | docs/papers | conceptual entities via LangChain (configurable model) |
| `build_graph()` | entities + relations | NetworkX graph |
| `map_chunks()` | graph + source files | node → chunk hash links |
| `cluster()` | graph | community labels via Leiden |
| `serve()` | graph + chunk_map | REST API for queries |

---
## Architecture
```
                    ┌─────────────────────────┐
                    │   Docker Container       │
                    │   kg-service             │
                    │                          │
                    │  ┌──────────────┐        │
                    │  │ AST Extractor│────────── tree-sitter (free)
                    │  └──────────────┘        │
                    │  ┌──────────────┐        │
                    │  │ Semantic Ext │────────── LangChain (configurable model)
                    │  └──────────────┘        │
                    │  ┌──────────────┐        │
                    │  │ Graph Builder│────────── NetworkX
                    │  └──────────────┘        │
                    │  ┌──────────────┐        │
                    │  │ Chunk Mapper │────────── hashes source ranges
                    │  └──────────────┘        │
                    │  ┌──────────────┐        │
                    │  │ Query Engine │────────── BFS/DFS/path
                    │  └──────────────┘        │
                    └──────────────────────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
          ┌─────▼─────┐  ┌─────▼─────┐  ┌──────▼──────┐
          │  Volume    │  │  Volume    │  │  LangChain  │
          │  corpus/   │  │ graph.json │  │  (LLM API)  │
          │ (raw files) │  │ chunk_map  │  │             │
          └────────────┘  └───────────┘  └─────────────┘
```

---
## Data Model

### Node
```json
{
  "id": "session_validatetoken",
  "label": "ValidateToken",
  "file_type": "code",
  "source_file": "src/auth/session.py",
  "chunks": [
    {
      "file": "src/auth/session.py",
      "start_line": 42,
      "end_line": 58,
      "hash": "sha256:abc123..."
    }
  ],
  "community": 2
}
```

### Edge
```json
{
  "source": "session_validatetoken",
  "target": "session_create",
  "relation": "calls",
  "confidence": "EXTRACTED",
  "confidence_score": 1.0
}
```

### Chunk Map (separate index)
```json
{
  "src/auth/session.py": [
    {"start_line": 1, "end_line": 20, "hash": "sha256:aaa..."},
    {"start_line": 21, "end_line": 41, "hash": "sha256:bbb..."},
    {"start_line": 42, "end_line": 58, "hash": "sha256:abc..."}
  ]
}
```

The hash field is the join key between graph nodes and vector search results. Both systems reference the same chunk hashes.

---
## API Surface
All endpoints served at `http://localhost:8080/v1/`.

| Endpoint | Method | Description |
|---|---|---|
| `/graph/query` | POST | BFS/DFS subgraph retrieval around relevant nodes |
| `/graph/path` | POST | Shortest path between two nodes |
| `/graph/explain` | POST | Explain a node (community, neighbors, role, connected chunks) |
| `/graph/chunks` | POST | Resolve node IDs → chunk hashes + line ranges |
| `/graph/build` | POST | Trigger full graph rebuild from corpus (async, returns 202) |
| `/graph/status` | GET | Check build status and graph health |

### Hybrid RAG: Graph + Vector combination flow
```
User query
    │
    ├──► Vector search pipeline (separate service)
    │    → top-K chunks by embedding similarity
    │    → [{hash: "sha256:abc...", score: 0.92}, ...]
    │
    ├──► Graph query (this service)
    │    → POST /graph/query {"question": "..."}
    │    → subgraph: nodes + edges + chunk hashes
    │
    └──► Merge by chunk hash
         → enriched context per chunk:
           - chunk text (from vector store)
           - structural relationships (from graph)
           - community context (from clustering)
           - confidence labels (EXTRACTED / INFERRED / AMBIGUOUS)
```

---
## Chunk Mapping Strategy
`map_chunks()` creates the node-to-chunk link in three steps:
1. **Chunk source files** — split each file into overlapping chunks (configurable size, default ~100 lines, 20-line overlap). Each chunk gets a SHA-256 hash of its content.
2. **Resolve node locations** — each node already carries `source_file` + `source_location` (from AST extraction or semantic extraction). Map each node's line range to the chunks it overlaps with.
3. **Write chunk map** — store `chunk_map.json` alongside `graph.json`.

This means: if vector search returns chunk `sha256:abc...`, you can look up which graph nodes reference it, get their community, neighbors, and structural role.

---
## Confidence Labels

| Label | Source | Description |
|---|---|---|
| `EXTRACTED` | AST (tree-sitter) | Directly observed in code structure |
| `INFERRED` | LangChain (LLM) | Inferred from docs/semantics |
| `AMBIGUOUS` | LLM (low score) | Low confidence, needs review |

---
## Docker Setup

### Dockerfile
```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install -e ".[api,leiden]"

COPY src/codeknow/ codeknow/

EXPOSE 8080

CMD ["uvicorn", "codeknow.api.app:create_app", "--host", "0.0.0.0", "--port", "8080"]
```

### docker-compose.yml
```yaml
services:
  kg-service:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data/corpus:/app/corpus
      - ./data/output:/app/data/output
    environment:
      - LANGCHAIN_MODEL=openai   # or anthropic, google, etc.
      - GRAPH_PATH=graph.json
      - CHUNK_MAP_PATH=chunk_map.json
      - CORPUS_PATH=/app/corpus
```

### Build & Run
```bash
docker compose build
docker compose up

# Seed the graph
curl -X POST http://localhost:8080/v1/graph/build \
  -H "Content-Type: application/json" \
  -d '{"source": "/app/corpus/", "mode": "full"}'
```

---
## Graph Storage (local volume)
```
./data/output/
  graph.json          ← full graph (nodes + edges + communities)
  chunk_map.json      ← file → chunk hash index
  cost.json           ← cumulative token tracking
  corpus/             ← raw source files
    src/auth/session.py
    docs/architecture.md
    ...
```

On each request, the service loads `graph.json` into memory (NetworkX). For corpora under ~50K nodes, this loads in <2s. For larger graphs, consider a graph DB migration.

---
## Security
- **Container isolation** — service runs in Docker, no direct host access
- **Volume mounts** — only corpus and output directories are mounted
- **LangChain credentials** — configured via environment variables or `.env` file (not committed)
- **No exposed secrets** — LLM API keys passed via env vars at runtime

---
## Key decisions
1. **LangChain for semantic extraction** — configurable model provider (OpenAI, Anthropic, Google, local models)
2. **Docker containers** — package the service with all dependencies, portable across environments
3. **File-based graph storage** — `graph.json` + `chunk_map.json` on mounted volume
4. **AST-aware chunking** — tree-sitter boundaries for code, naive line chunking for docs
5. **chunk.hash as join key** — same SHA-256 hashes used by both graph and vector pipelines
