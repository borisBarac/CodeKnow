# Implementation Status (2026-06-12)

## Plan 01: Collapse hybrid search — ~60% done, 0/6 cleanup tasks completed

`GraphSearcher` class exists and is wired as the primary call path, but **no internals have been absorbed**:

| # | Task | Status |
|---|------|--------|
| 1 | Delete `vector/multi_search.py` (dead code, zero importers) | REMAINING |
| 2 | Absorb `_bfs_seeds`, `_fetch_chunks_from_store`, `sort_key`, `_discover_graph_dirs`, `_MAX_GRAPH_RESULTS` into `GraphSearcher` | REMAINING |
| 3 | Inline `read_chunk_content()` and delete `vector/_utils.py` | REMAINING |
| 4 | Absorb `weights.py` constants into `GraphSearcher` as class attrs | REMAINING |
| 5 | Remove `hybrid_search()` backward-compat wrapper (search.py:374, 2 callers) | REMAINING |
| 6 | Clean `__init__.py` — remove `hybrid_search` from `__all__` | REMAINING |

---

## Plan 02: Insulate API from lib internals — ~60% done, 3 resolved / 3 remaining

**Done:**
- `_env_path` private import removed from API layer
- `models.py` uses `PipelineFacade.resolve_slug()` instead of `PipelineConfig`
- `middleware.py` uses `PipelineFacade.resolve_slug()` instead of `PipelineConfig`

**Remaining:**

| # | Task | Status |
|---|------|--------|
| 1 | Wrap `get_path(url)` in `PipelineFacade` — `app.py:273` still imports directly from `codeknow.git_download` | REMAINING |
| 2 | Add `cleanup()` method to `PipelineFacade` | REMAINING |
| 3 | Migrate build + list_repos handlers to `PipelineFacade` — still construct `PipelineConfig`, `ChromaConfig`, `ChromaStore`, `EmbeddingConfig`, call `run_pipeline` / `load_metadata` / `load_graph` directly | REMAINING |

**Bug:** `_facade` is referenced at 5 call sites in `app.py` but **never assigned** — `NameError` at runtime.

---

## Plan 03: Deepen extraction pipeline — ~40% done, 0/6 cleanup tasks completed

`Extractor` class exists (45 lines in `extractor.py`) used only by unit tests. Production path is untouched:

| # | Task | Status |
|---|------|--------|
| 1 | Wire `pipeline/runner.py` through `Extractor` — still calls `detect()` + `extract_ast()` as separate stages | REMAINING |
| 2 | Make `extract_ast()` delegate to `Extractor.extract()` — calls internal `extract()` instead | REMAINING |
| 3 | Absorb `ast.py:extract()` (~200 lines at L865) into `Extractor` as `_extract()` | REMAINING |
| 4 | Prefix `extract_python()` / `extract_js()` with `_` (still public at L682, L690) | REMAINING |
| 5 | Migrate e2e tests to `Extractor` — still import `detect` + `extract_ast` directly | REMAINING |
| 6 | Update `pipeline/types.py` `ExtractAstFn` protocol (still expects `files: dict`) | REMAINING |

---

## Note: `read_chunk_content` extraction (refactored in `sandcastle/sequential-reviewer/1781248356770`)

- `read_chunk_content` (formerly `_read_chunk_content`) was extracted from `chroma.py` into `codeknow/vector/_utils.py`
- Both `chroma.py` and `embeddings.py` import via `from ._utils import read_chunk_content`
- `test_embed_stage.py:261` mocks `codeknow.vector.chroma.read_chunk_content` — this remains valid because `from ._utils import read_chunk_content` binds the name in chroma's module namespace at import time
- **No bug** — the mock path is correct. Plan 01 task 3 (inlining `read_chunk_content` and deleting `_utils.py`) would need to update this test patch path.
