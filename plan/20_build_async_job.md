# Plan: Async Job Submission for `/v1/build`

## Goal

Convert `POST /v1/build` from a blocking endpoint (holds request open until pipeline finishes) to an async job-submission pattern: return `202 Accepted` immediately, run the pipeline in an in-process background task, and add `GET /v1/build/{slug}` for polling. The CLI blocks and polls in a loop so it appears synchronous to the user.

Supersedes `plan/17_build_sse_progress.md` (SSE approach abandoned in favor of polling).

## Design Decisions

1. **In-process worker, in-memory state only** — pipeline runs via `asyncio.to_thread` in the API process. No Celery/Arq, no Redis for build state. State lives in `app.state` dicts (same pattern as today). No new infrastructure dependencies.
2. **Slug-keyed** — no UUID build IDs. State keyed by slug (e.g. `owner/repo`). Only one build per slug at a time. `GET /v1/build/{slug}` for polling. Simpler than UUIDs; the slug is the natural identifier for a single-user daemon.
3. **202/200 status code split** — `GET /v1/build/{slug}` returns `202` while queued/running, `200` when succeeded/failed. CLI polling loop checks status code, not body field. `Retry-After` header on 202.
4. **Progress callback in pipeline** — add `progress_callback` parameter to `run_pipeline`. Pipeline calls back after each of its 7 stages (resolve, detect, extract_ast, build, map_chunks, cluster, embed). Background task updates `app.state` via the callback. CLI shows per-stage progress. Backward-compatible (callback defaults to `None`).
5. **No separate state module** — state is `app.state.build_jobs: dict[str, dict]` and `app.state.build_locks: dict[str, asyncio.Lock]` directly on `app.state`. No `build_state.py` module, no class wrapping a dict.
6. **One response model** — single `BuildStatusResponse` model for both POST and GET. All fields optional except `status` and `slug`. POST returns it with `status="queued"`, GET returns it with whatever the current state is.
7. **Raw httpx for CLI build flow** — CLI uses raw `httpx` for POST + polling loop, bypassing the generated OpenAPI client for build. Generated client still used for search, delete, list_repos. More control over retry/timeout behavior.
8. **Cleanup synchronous before 202** — ChromaDB/vector cleanup and old graph dir deletion happen in the POST handler before returning 202. Only the pipeline run itself moves to the background task. This keeps cleanup errors in the request/response cycle.

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

### Phase 1: Add `progress_callback` to Pipeline Runner

**File:** `packages/codeknow-lib/src/codeknow/pipeline/runner.py`

- Add `progress_callback: Callable[[str, int, str], None] | None = None` parameter to `run_pipeline`
- Define stage info as a module-level constant:

```python
_STAGES = [
    ("resolve",     14, "Resolving repository..."),
    ("detect",      28, "Discovering files..."),
    ("extract_ast", 42, "Extracting AST..."),
    ("build",       57, "Building graph..."),
    ("map_chunks",  71, "Mapping chunks..."),
    ("cluster",     85, "Detecting communities..."),
    ("embed",      100, "Generating embeddings..."),
]
```

- After each stage call, invoke `progress_callback(stage_name, percent, message)` if provided
- Backward-compatible: when `progress_callback=None`, behavior is identical to current

### Phase 2: Update Models

**File:** `packages/codeknow-api/src/codeknow_api/models.py`

Replace `BuildResponse` with a single unified model:

```python
class BuildStatusResponse(BaseModel):
    status: str  # "queued" | "running" | "succeeded" | "failed"
    slug: str
    status_url: str | None = None  # only on POST 202
    progress: int = 0
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    commit_hash: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    community_count: int | None = None
```

Delete `BuildResponse`. Update all consumers.

### Phase 3: Rewrite Build Endpoint

**File:** `packages/codeknow-api/src/codeknow_api/app.py`

Replace `app.state.build_status` with `app.state.build_jobs: dict[str, dict]` keyed by slug. Each entry:

```python
{
    "status": "running",
    "slug": "owner/repo",
    "progress": 42,
    "stage": "extract_ast",
    "message": "Extracting AST...",
    "error": None,
    "commit_hash": None,
    "node_count": None,
    "edge_count": None,
    "community_count": None,
}
```

Keep `app.state.build_locks: dict[str, asyncio.Lock]` as-is.

**`POST /v1/build`** (202):
1. Validate `BuildRequest` (unchanged)
2. Compute slug, build config
3. Check lock → 409 if already building
4. Acquire lock
5. Run existing cleanup synchronously (rm old graph dir, rm temp dir, ChromaDB delete)
6. Set initial state: `app.state.build_jobs[slug] = {status: "queued", ...}`
7. Spawn background task via `asyncio.create_task(_run_build(slug, config))`
8. Set `Location: /v1/build/{slug}` and `Retry-After: 3` headers
9. Return `BuildStatusResponse(status="queued", slug=slug, status_url=..., progress=0)`

**Background task `_run_build(slug, config)`:**
1. Update state to `running`, progress 0
2. Define `on_progress(stage, percent, message)` callback that updates `app.state.build_jobs[slug]` (runs in thread context via `loop.call_soon_threadsafe`)
3. Call `run_pipeline(config, progress_callback=on_progress)` via `asyncio.to_thread`
4. On success: update state to `succeeded`, progress 100, store result fields, invalidate search cache, cleanup temp dir
5. On error: update state to `failed`, store error message
6. Always: release lock

**`GET /v1/build/{slug}`** (new):
1. Look up `app.state.build_jobs.get(slug)`
2. If not found → 404
3. If `queued`/`running` → 202 with current state + `Retry-After: 3` header
4. If `succeeded` → 200 with full result payload
5. If `failed` → 200 with error payload

### Phase 4: Update Downstream Consumers

**`POST /v1/search`** — update the build-collision check:
- Replace `app.state.build_status.get(s, {}).get("status") == "building"` with `app.state.build_jobs.get(s, {}).get("status") in ("queued", "running")`

**`GET /v1/repos`** — update the build-status enrichment:
- Replace `app.state.build_status.get(slug)` with `app.state.build_jobs.get(slug)`

**Stub middleware** (`middleware.py`):
- Update `POST /v1/build` stub to return `BuildStatusResponse` shape:
  ```python
  "/v1/build": lambda _body, _qs: (
      202,
      {
          "status": "queued",
          "slug": _STUB_REPO["slug"],
          "status_url": f"/v1/build/{_STUB_REPO['slug']}",
          "progress": 0,
      },
  )
  ```
- Add `GET /v1/build/{slug}` route stub that returns `succeeded` immediately with full result

### Phase 5: Update CLI Client

**File:** `packages/codeknow-cli/src/codeknow_cli/client.py`

**`add_to_index` method** — replace generated client call with raw httpx submit + poll loop:

```python
def add_to_index(self, ssh_url: str) -> BuildStatusResponse:
    # 1. POST /v1/build via httpx -> get back status + slug
    # 2. Poll GET /v1/build/{slug} every 3s via httpx
    # 3. Print progress per stage: "[2/7] Discovering files... (28%)"
    # 4. When 200 + status="succeeded", return result
    # 5. When 200 + status="failed", raise ApiError
```

The CLI call in `main.py:add` prints fields from the returned object. Minor updates to field names if needed.

### Phase 6: Regenerate OpenAPI Client

- `POST /v1/build` response changes to `BuildStatusResponse` (202)
- `GET /v1/build/{slug}` added to schema
- `BuildResponse` removed from schema
- Regenerate: `uv run project-scripts.py gen-client --output-dir packages/codeknow-cli/generated`
- CLI uses generated client for search/delete/list_repos; raw httpx for build

### Phase 7: Cleanup

- Remove `BuildResponse` from `models.py`
- Remove old `app.state.build_status` (replaced by `app.state.build_jobs`)
- Delete `plan/17_build_sse_progress.md` (superseded)

### Phase 8: Tests

| Test | What it covers |
|---|---|
| `test_build_submit_returns_202` | POST returns `status="queued"`, `Location` header, `Retry-After` header |
| `test_build_poll_running_then_done` | Submit, poll until 200 `succeeded`, verify result fields |
| `test_build_concurrent_409` | Submit build for same slug while running → 409 |
| `test_build_failure_returns_failed` | Pipeline error → status `failed` with error message |
| `test_build_not_found_404` | Poll unknown slug → 404 |
| `test_pipeline_progress_callback` | Verify callback called 7 times with correct stage names |
| Update `TestSearchBuildCollision` | Adapt to `app.state.build_jobs` |
| Update stub middleware tests | New response shape |

---

## Files Changed Summary

| File | Change |
|---|---|
| `packages/codeknow-lib/src/codeknow/pipeline/runner.py` | Add `progress_callback` param |
| `packages/codeknow-api/src/codeknow_api/models.py` | Replace `BuildResponse` with `BuildStatusResponse` |
| `packages/codeknow-api/src/codeknow_api/app.py` | Rewrite build endpoint, add status endpoint, rename state dict |
| `packages/codeknow-api/src/codeknow_api/middleware.py` | Update stub responses for build |
| `packages/codeknow-cli/src/codeknow_cli/client.py` | Raw httpx poll loop in `add_to_index` |
| `packages/codeknow-cli/generated/...` | Regenerated client |
| `packages/codeknow-api/tests/test_build.py` | **New** — build endpoint tests |
| `packages/codeknow-api/tests/test_search.py` | Update build-collision fixtures |
| `plan/17_build_sse_progress.md` | **Delete** — superseded |

## Execution Order

Phase 1 (pipeline callback) → Phase 2 (models) → Phase 3 (endpoints) → Phase 7 (cleanup) → Phase 4 (downstream) → Phase 8 (tests) → Phase 5 (CLI) → Phase 6 (regen client)

## Edge Cases

- **Concurrent builds of same repo** → 409 still returned (lock check happens before cleanup)
- **Server restart mid-build** → in-memory state lost, build dies. Client sees connection error. Acceptable for local daemon.
- **Client disconnects** — background task continues to completion regardless
- **Build for slug with existing succeeded build** → allowed (rebuild), old state overwritten after cleanup
- **Multiple submissions for same slug before any completes** → second gets 409
- **Cleanup fails (e.g. ChromaDB down)** → error returned synchronously before 202, build never starts
