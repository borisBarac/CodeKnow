# Implementation Plan: All 4 API Endpoints

Based on analysis of `app.py` against `API_implementation_cheatsheet.md`.

---

## Endpoint 1: `POST /v1/build` (10 items)

| # | Priority | Task | Details |
|---|----------|------|---------|
| 1 | **HIGH** | Add Pydantic request model `BuildRequest` | Replace `dict[str, Any]` with a typed model. Use existing `validate_github_ssh_url` from `params.py`. Expose `no_embed`, `embed_provider`, `embed_model` as optional fields. |
| 2 | **HIGH** | Fix error HTTP status code | Errors currently return HTTP 202. Use `JSONResponse(status_code=500)` or `HTTPException` for failures. |
| 3 | **MEDIUM** | Make async behavior consistent with 202 | Either: (A) use `BackgroundTasks`/`asyncio.create_task` to run pipeline and return immediately with `{"status": "started", "slug": slug}`, or (B) change to `status_code=200` if synchronous is intended. |
| 4 | **MEDIUM** | Add concurrency guard | Prevent duplicate builds for the same slug using a per-slug `asyncio.Lock` or check `build_status` before starting. Return HTTP 409 if already building. |
| 5 | **LOW** | Enrich response with `PipelineResult` stats | Return `node_count`, `edge_count`, `community_count`, `files` from `result.stats` alongside slug and commit_hash. |
| 6 | **LOW** | Eliminate double `PipelineConfig` construction | Compute slug from URL regex directly, or construct config once and derive output_dir from `config.slug`. |
| 7 | **LOW** | Consider keeping temp clone for cache reuse | Deleting temp after every build prevents `resolve()` from doing `git pull` on rebuild. Consider cleanup only on `DELETE /v1/repos`. |
| 8 | **LOW** | Add real progress tracking | Update `build_status[slug]["progress"]` per pipeline stage (8 stages -> 12% increments). Requires callback or shared state. |
| 9 | **LOW** | Differentiate error types by HTTP status | 422 for invalid URL, 502 for git clone failure, 500 for pipeline failure, 409 for concurrent build. |
| 10 | **LOW** | Add Pydantic response model `BuildResponse` | Typed return for OpenAPI docs. |

---

## Endpoint 2: `POST /v1/search` (8 items)

| # | Priority | Task | Details |
|---|----------|------|---------|
| 1 | **P0** | **Wrap `multi_graph_search` in `asyncio.to_thread()`** | Currently blocks the event loop (synchronous ChromaDB HTTP calls + BFS traversal). Same pattern as `/v1/build`. |
| 2 | **P0** | Add Pydantic request model `SearchRequest` | Fields: `query: str (min_length=1)`, `repos: list[str] \| None`, `top_k: int (ge=1, le=200)`. Reject empty query with 422. |
| 3 | **P1** | Type response as `HybridSearchResponse` | Remove `model_dump()` -- FastAPI handles Pydantic models natively and generates OpenAPI schema. |
| 4 | **P1** | Fix error response shape | Current error `{"error": "...", "results": []}` is not a valid `HybridSearchResponse`. Raise `HTTPException(422)` instead. |
| 5 | **P1** | Validate `repos` elements are strings | Current check only verifies `isinstance(repos, list)` -- elements aren't validated. |
| 6 | **P2** | Expose `n_results_per_graph` and `traversal_depth` | Add to `SearchRequest` with sensible defaults (5 and 2 respectively). |
| 7 | **P3** | Add top-level exception handling | Catch ChromaDB/embedding failures and return structured error instead of bare 500. |
| 8 | **P3** | Reuse `EmbeddingConfig`/`ChromaConfig` instances | Create once at app startup rather than per-request. |

---

## Endpoint 3: `DELETE /v1/repos` (7 items)

| # | Priority | Task | Details |
|---|----------|------|---------|
| 1 | **HIGH** | Add existence check with `get_path(url)` | Return HTTP 404 if repo not registered. Currently returns 200 "deleted" for unknown repos. |
| 2 | **HIGH** | **Verify collection name consistency** | Endpoint uses `f"codeknow_{slug}"` but default `ChromaConfig.collection_name` is `"codeknow_chunks"`. Confirm which the build pipeline uses -- if mismatched, deletes silently target the wrong collection. |
| 3 | **MEDIUM** | Move URL from query param to request body | SSH URLs (`git@github.com:owner/repo.git`) contain `:` and `@` that are problematic in query strings. Use `DeleteRepoRequest(url: str)` body. |
| 4 | **MEDIUM** | Avoid unnecessary `create_embeddings()` on delete | `delete_by_slug` doesn't use embeddings, but `ChromaStore.__init__` requires them. Either refactor `ChromaStore` or add a lightweight delete-only path. |
| 5 | **LOW** | Use `delete_by_slug` return value | It returns count of deleted chunks -- include in response: `{"chunks_removed": N}`. |
| 6 | **LOW** | Add URL format validation | Reject clearly invalid URLs early with HTTP 400. |
| 7 | **LOW** | Narrow exception catch | Replace bare `except Exception` with specific ChromaDB/connection errors. |

---

## Endpoint 4: `GET /v1/repos` (6 items)

| # | Priority | Task | Details |
|---|----------|------|---------|
| 1 | **MEDIUM** | Cross-reference with `repo_map.list_all()` | Detect orphaned graph dirs (no registration) and stale registrations (no graph dir). Add `registered: bool` field. |
| 2 | **MEDIUM** | Add health check via `load_graph()` | Make it opt-in with `?check_health=true` query param (avoid loading large graphs on every call). Return `health: "ok" \| "missing_graph" \| "corrupt"`. |
| 3 | **MEDIUM** | Integrate `build_status` from `app.state` | Show `"building"` / `"done"` / `"error"` status for repos currently being built. |
| 4 | **LOW** | Add Pydantic response models | `RepoMetadata` and `ListReposResponse` for OpenAPI docs and type safety. |
| 5 | **LOW** | Add per-repo error handling | Wrap `load_metadata(child)` in try/except so one bad file doesn't kill the entire listing. |
| 6 | **LOW** | Add pagination | `?limit=N&offset=M` + `total` count in response. |

---

## Cross-cutting concerns (apply to all endpoints)

| Concern | Action |
|---------|--------|
| `HTTPException` not imported | Add to `app.py` imports |
| No Pydantic models in API package | Create `codeknow_api/models.py` with `BuildRequest`, `SearchRequest`, `DeleteRepoRequest`, `BuildResponse`, `RepoMetadata`, `ListReposResponse` |
| All endpoints return `dict[str, Any]` | Migrate to typed Pydantic models for OpenAPI docs |
| No shared `ChromaStore`/`Embeddings` instance | Create at app startup, store on `app.state` |

---

## Recommended execution order

1. Create `models.py` with all Pydantic request/response models
2. Fix P0 bugs (search event loop blocking, build error status code, collection name mismatch)
3. Add existence checks and validation (build concurrency guard, delete 404, search query validation)
4. Migrate all endpoints to typed models
5. Add health/progress/status features
6. Polish (pagination, error categorization, config reuse)
