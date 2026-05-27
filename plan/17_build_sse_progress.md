# Plan: SSE Progress Streaming for Build Endpoint

## Goal

Convert `POST /v1/build` to stream real-time per-stage progress to the client via Server-Sent Events (SSE), replacing the current blocking JSON response.

## Current State

- `POST /v1/build` returns `202` but blocks until the entire pipeline completes
- Progress tracking is trivial: `build_status` is only set at start (0%) and completion (100%)
- No SSE/WebSocket/streaming infrastructure exists in the project
- The pipeline runs 7 sequential stages in a thread via `asyncio.to_thread`

## Pipeline Stages (equal weight ~14% each)

| # | Stage | Function | Description |
|---|-------|----------|-------------|
| 1 | `resolve` | `_resolve` | Clone/fetch repo |
| 2 | `detect` | `_detect` | Discover & classify files |
| 3 | `extract_ast` | `_extract_ast` | Tree-sitter AST extraction |
| 4 | `build` | `_build` | Construct NetworkX graph |
| 5 | `map_chunks` | `_map_chunks` | Map code chunks to graph nodes |
| 6 | `cluster` | `_cluster` | Leiden community detection |
| 7 | `embed` | `_embed` | Embedding + ChromaDB upsert |

## Design Decisions

- **Always SSE** — endpoint always returns `text/event-stream`, no content negotiation
- **Backward compat** — not preserved; CLI client will be updated to consume SSE
- **No new endpoint** — same `POST /v1/build` URL, different response format
- **Dependency** — `sse-starlette` for `EventSourceResponse`

## SSE Event Flow

```
event: progress
data: {"stage": "resolve", "progress": 14, "message": "Resolving repository..."}

event: progress
data: {"stage": "detect", "progress": 28, "message": "Discovering files..."}

event: progress
data: {"stage": "extract_ast", "progress": 42, "message": "Extracting AST..."}

event: progress
data: {"stage": "build", "progress": 57, "message": "Building graph..."}

event: progress
data: {"stage": "map_chunks", "progress": 71, "message": "Mapping chunks..."}

event: progress
data: {"stage": "cluster", "progress": 85, "message": "Detecting communities..."}

event: progress
data: {"stage": "embed", "progress": 100, "message": "Generating embeddings..."}

event: done
data: {"stage":"done","progress":100,"status":"done","slug":"owner/repo","commit_hash":"abc123","node_count":42,"edge_count":120,"community_count":5}
```

On error:

```
event: error
data: {"stage": "build", "progress": 57, "message": "something went wrong"}
```

## Files to Change

### 1. `packages/codeknow-api/pyproject.toml`
- Add `sse-starlette` to dependencies

### 2. `packages/codeknow-lib/src/codeknow/pipeline/runner.py`
- Add `progress_callback: Callable[[str, int, str], None] | None = None` parameter to `run_pipeline`
- Define stage weights and human-readable messages:

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
- The final `embed` stage completes at 100% (save + commit_hash are post-embed, negligible)

### 3. `packages/codeknow-api/src/codeknow_api/models.py`
- Add `BuildProgressEvent` model:

```python
class BuildProgressEvent(BaseModel):
    stage: str
    progress: int
    message: str | None = None
```

### 4. `packages/codeknow-api/src/codeknow_api/app.py`
- Import `EventSourceResponse` from `sse_starlette`
- Rewrite the `build` handler:

```python
@app.post("/v1/build")
async def build(body: BuildRequest):
    from codeknow.pipeline import PipelineConfig, run_pipeline

    slug = PipelineConfig(repo_url=body.github_ssh_url).slug
    config = PipelineConfig(
        repo_url=body.github_ssh_url,
        input_dir=TEMP_DIR,
        output_dir=GRAPH_DIR / slug,
    )

    lock = app.state.build_locks.setdefault(slug, asyncio.Lock())
    if not await lock.acquire():
        raise HTTPException(status_code=409, detail="Build already in progress for this repo")

    # ... cleanup logic unchanged ...

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def on_progress(stage: str, percent: int, message: str) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"event": "progress", "data": BuildProgressEvent(stage=stage, progress=percent, message=message).model_dump_json()},
        )

    async def event_generator():
        try:
            result = await asyncio.to_thread(run_pipeline, config, progress_callback=on_progress)
            shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)
            app.state.build_status[slug] = {"status": "done", "progress": 100}
            await invalidate_for_slug(slug)

            done_event = {
                "event": "done",
                "data": BuildResponse(
                    status="done",
                    slug=slug,
                    commit_hash=result.commit_hash,
                    node_count=result.stats.get("nodes"),
                    edge_count=result.stats.get("edges"),
                    community_count=result.stats.get("communities"),
                ).model_dump_json(),
            }
            await queue.put(done_event)
            await queue.put(None)  # sentinel to end stream
        except Exception as exc:
            app.state.build_status[slug] = {"status": "error", "progress": 0}
            await queue.put({"event": "error", "data": json.dumps({"message": str(exc)})})
            await queue.put(None)
        finally:
            lock.release()

    asyncio.create_task(event_generator())

    async def stream():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    return EventSourceResponse(stream())
```

- Keep the lock / 409 / cleanup logic
- `asyncio.create_task` runs the pipeline in background while SSE streams
- Sentinel `None` ends the SSE stream
- `build_status` dict still updated for `GET /v1/repos` polling compatibility

### 5. `packages/codeknow-cli/src/codeknow_cli/client.py`
- Replace generated OpenAPI client call with `httpx` streaming
- Parse SSE events line-by-line
- Print progress to terminal (e.g., `[2/7] Discovering files... (28%)`)
- Return final `BuildResponse` from the `done` event

## Stage Weight Details

Equal weights (each stage ~14%):

```
resolve      →  14%
detect       →  28%
extract_ast  →  42%
build        →  57%
map_chunks   →  71%
cluster      →  85%
embed        → 100%
```

This can be refined later with actual timing data per stage. For now, equal weighting is honest and simple.

## Edge Cases

- **Concurrent builds of same repo** → 409 still returned (lock check happens before SSE starts)
- **Client disconnects mid-stream** → `EventSourceResponse` handles cancel; background task should check `asyncio.CancelledError` or use a disconnect flag
- **Pipeline error** → `error` event sent, then stream closes; `build_status` set to `{"status": "error", "progress": 0}`
- **Server restart mid-build** → in-memory state lost; client should handle reconnection/retry at a higher level
