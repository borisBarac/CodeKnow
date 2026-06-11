# Plan: Async Job Submission for `/v1/build`

## Goal

Convert `POST /v1/build` from a blocking endpoint (holds request open until pipeline finishes) to an async job-submission pattern: return `202 Accepted` immediately with a `build_id`, run the pipeline in an in-process background task, persist state in Redis (with in-memory fallback), and add `GET /v1/build/{build_id}` for polling. The CLI blocks and polls in a loop so it appears synchronous to the user.

Supersedes `plan/17_build_sse_progress.md` (SSE approach abandoned in favor of polling).

## Design Decisions

- **In-process worker** — pipeline still runs via `asyncio.to_thread` in the API process. No Celery/Arq, no separate worker process.
- **Redis state (optional)** — build state persisted to Redis when available; in-memory dict fallback when Redis is not configured.
- **Polling only** — `GET /v1/build/{build_id}` returns JSON status. No SSE/WebSocket.
- **CLI blocks** — CLI `add` command submits build then polls in a loop, appearing synchronous to the user.
- **UUID build IDs** — each build gets a `uuid4()` identifier, independent of slug.
- **Lock by slug** — only one build per slug at a time (same as current 409 behavior).

## Current State

- `POST /v1/build` returns `202` but blocks until the entire pipeline completes
- Progress tracking is trivial: `build_status` dict set at start (0%) and completion (100%)
- State stored in `app.state.build_status` (in-memory dict) and `app.state.build_locks` (asyncio.Lock dict)
- Pipeline runs in a thread via `asyncio.to_thread`
- CLI client uses a generated OpenAPI client (`code_know_api_client`) for all endpoints including build
- Search endpoint checks `build_status` for collision detection
- List repos endpoint enriches metadata from `build_status`
- Stub middleware returns a synchronous "done" response for build

---

## Implementation Phases

### Phase 1: New Models

**File:** `packages/codeknow-api/src/codeknow_api/models.py`

Add two new response models:

```python
class BuildAcceptedResponse(BaseModel):
    build_id: str
    status: str  # "queued"
    status_url: str
    slug: str


class BuildStatusResponse(BaseModel):
    build_id: str
    status: str  # "queued" | "running" | "succeeded" | "failed"
    slug: str | None = None
    progress: int = 0
    error: str | None = None
    commit_hash: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    community_count: int | None = None
```

Keep `BuildResponse` for backward compatibility during transition.

### Phase 2: Build State Store

**File:** `packages/codeknow-api/src/codeknow_api/build_state.py` (new)

Create a `BuildStateStore` class that abstracts state persistence:

**Redis mode** (when `CODEKNOW_REDIS_URL` is set):
- Store each build as a Redis hash at `ck:build:{build_id}` with fields: `status`, `slug`, `progress`, `error`, `commit_hash`, `node_count`, `edge_count`, `community_count`, `github_ssh_url`, `created_at`, `updated_at`
- Maintain `ck:build:slug:{slug}` → `build_id` index for concurrency guard
- Completed/failed builds expire after 24h

**In-memory fallback** (when Redis unavailable):
- `dict[str, dict]` keyed by `build_id`
- Secondary `slug → build_id` lookup dict
- In-memory asyncio.Lock per slug

Key methods:
- `create(build_id, slug, github_ssh_url) -> None` — persist with status `queued`
- `update(build_id, **fields) -> None` — patch any fields
- `get(build_id) -> dict | None` — read state
- `get_by_slug(slug) -> dict | None` — for lock/concurrency check
- `acquire_lock(slug) -> bool` / `release_lock(slug)` — Redis-based or in-memory lock

### Phase 3: Rewrite Build Endpoint

**File:** `packages/codeknow-api/src/codeknow_api/app.py`

**`POST /v1/build`** (202):
1. Validate `BuildRequest` (unchanged)
2. Compute slug from `github_ssh_url`
3. Check if a build for this slug is already running → 409
4. Generate `build_id = uuid4()`
5. Persist build record as `queued` via `BuildStateStore`
6. Spawn background task via `asyncio.create_task(_run_build(...))`
7. Set `Location` and `Retry-After` headers
8. Return `BuildAcceptedResponse`

```python
@app.post("/v1/build", status_code=202)
async def build(body: BuildRequest, response: Response) -> BuildAcceptedResponse:
    from codeknow.pipeline import PipelineConfig, run_pipeline

    slug = PipelineConfig(repo_url=body.github_ssh_url).slug
    state: BuildStateStore = app.state.build_state

    if not await state.acquire_lock(slug):
        raise HTTPException(status_code=409, detail="Build already in progress for this repo")

    build_id = str(uuid4())
    await state.create(build_id, slug, body.github_ssh_url)

    config = PipelineConfig(
        repo_url=body.github_ssh_url,
        input_dir=TEMP_DIR,
        output_dir=GRAPH_DIR / slug,
    )
    asyncio.create_task(_run_build(build_id, slug, config, state))

    status_url = f"/v1/build/{build_id}"
    response.headers["Location"] = status_url
    response.headers["Retry-After"] = "3"

    return BuildAcceptedResponse(
        build_id=build_id,
        status="queued",
        status_url=status_url,
        slug=slug,
    )
```

**Background task `_run_build(build_id, slug, config, state)`:**
1. Update state to `running`, progress 0
2. Run existing cleanup logic (rm old graph dir, ChromaDB cleanup)
3. Call `run_pipeline(config)` via `asyncio.to_thread`
4. On success: update state to `succeeded`, progress 100, store result fields, invalidate search cache, cleanup temp dir
5. On error: update state to `failed`, store error message
6. Always: release lock

**`GET /v1/build/{build_id}`** (new):
1. Look up build state via `BuildStateStore`
2. If not found → 404
3. If `queued`/`running` → 200 with current status + `Retry-After: 3` header
4. If `succeeded` → 200 with full result payload
5. If `failed` → 200 with error payload

### Phase 4: Update Downstream Consumers

**`POST /v1/search`** — update the build-collision check:
- Replace `app.state.build_status.get(s, {}).get("status") == "building"` with query to `BuildStateStore.get_by_slug(s)` checking `status in ("queued", "running")`

**`GET /v1/repos`** — update the build-status enrichment:
- Replace `app.state.build_status.get(slug)` with `BuildStateStore.get_by_slug(slug)`

**Stub middleware** (`middleware.py`):
- Update `POST /v1/build` stub to return `BuildAcceptedResponse` shape:
  ```python
  "/v1/build": lambda _body, _qs: (
      202,
      {
          "build_id": "stub-build-id",
          "status": "queued",
          "status_url": "/v1/build/stub-build-id",
          "slug": _STUB_REPO["slug"],
      },
  )
  ```
- Add `GET /v1/build/stub-build-id` stub that returns `succeeded` immediately with full result

### Phase 5: Update CLI Client

**File:** `packages/codeknow-cli/src/codeknow_cli/client.py`

**`add_to_index` method** — replace single call with submit + poll loop:

```python
def add_to_index(self, ssh_url: str) -> BuildResponse:
    # 1. POST /v1/build -> get build_id + status_url
    # 2. Poll GET /v1/build/{build_id} every 3s
    # 3. Print progress dots to terminal
    # 4. When status is "succeeded", construct and return BuildResponse
    # 5. When status is "failed", raise ApiError with error message
```

The CLI call in `main.py:add` doesn't need to change — it already prints the `BuildResponse` fields. The polling is hidden inside the client.

### Phase 6: Regenerate OpenAPI Client

- Add `GET /v1/build/{build_id}` to the OpenAPI schema
- Change `POST /v1/build` response to `BuildAcceptedResponse` (202)
- Regenerate: `uv run project-scripts.py gen-client --output-dir packages/codeknow-cli/generated`
- Update CLI's `add_to_index` to use the new generated types for both endpoints (or handle build manually via httpx if the generated client is awkward for the polling pattern)

### Phase 7: Remove Old State

- Remove `app.state.build_status` dict and `app.state.build_locks` dict from `create_app()`
- Remove `asyncio.Lock` import and lock logic from `app.py`
- Delete `plan/17_build_sse_progress.md` (superseded by this plan)

### Phase 8: Tests

| Test | What it covers |
|---|---|
| `test_build_submit_returns_202` | POST returns `build_id`, `status="queued"`, `Location` header |
| `test_build_poll_running_then_done` | Submit, poll until `succeeded`, verify result fields |
| `test_build_concurrent_409` | Submit build for same slug while running → 409 |
| `test_build_failure_returns_failed` | Pipeline error → status `failed` with error message |
| `test_build_not_found_404` | Poll unknown `build_id` → 404 |
| Update `TestSearchBuildCollision` | Adapt to new state store |
| Update stub middleware tests | New response shape |

---

## Files Changed Summary

| File | Change |
|---|---|
| `packages/codeknow-api/src/codeknow_api/models.py` | Add `BuildAcceptedResponse`, `BuildStatusResponse` |
| `packages/codeknow-api/src/codeknow_api/build_state.py` | **New** — `BuildStateStore` (Redis + in-memory) |
| `packages/codeknow-api/src/codeknow_api/app.py` | Rewrite build endpoint, add status endpoint, remove old state |
| `packages/codeknow-api/src/codeknow_api/middleware.py` | Update stub responses for build |
| `packages/codeknow-cli/src/codeknow_cli/client.py` | Poll loop in `add_to_index` |
| `packages/codeknow-cli/generated/...` | Regenerated client |
| `packages/codeknow-api/tests/test_build.py` | **New** — build endpoint tests |
| `packages/codeknow-api/tests/test_search.py` | Update build-collision fixtures |
| `packages/codeknow-api/pyproject.toml` | No new deps (Redis already optional) |

## Execution Order

Phase 1 (models) → Phase 2 (state store) → Phase 3 (endpoints) → Phase 7 (remove old state) → Phase 4 (downstream) → Phase 8 (tests) → Phase 5 (CLI) → Phase 6 (regen client)

## Edge Cases

- **Concurrent builds of same repo** → 409 still returned (lock check happens before accepting)
- **Server restart mid-build** → in-memory state lost; Redis state survives if configured. Client should handle reconnection/retry.
- **Client disconnects** — background task continues to completion regardless
- **Build for slug with existing succeeded build** → allowed (rebuild), old state overwritten
- **Multiple submissions for same slug before any completes** → second gets 409
