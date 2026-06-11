# Candidate 2: Insulate the API layer from lib internals

**Strength:** Strong
**Dependency category:** local-substitutable

## Files involved

| File | Lines | Role |
|---|---|---|
| `codeknow_api/app.py` | 324 | FastAPI app — build, search, delete, list_repos handlers |
| `codeknow_api/models.py` | 80 | Pydantic request/response models |
| `codeknow_api/middleware.py` | 132 | StubMiddleware for testing/demo |
| `codeknow_api/cache.py` | 169 | Redis search cache |
| `codeknow_api/params.py` | 24 | GitHub SSH URL validation regex |
| `codeknow_api/__init__.py` | 3 | Version string |

### codeknow-lib internals that leak

| Symbol | Location | Used by |
|---|---|---|
| `_env_path` (private) | `pipeline/config.py` | `app.py` (delete handler) |
| `PipelineConfig.chroma_collection` | `pipeline/config.py` | `app.py` (build + delete handlers) |
| `PipelineConfig.chroma_host` | `pipeline/config.py` | `app.py` (build + delete handlers) |
| `PipelineConfig.chroma_port` | `pipeline/config.py` | `app.py` (build + delete handlers) |
| `PipelineConfig.slug` | `pipeline/config.py` | `models.py` (DeleteRepoRequest.resolve_slug), `middleware.py` (_stub_delete) |
| `ChromaConfig` | `vector/chroma.py` | `app.py` (build + delete handlers) |
| `EmbeddingConfig` | `vector/embeddings.py` | `app.py` (build + delete handlers) |
| `ChromaStore` | `vector/chroma.py` | `app.py` (build + delete handlers) |
| `run_pipeline` | `pipeline/runner.py` | `app.py` (build handler) |
| `load_metadata` | `pipeline/__init__.py` | `app.py` (list_repos handler) |
| `load_graph` | `pipeline/io.py` | `app.py` (list_repos health check) |
| `multi_graph_search` | `vector/multi_search.py` | `app.py` (search handler) |
| `get_path`, `get_url`, `unregister` | `git_download/` | `app.py` (delete handler) |

## Problem details

### 1. Private symbol import

`app.py` imports `_env_path` from `codeknow.pipeline.config`:

```python
from codeknow.pipeline.config import _env_path
```

This underscore-prefixed function is a private internal. The API package's stability now depends on an internal symbol that the lib can rename or remove at any time.

### 2. Duplicated ChromaDB wiring

Both the **build handler** and **delete handler** construct `ChromaStore` + `EmbeddingConfig` + `ChromaConfig` from `PipelineConfig` attributes:

```python
# In build handler:
chroma_config = ChromaConfig(
    collection=config.chroma_collection,
    host=config.chroma_host,
    port=config.chroma_port,
)
embed_config = EmbeddingConfig(...)
store = ChromromaStore(chroma_config, embed_config, create_embeddings(embed_config))
```

The same construction appears in the delete handler. If the ChromaDB wiring changes, both must be updated in lockstep.

### 3. Models reaching into the pipeline

`models.py` has a lazy import of `PipelineConfig` inside `DeleteRepoRequest.resolve_slug()`:

```python
def resolve_slug(self) -> str:
    from codeknow.pipeline import PipelineConfig
    if self.slug:
        return self.slug
    if self.url:
        return PipelineConfig(repo_url=self.url).slug
    ...
```

This makes the Pydantic models module not standalone — it cannot be validated or tested without the pipeline dependency.

### 4. Middleware reaching into the pipeline

`middleware.py` also lazily imports `PipelineConfig`:

```python
def _stub_delete(body, qs):
    from codeknow.pipeline import PipelineConfig
    ...
    slug = PipelineConfig(repo_url=url).slug
```

Same slug-derivation logic duplicated from models.py.

### 5. Module-level paths computed at import time

```python
GRAPH_DIR = Path(os.environ.get("CODEKNOW_GRAPH_DIR", Path.home() / ".codeknow" / "graphs"))
TEMP_DIR = Path(os.environ.get("CODEKNOW_TEMP_DIR", Path.home() / ".codeknow" / "tmp"))
_CODEKNOW_HOME = Path.home() / ".codeknow"
```

Tests must monkeypatch the module attribute (`monkeypatch.setattr(app_module, "GRAPH_DIR", ...)`) rather than injecting the dependency.

## Current architecture

```
app.py
├── build handler
│   ├── PipelineConfig(repo_url=...)
│   ├── PipelineConfig.chroma_collection/host/port  ← reads internals
│   ├── ChromaConfig(...)
│   ├── EmbeddingConfig(...)
│   ├── ChromaStore(...)
│   ├── run_pipeline(config)
│   ├── ChromaStore.delete_by_slug(...)             ← separate construction
│   └── cache.invalidate_for_slug(slug)
│
├── delete handler
│   ├── DeleteRepoRequest.resolve_slug()            ← reaches into PipelineConfig
│   ├── _env_path(...)                              ← private import
│   ├── ChromaConfig(...)
│   ├── ChromaStore(...)
│   ├── store.delete_by_slug(...)
│   ├── unregister(slug)                            ← git_download
│   ├── shutil.rmtree(graph_dir)
│   └── shutil.rmtree(tmp_dir)
│
├── search handler
│   └── multi_graph_search(GRAPH_DIR, query, top_k, repos)
│
└── list_repos handler
    ├── repo_map.list_all()
    ├── load_metadata(graph_dir)
    └── load_graph(graph_dir)                       ← health check path

models.py
└── DeleteRepoRequest.resolve_slug() → PipelineConfig(slug derivation)

middleware.py
└── _stub_delete() → PipelineConfig(slug derivation)
```

## Proposed solution

Introduce a **`PipelineFacade`** in codeknow-lib that owns config, ChromaDB wiring, and repo_map.

### Interface

```python
class PipelineFacade:
    def __init__(self, graph_dir: Path | None = None, temp_dir: Path | None = None): ...

    def build(self, ssh_url: str) -> BuildResult: ...
    def delete(self, slug: str) -> DeleteResult: ...
    def search(self, query: str, top_k: int = 10, slugs: list[str] | None = None) -> HybridSearchResponse: ...
    def list_repos(self, page: int = 1, page_size: int = 50, health_check: bool = False) -> ListReposResponse: ...

    @staticmethod
    def resolve_slug(url_or_slug: str) -> str: ...

    def cleanup(self) -> None: ...  # removes graph dir, temp dir, repo_map entries
```

### What the facade hides

- PipelineConfig construction + all its attribute access
- ChromaConfig + EmbeddingConfig + ChromaStore construction
- `run_pipeline()` invocation
- `load_metadata()` / `load_graph()` calls
- `get_path()` / `get_url()` / `unregister()` calls
- `_env_path()` usage
- Filesystem path management (GRAPH_DIR, TEMP_DIR, _CODEKNOW_HOME)

### API layer after

```python
# app.py
facade = PipelineFacade(graph_dir=GRAPH_DIR, temp_dir=TEMP_DIR)

@app.post("/v1/build")
async def build_repo(body: BuildRequest):
    slug = facade.resolve_slug(body.github_ssh_url)
    result = await asyncio.to_thread(facade.build, body.github_ssh_url)
    return BuildResponse(status="ok", slug=slug, ...)

@app.delete("/v1/repos")
async def delete_repo(body: DeleteRepoRequest):
    slug = body.slug or facade.resolve_slug(body.url)
    result = await asyncio.to_thread(facade.delete, slug)
    return {"status": "ok", "slug": slug}

# models.py — no PipelineConfig import
# middleware.py — uses facade.resolve_slug(url) instead of PipelineConfig(url).slug
```

### Models + Middleware after

- `DeleteRepoRequest` no longer imports PipelineConfig. It just holds the raw fields.
- `resolve_slug()` moves to the facade.
- Middleware calls `facade.resolve_slug(url)` instead of `PipelineConfig(repo_url=url).slug`.

## Wins

- **seam between API and lib**: API never sees ChromaConfig, EmbeddingConfig, or PipelineConfig internals
- **locality**: config + chroma wiring bugs concentrate in one module
- **leverage**: 3 callers (app, models, middleware) share one module
- **delete private-symbol import** (_env_path)
- **ChromaDB wiring written once**, not duplicated across handlers

## Testing improvements

- API handler tests can inject a mock facade at the seam — no need to mock ChromaStore, EmbeddingConfig, and PipelineConfig separately
- Facade tests in codeknow-lib exercise the full build/delete/list workflow against a real (or test) filesystem
- models.py becomes testable in isolation (no pipeline dependency)

## Risks / considerations

- The facade is a wide interface (build, delete, search, list_repos, resolve_slug, cleanup). If it feels too wide, it can be split into narrower facades (e.g., `PipelineRunner` for build/delete, `SearchFacade` for search). But the key win is that the API layer sees only the facade, not the internals.
- The `cache.py` module (Redis search cache) stays in the API layer — it's an API concern, not a lib concern. The facade returns raw results, and the API layer decides whether to cache them.
- Stale `.pyc` files in `codeknow_api/__pycache__/` for deleted modules (ws_handlers.py, ws_models.py, asyncapi_spec.py, gen_client.py) should be cleaned up as part of this work.
