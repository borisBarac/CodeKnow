# Plan 2: API Surface & Query Engine

## Goal
Define the REST API endpoints and the query engine that serves graph traversals locally.

## Context
Currently runs as an **MCP stdio server** (`serve/engine.py`) with 7 query tools. The REST API is the next layer — wrapping the same underlying functions in FastAPI endpoints. No API Gateway, no auth — that's for cloud deployment later.

> **Current state**: All query logic exists as standalone functions in `serve/engine.py`. No REST API (`api/` directory) exists yet.

## Reusable Components (already exist in `serve/engine.py`)

| Function | Location | Maps to endpoint |
|---|---|---|
| `_load_graph()` | `engine.py:11` | Graph loading (shared utility) |
| `_score_nodes()` | `engine.py:52` | `/query` (node matching by keyword) |
| `_bfs()` | `engine.py:68` | `/query` (mode=bfs) |
| `_dfs()` | `engine.py:86` | `/query` (mode=dfs) |
| `_subgraph_to_text()` | `engine.py:104` | MCP-specific formatting; REST needs structured JSON |
| `_find_node()` | `engine.py:132` | `/explain`, `/path` (node lookup) |
| `_tool_shortest_path` logic | `engine.py:392-418` | `/path` |
| `_tool_get_node` logic | `engine.py:320-339` | `/explain` (partial — no chunks, no neighbors) |
| `_tool_graph_stats` logic | `engine.py:380-390` | `/status` (no build status) |
| `god_nodes()` | `analyze.py:44` | New endpoint (not in original plan) |
| `surprising_connections()` | `analyze.py:68` | New endpoint (not in original plan) |
| `suggest_questions()` | `analyze.py:359` | New endpoint (not in original plan) |
| `graph_diff()` | `analyze.py:507` | New endpoint (not in original plan) |

## Checklist

- [x] Reuse existing query logic (`_bfs`, `_dfs`, `_score_nodes`, `_subgraph_to_text`, `_find_node`, `_tool_shortest_path`)
  - All exist as standalone callables in `engine.py`, decoupled from MCP.
- [ ] Define request/response Pydantic schemas for each endpoint (`api/schemas.py`)
- [ ] `POST /v1/graph/query` — BFS/DFS subgraph retrieval around relevant nodes
  - Logic exists in `_bfs()` + `_dfs()` + `_score_nodes()`. Returns text currently; REST needs structured JSON (nodes + edges as dicts).
- [ ] `POST /v1/graph/path` — shortest path between two nodes
  - Logic exists in `_tool_shortest_path` (`engine.py:392`). Uses `nx.shortest_path()` with max_hops guard.
- [ ] `POST /v1/graph/explain` — explain a node (community, neighbors, role, connected chunks)
  - Partial logic in `_tool_get_node` (`engine.py:320`) returns community, degree, source. **Missing**: neighbor list, chunk references. Currently split across `get_node` + `get_neighbors` MCP tools.
- [ ] `POST /v1/graph/chunks` — resolve node IDs → chunk hashes + line ranges
  - **No implementation exists.** `chunk_mapper.py` has `build_reverse_index()` but the serve engine doesn't load `chunk_map.json`.
- [ ] `POST /v1/graph/build` — trigger full graph rebuild from corpus (async, returns 202)
  - **No implementation exists.** Build pipeline is CLI-only. Requires programmatic build trigger + in-process status tracking.
- [ ] `GET /v1/graph/status` — check build status and graph health
  - Partial: `_tool_graph_stats` (`engine.py:380`) returns node/edge/community counts. **Missing**: build status, last build time.
- [ ] Implement query engine with NetworkX:
  - [x] BFS traversal with configurable depth (`engine.py:68`)
  - [x] DFS traversal with configurable depth (`engine.py:86`)
  - [x] Shortest path via `nx.shortest_path()` (`engine.py:402`)
  - [x] Node lookup by label/ID, diacritic-insensitive (`engine.py:132`)
  - [ ] Node explanation with chunk references (blocked on chunk_map loading)
- [ ] Add pagination for large subgraph results
  - Current: char-budget truncation in `_subgraph_to_text` only. No offset/limit/cursor.
- [ ] Add request validation and error schemas (400, 404, 500)
- [ ] `uvicorn` dev server entry point with CORS enabled for local testing
  - `fastapi` + `uvicorn` declared in `pyproject.toml:[api]` but never used.
- [ ] Create `src/codeknow/api/` module:
  - `api/app.py` — FastAPI app factory
  - `api/routes.py` — route handlers
  - `api/schemas.py` — Pydantic request/response models

## Additional Endpoints (from unplanned `graph/analyze.py`)

These capabilities exist but have no REST endpoint:

| Capability | Location | Suggested Endpoint |
|---|---|---|
| God nodes (most connected) | `analyze.py:44` | `GET /v1/graph/god-nodes` |
| Surprising connections | `analyze.py:68` | `GET /v1/graph/surprises` |
| Suggested questions | `analyze.py:359` | `GET /v1/graph/questions` |
| Graph diff | `analyze.py:507` | `POST /v1/graph/diff` |
| Community lookup | `engine.py:359` | `GET /v1/graph/community/{id}` |
| Neighbor lookup | `engine.py:341` | `GET /v1/graph/neighbors/{id}` |

## Gaps & Blockers

1. **`_subgraph_to_text` returns text, not JSON** — REST endpoints need structured responses (Node/Edge dicts). The text formatting is MCP-specific and must be replaced for REST.
2. **Chunk resolution not loaded** — serve engine loads `graph.json` but not `chunk_map.json`. `/chunks` and `/explain` endpoints need chunk data.
3. **Build trigger** — no programmatic build trigger exists. Requires wrapping `run_pipeline()` in an async task with status tracking.
4. **No `api/` module** — `src/codeknow/api/` directory doesn't exist.

## Key Decisions
- All endpoints are JSON in / JSON out
- Build is async (202 + status polling) since it can take minutes
- Query responses include chunk hashes so downstream can merge with vector results
- Start from existing MCP query tools and wrap them in REST endpoints
- MCP stdio server continues to work — REST is an additional transport, not a replacement
