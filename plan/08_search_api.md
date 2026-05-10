# Plan 8: Search API Surface

## Goal
Define the REST API endpoints for semantic and hybrid search over code chunks and the knowledge graph.

## Context
- **Plan 06** (`06_semantic_search.md`) adds the embedding pipeline stage and vector storage in ChromaDB. This plan defines the API layer that exposes search capabilities over that data.
- **Plan 02** (`02_api_surface.md`) defines graph-only REST endpoints under `/v1/graph/*`. Search endpoints live under a separate `/v1/search` prefix.
- The join key between vector results and graph context is `chunk.hash`.

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `POST /v1/search` | POST | Hybrid search: embed query → vector search → enrich with graph |
| `POST /v1/search/vector` | POST | Vector-only: embed query → return chunks |
| `POST /v1/search/graph` | POST | Graph-only: keyword match → BFS/DFS subgraph |

## Request / Response Schemas

### `POST /v1/search` — Hybrid

**Request:**
```json
{
  "query": "how does authentication work?",
  "repo": "owner/repo",
  "top_k": 10,
  "mode": "hybrid"
}
```

**Response:**
```json
{
  "results": [
    {
      "chunk_text": "def validate_token(token): ...",
      "chunk_hash": "sha256:abc123...",
      "distance": 0.12,
      "metadata": {
        "file": "src/auth/session.py",
        "start_line": 42,
        "end_line": 58,
        "node_labels": "ValidateToken|create_session",
        "community_ids": "2,5",
        "repo": "owner-repo"
      },
      "nodes": [
        {"id": "session_validatetoken", "label": "ValidateToken", "community": 2, "edges": ["calls→create_session"]}
      ],
      "communities": [
        {"id": 2, "size": 15, "node_count": 8}
      ]
    }
  ],
  "stats": {
    "vector_results": 10,
    "graph_enriched": 8,
    "duration_ms": 120
  }
}
```

### `POST /v1/search/vector` — Vector-only

**Request:**
```json
{
  "query": "how does authentication work?",
  "repo": "owner/repo",
  "top_k": 10
}
```

**Response:**
```json
{
  "results": [
    {
      "chunk_text": "def validate_token(token): ...",
      "chunk_hash": "sha256:abc123...",
      "distance": 0.12,
      "metadata": {
        "file": "src/auth/session.py",
        "start_line": 42,
        "end_line": 58,
        "node_labels": "ValidateToken|create_session",
        "community_ids": "2,5",
        "repo": "owner-repo"
      }
    }
  ]
}
```

### `POST /v1/search/graph` — Graph-only

**Request:**
```json
{
  "query": "authentication",
  "repo": "owner/repo",
  "mode": "bfs",
  "max_depth": 2
}
```

**Response:**
```json
{
  "nodes": [
    {"id": "session_validatetoken", "label": "ValidateToken", "community": 2, "degree": 5}
  ],
  "edges": [
    {"source": "session_validatetoken", "target": "session_create", "relation": "calls"}
  ],
  "chunks": [
    {"hash": "sha256:abc123...", "file": "src/auth/session.py", "start_line": 42, "end_line": 58}
  ]
}
```

## Implementation Checklist

- [ ] Define Pydantic schemas in `api/schemas.py`:
  - `SearchRequest` / `SearchResponse`
  - `VectorSearchRequest` / `VectorSearchResponse`
  - `GraphSearchRequest` / `GraphSearchResponse`
  - `SearchResultChunk` / `SearchResultNode` / `SearchResultCommunity`
- [ ] Create `api/routes/search.py` — route handlers for `/v1/search/*`
- [ ] Implement `POST /v1/search`:
  - Embed query via `Embeddings`
  - Query ChromaDB via `ChromaStore.search()`
  - For each result chunk, load graph context (nodes, communities, edges) using `chunk.hash` join
  - Return merged response
- [ ] Implement `POST /v1/search/vector`:
  - Embed query → ChromaDB search → return raw results (no graph enrichment)
- [ ] Implement `POST /v1/search/graph`:
  - Keyword match via `_score_nodes()` from `engine.py`
  - BFS/DFS via existing `_bfs()` / `_dfs()`
  - Resolve chunk hashes from matching nodes
  - Return structured subgraph
- [ ] Add search routes to FastAPI app in `api/app.py`
- [ ] Tests:
  - `tests/test_search_api.py` — mock ChromaDB + graph, verify response shapes
  - Test hybrid merge joins vector results with graph context correctly
  - Test vector-only returns without graph enrichment
  - Test graph-only returns without vector results

## Files Changed
- `src/codeknow/api/schemas.py` — new request/response models
- `src/codeknow/api/app.py` — register search routes

## Files Created
- `src/codeknow/api/routes/search.py` — search endpoint handlers
- `tests/test_search_api.py` — search API tests

## Open Questions

1. **Collection isolation** — `codeknow_{owner}_{repo}` per collection, or single collection with `repo` metadata filter? Recommendation: separate collections for now.
2. **Re-embedding on update** — delete collection + re-upsert on full rebuild, since `chunk.hash` is content-addressed.
3. **Embedding batch size** — default 100, configurable per provider.
