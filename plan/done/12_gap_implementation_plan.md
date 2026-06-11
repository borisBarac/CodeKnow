# Implementation Plan: Closing the 6 Gaps

## Overview

**Packages touched:**
- `codeknow-lib` — 6 files modified, 1 new file
- `codeknow-api` — 2 files modified (app.py + middleware.py)

**Not touched:** Redis cache layer stays as-is.

---

## Gap 1: Commit hash tracking

**Problem:** `download()` returns only a `Path`. No commit hash is captured anywhere in the pipeline.

**Changes in `codeknow-lib`:**

| File | Change |
|---|---|
| `git_download/downloader.py` | Add `get_commit_hash(target_path: Path) -> str` — reads `Repo(path).head.commit.hexsha` |
| `git_download/__init__.py` | Export `get_commit_hash` |
| `pipeline/types.py` | Add `commit_hash: str | None = None` field to `PipelineResult` |
| `pipeline/runner.py` | After `_resolve(config)`, call `get_commit_hash(root)` and set it on the final `PipelineResult` |

**Detail:**

```python
# git_download/downloader.py — add:
def get_commit_hash(target_path: Path) -> str:
    """Return the current HEAD commit hex SHA of the repo at *target_path*."""
    return Repo(target_path).head.commit.hexsha
```

```python
# pipeline/runner.py — after line 57 (root = _resolve(config)):
from codeknow.git_download.downloader import get_commit_hash
commit_hash = get_commit_hash(root)

# Then at the end, when constructing PipelineResult or doing replace():
result = replace(result, commit_hash=commit_hash)
```

**Tests:** Add `test_get_commit_hash` to `tests/test_git_download.py` using the existing `_make_local_remote()` fixture.

---

## Gap 2: Metadata persistence

**Problem:** `save_pipeline_result()` writes `graph.json`, `chunk_map.json`, `embed_stats.json` but no `metadata.json`.

**Changes in `codeknow-lib`:**

| File | Change |
|---|---|
| `pipeline/io.py` | Add `save_metadata(result) -> Path` and `load_metadata(output_dir) -> dict | None` |
| `pipeline/io.py` | Call `save_metadata()` at the end of `save_pipeline_result()` |
| `pipeline/__init__.py` | Export `load_metadata` |

**Detail:**

```python
# pipeline/io.py — new functions:
def save_metadata(result: PipelineResult) -> Path:
    cfg = result.config
    out = cfg.resolved_output_dir()
    metadata = {
        "github_ssh_url": cfg.repo_url,
        "slug": cfg.slug,
        "commit_hash": result.commit_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "node_count": result.graph.number_of_nodes(),
        "edge_count": result.graph.number_of_edges(),
        "community_count": len(result.communities),
    }
    path = out / "metadata.json"
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

def load_metadata(output_dir: Path) -> dict | None:
    path = output_dir / "metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
```

**Tests:** Add test to `tests/test_build.py` or a new `test_io.py` verifying metadata round-trip.

---

## Gap 3: Repo deletion after build

**Problem:** No function to remove the temp repo clone.

**Change:** No lib change needed. This is a one-liner in the API handler.

**In `codeknow-api/app.py`** (build handler):
```python
import shutil
# After run_pipeline(result):
shutil.rmtree(config.resolved_input_dir() / config.slug, ignore_errors=True)
```

---

## Gap 4: `repo_map.unregister()`

**Problem:** No way to remove a URL → path entry from the repo map.

**Changes in `codeknow-lib`:**

| File | Change |
|---|---|
| `git_download/repo_map.py` | Add `unregister(repo_url, *, store_path) -> dict[str, str]` |
| `git_download/__init__.py` | Export `unregister` |

**Detail:**

```python
# git_download/repo_map.py — add:
def unregister(
    repo_url: str,
    *,
    store_path: Path = DEFAULT_STORE_PATH,
) -> dict[str, str]:
    """Remove a URL→path entry and persist. Returns the updated mapping."""
    mapping = load(store_path=store_path)
    mapping.pop(repo_url, None)
    save(mapping, store_path=store_path)
    return mapping
```

**Tests:** Add to `tests/test_repo_map.py`.

---

## Gap 5: Multi-graph search

**Problem:** `hybrid_search()` is per-graph. The API needs to search across all indexed graphs and merge results.

**Changes in `codeknow-lib`:**

| File | Change |
|---|---|
| `vector/multi_search.py` | **NEW FILE** — `multi_graph_search()` function |
| `vector/__init__.py` | Export `multi_graph_search` |

**Detail:**

```python
# vector/multi_search.py — new file:
def multi_graph_search(
    query: str,
    *,
    graph_base_dir: Path,
    slugs: list[str] | None = None,
    n_results_per_graph: int = 5,
    total_limit: int = 20,
    embed_config: EmbeddingConfig | None = None,
    chroma_config: ChromaConfig | None = None,
) -> HybridSearchResponse:
    """Search across multiple graphs and merge results by relevance.

    If *slugs* is None, searches all subdirectories of *graph_base_dir*
    that contain a metadata.json.
    If *slugs* is provided, only searches those specific graphs.
    """
    # 1. Discover graph dirs (all subdirs with metadata.json, or filter by slugs)
    # 2. For each graph dir:
    #    collection_name = f"codeknow_{slug}"
    #    response = hybrid_search(query, output_dir=dir, collection_name=collection_name, ...)
    # 3. Merge all results into one list
    # 4. Sort by (provenance, distance, path_length) — same key as hybrid_search
    # 5. Return HybridSearchResponse with merged results, truncated to total_limit
```

**Key design decisions:**
- `slugs=None` → search all graphs (scan `./graph/*/metadata.json`)
- `slugs=["owner-repo1", "owner-repo2"]` → search only those
- Each graph searched independently with `n_results_per_graph` limit
- Final merge sorted by distance, capped at `total_limit`
- Returns same `HybridSearchResponse` schema (API doesn't need to change response format)

**In `codeknow-api/app.py`** (search handler):
```python
@app.post("/v1/search")
async def search(body: dict[str, Any]) -> dict[str, Any]:
    query = body.get("query")
    top_k = body.get("top_k", 10)
    repos = body.get("repos")  # optional list of slugs
    result = multi_graph_search(query, graph_base_dir=Path("./graph"), slugs=repos, total_limit=top_k)
    return result.model_dump()
```

---

## Gap 7: SSH URL support

**Problem:** `PipelineConfig.slug` only matches `https://github.com/...` URLs. The `resolve()` stage rejects non-matching URLs with a `ValueError`.

**Changes in `codeknow-lib`:**

| File | Change |
|---|---|
| `pipeline/config.py` | Add `_GITHUB_SSH_RE` regex. Update `slug` property to try both patterns. |
| `pipeline/stages.py` | Update `resolve()` to accept SSH URLs (remove HTTPS-only validation). |

**Detail:**

```python
# pipeline/config.py — add:
_GITHUB_SSH_RE = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)

# Update slug property:
@property
def slug(self) -> str:
    match = _GITHUB_RE.match(self.repo_url)
    if not match:
        match = _GITHUB_SSH_RE.match(self.repo_url)
    if not match:
        return self.repo_url.replace("/", "-").replace(":", "-").replace(".git", "")
    return f"{match.group('owner')}-{match.group('repo')}"
```

```python
# pipeline/stages.py — update resolve():
# Replace the strict _GITHUB_RE check with a lenient one:
# Accept any URL that looks like a git remote (HTTPS or SSH)
```

---

## API Handler Wiring (`codeknow-api`)

| Endpoint | Handler logic |
|---|---|
| `POST /v1/build` | 1. Create `PipelineConfig(repo_url, input_dir="./temp/{slug}", output_dir="./graph/{slug}")` 2. `run_pipeline(config)` 3. `shutil.rmtree("./temp/{slug}")` 4. Return 202 with slug + commit_hash |
| `POST /v1/search` | 1. Call `multi_graph_search(query, graph_base_dir="./graph", slugs=body.repos, total_limit=top_k)` 2. Return result |
| `DELETE /v1/repos` | 1. Derive slug from URL 2. `shutil.rmtree("./graph/{slug}")` 3. `ChromaStore.delete_by_slug(slug)` 4. `repo_map.unregister(url)` 5. Return 200 |
| `GET /v1/repos` | 1. Scan `./graph/*/metadata.json` 2. `load_metadata()` each 3. Return `{ repos: [...] }` |

---

## Execution Order

1. **Gap 1** — Commit hash (downloader + PipelineResult + runner)
2. **Gap 7** — SSH URL support (config + stages)
3. **Gap 2** — Metadata persistence (io.py)
4. **Gap 4** — repo_map.unregister (repo_map.py)
5. **Gap 5** — Multi-graph search (new file)
6. **Gap 3** — Repo deletion (API handler, trivial)
7. **Wire API handlers** (app.py + update middleware stubs)

Steps 1-3 are sequential (each builds on the previous). Steps 4 and 5 are independent. Step 6-7 depend on all prior steps.

---

## Test Plan

| Gap | Test file | What to test |
|---|---|---|
| 1 | `test_git_download.py` | `get_commit_hash()` returns correct SHA |
| 2 | `test_build.py` or new `test_io.py` | `save_metadata` / `load_metadata` round-trip; metadata contains expected fields |
| 4 | `test_repo_map.py` | `unregister()` removes entry, no-op on missing key |
| 7 | new `test_config.py` | SSH URL slug derivation; HTTPS still works |
