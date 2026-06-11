# Candidate 3: Deepen the extraction pipeline for testability

**Strength:** Strong (top recommendation)
**Dependency category:** in-process

## Files involved

| File | Lines | Role | Tests |
|---|---|---|---|
| `codeknow/extract/ast.py` | 1076 | AST extraction (largest file in repo) | **Zero** |
| `codeknow/extract/detect.py` | 440 | File discovery + classification | **Zero** |
| `codeknow/extract/__init__.py` | 1 | Empty docstring | — |

### Downstream dependents

| File | Lines | What it uses |
|---|---|---|
| `codeknow/graph/build.py` | 239 | `ExtractionResult` (nodes + edges dicts) |
| `codeknow/pipeline/runner.py` | 95 | `extract_ast()` as a pipeline stage |
| `codeknow/pipeline/chunk_stage.py` | 72 | Graph nodes (needs extraction to have run first) |
| `codeknow/pipeline/embed_stage.py` | 67 | ChunkMap (needs extraction to have run first) |
| `codeknow/cache/file.py` | 188 | Caches `ExtractionResult` |
| `codeknow/cache/redis.py` | 164 | Caches `ExtractionResult` |
| `e2e/graph_gen/test_graph_gen.py` | 270 | Calls `extract_ast()` end-to-end |

## Current structure of ast.py

### Key functions (in dependency order)

```
extract_ast(repo_path, output_dir?)    — pipeline wrapper (line ~990)
  └── extract(repo_path, output_dir?) — main entry point (line ~890)
        ├── _DISPATCH[file_ext]        — routes to extract_python or extract_js
        ├── extract_python(path)       — 300-line AST walk for Python (line ~200)
        ├── extract_js(path)           — 200-line AST walk for JS/TS (line ~500)
        ├── _resolve_cross_file_imports(nodes, edges, repo_path) — 140 lines (line ~700)
        │     └── Re-parses Python files to find import statements
        │     └── Creates class-level "uses" edges
        ├── ID remapping (lines 940-958) — silently continues on ValueError
        └── cache orchestration (load_cached / save_cached)
  └── collect_files(repo_path)        — file walk with graphignore (line ~850)
        ├── _load_graphignore(repo_path)
        └── _is_ignored(rel_path, patterns)
```

### Language configs

```python
_PYTHON_CONFIG = LanguageConfig(
    language_fn=tree_sitter_python,
    node_queries=[...],
    edge_queries=[...],
    extra_walk=_extract_python_rationale,  # docstrings + rationale comments
)

_JS_CONFIG = LanguageConfig(...)
_TS_CONFIG = LanguageConfig(...)
```

### Imports

**Internal:**
- `codeknow.cache.load_cached`, `codeknow.cache.save_cached` — caching
- `codeknow.extract.detect._is_ignored`, `codeknow.extract.detect._load_graphignore` — file filtering

**External:**
- `tree_sitter`, `tree_sitter_python`, `tree_sitter_javascript`, `tree_sitter_typescript` — AST parsing
- `importlib` — dynamic tree-sitter loading
- `re`, `os`, `pathlib.Path`, `dataclasses.dataclass`

## Current structure of detect.py

### Key functions

```
detect(repo_path) → FileDiscovery          — main entry point (line ~250)
  ├── classify_file(path) → FileType       — code / document / other
  ├── _is_sensitive(path) → bool           — secrets, env files, keys
  ├── _looks_like_paper(path) → bool       — academic paper heuristics
  ├── _is_noise_dir(name) → bool           — node_modules, .git, __pycache__
  ├── _load_graphignore(repo_path)          — .graphignore patterns
  └── _is_ignored(rel_path, patterns)       — fnmatch filtering

detect_incremental(repo_path, manifest) → FileDiscovery  — incremental detection
  ├── load_manifest(repo_path)              — reads .ck-manifest.json
  └── save_manifest(repo_path, manifest)    — writes .ck-manifest.json

count_words(path) → int                     — word count for documents
docx_to_markdown(path) → str                — .docx conversion (uses openpyxl)
```

### Bug: `total_words` never incremented

In `detect()`, `total_words` is initialized to 0 (line ~283) and checked at the end (line ~351) to decide `needs_graph`, but nothing in the file walk loop increments it. This means `needs_graph` is always `False` and corpus threshold warnings are always based on 0 words.

## Why zero tests = wrong module shape

The skill's principle: **"The interface is the test surface."** If 1516 lines have zero tests, the module shape doesn't present a testable interface. The current interface is a collection of internal functions that callers must wire together correctly:

```python
# What the pipeline runner must do:
files = detect(repo_path)                    # from detect.py
result = extract_ast(repo_path, output_dir)  # from ast.py
# extract_ast internally calls extract(), which calls:
#   collect_files() → _is_ignored() from detect.py
#   _DISPATCH[ext] → extract_python() or extract_js()
#   _resolve_cross_file_imports()
#   cache load/save
```

Each of these internal steps is a potential failure point, but none are independently testable because they're hidden inside a monolithic function chain. The e2e test (`test_graph_gen.py`) exercises the full pipeline, but that requires a real codebase fixture and doesn't isolate extraction failures from graph-building failures.

## Proposed solution

Deepen into an **`Extractor`** module with a single testable seam.

### Interface

```python
class Extractor:
    def __init__(self, cache_dir: Path | None = None): ...

    def extract(self, repo_path: Path) -> ExtractionResult: ...
```

### What becomes internal

- `detect()` — file discovery (from detect.py)
- `classify_file()`, `_is_sensitive()`, `_looks_like_paper()`, `_is_noise_dir()`
- `collect_files()` + `_load_graphignore()` + `_is_ignored()`
- `extract_python()`, `extract_js()` — per-language AST walkers
- `_resolve_cross_file_imports()` — cross-file import resolution
- `_extract_python_rationale()` — docstring/rationale extraction
- Cache orchestration (load_cached / save_cached)
- ID remapping logic
- `LanguageConfig` dataclass and per-language configs
- All tree-sitter setup

### What stays at the seam

- `Extractor.extract(repo_path) → ExtractionResult`

### Internal structure (not exposed)

```
Extractor
├── _discover_files(repo_path) → FileDiscovery   [was detect()]
├── _walk_ast(file_path, lang_config) → nodes, edges  [was extract_python/js]
├── _resolve_imports(nodes, edges, repo_path) → nodes, edges  [was _resolve_cross_file_imports]
├── _remap_ids(nodes, edges) → nodes, edges      [was inline in extract()]
└── _cache layer                                  [was load_cached/save_cached]
```

### The `detect` concern

`detect.py` contains both file discovery (used by extraction) and document processing helpers (`count_words`, `docx_to_markdown`). After deepening:

- File discovery (`detect()`, `classify_file()`, `_is_ignored()`, etc.) becomes internal to `Extractor`
- Document helpers (`count_words`, `docx_to_markdown`) move to a separate utility or stay in detect.py as public functions
- The `FileType` enum stays public since it's used in schemas

## Testing strategy

### New tests at the interface

```python
# test_extractor.py

def test_extracts_python_classes(tmp_path):
    """Fixture: single Python file with a class definition."""
    (tmp_path / "main.py").write_text("class Foo:\n    pass\n")
    result = Extractor().extract(tmp_path)
    assert any(n["label"] == "Foo" and n["type"] == "class" for n in result.nodes)

def test_extracts_cross_file_imports(tmp_path):
    """Fixture: two Python files, one imports from the other."""
    (tmp_path / "a.py").write_text("from b import Bar\nclass Foo:\n    x = Bar()")
    (tmp_path / "b.py").write_text("class Bar:\n    pass\n")
    result = Extractor().extract(tmp_path)
    assert any(e["relation"] == "imports_from" for e in result.edges)

def test_respects_graphignore(tmp_path):
    """Fixture: .graphignore excluding vendor/."""
    (tmp_path / ".graphignore").write_text("vendor/\n")
    (tmp_path / "vendor" / "lib.py").write_text("class Vendor:\n    pass")
    (tmp_path / "main.py").write_text("class Main:\n    pass")
    result = Extractor().extract(tmp_path)
    assert not any(n["label"] == "Vendor" for n in result.nodes)

def test_caches_result(tmp_path, cache_dir):
    """Second call loads from cache, doesn't re-parse."""
    (tmp_path / "main.py").write_text("class Foo:\n    pass")
    ext = Extractor(cache_dir=cache_dir)
    r1 = ext.extract(tmp_path)
    r2 = ext.extract(tmp_path)
    assert r1 == r2
```

### Old tests

The existing e2e test `test_graph_gen.py` calls `extract_ast()` which would delegate to `Extractor.extract()`. This test continues to pass unchanged.

## Wins

- **interface is the test surface**: test extraction end-to-end against fixture repos
- **locality**: AST parsing bugs concentrate behind one seam
- **leverage**: pipeline runner, e2e tests, cache layer all use one interface
- **internal restructure survives**: tests describe behaviour, not implementation. Can refactor language walkers, add new languages, change caching strategy — tests survive.
- **fix the `total_words` bug**: once tests exist at the interface, bugs like the `total_words` never-incremented become visible.

## Risks / considerations

- **Scope**: This is the largest refactoring candidate (1516 lines). It should be done incrementally:
  1. Create `Extractor` class that delegates to existing functions
  2. Write tests at the new interface
  3. Move functions internal one at a time
  4. Delete old public functions
- **tree-sitter version sensitivity**: `_check_tree_sitter_version()` in ast.py handles version compatibility. This must move into the Extractor's init, not be lost.
- **`detect()` is also called from the pipeline runner independently**: After deepening, the pipeline runner should call `Extractor.extract()` which internally calls detect. If detect needs to remain callable independently (e.g., for incremental detection), it can stay as a public function in detect.py alongside the document helpers.
- **`CODE_EXTENSIONS`, `IMAGE_EXTENSIONS`, `PAPER_EXTENSIONS`** from detect.py are imported by `graph/analyze.py` for file classification. These constants should remain public (they're not part of extraction logic — they're shared classification data).

## Why this is the top recommendation

1516 lines with zero tests is the single highest-risk gap. Extraction is the foundation — graph, chunks, embeddings, search all depend on its output. Without a testable seam, every change to extraction is a guess. Deepening here makes every subsequent deepening safer because:

1. Tests at the interface protect all downstream modules from extraction regressions
2. The internal restructure (language walkers, cross-file resolution, caching) becomes safe to refactor with tests guarding it
3. Bug fixes (like `total_words`) become possible to verify

## Implementation status

**Overall: ~40% done.** `Extractor` class exists as a thin facade; the pipeline runner and e2e tests still use the old two-stage API.

### Done

- `Extractor` class created at `extract/extractor.py` (45 lines) with `extract()` and `discover()` methods
- `__init__.py` exports only `Extractor` (old functions not re-exported at package level)
- 19 unit tests in `test_extractor.py` (8 test classes: Python, cross-file, graphignore, JS, discovery, rationale, caching, word count)
- `total_words` bug fixed in `detect.py` — now correctly incremented via `total_words += count_words(p)`
- `_check_tree_sitter_version()` called in `Extractor.__init__()`

### Remaining cleanup

The core problem: **two parallel entry points** exist. The pipeline runner + e2e tests call `detect()` then `extract_ast()` as separate stages. `Extractor.extract()` wraps both but only unit tests use it.

| # | Task | Effort | Details |
|---|------|--------|---------|
| 1 | **Wire `pipeline/runner.py` through `Extractor`** | Medium | Currently calls `detect()` (L43) then `extract_ast(files_dict)` (L51) as separate stages. Runner also needs discovery data for stats/chunk_map (L71, L80). Options: (a) make `Extractor.extract()` return richer data including discovery, or (b) have runner call `Extractor.discover()` + `Extractor.extract()` instead of raw functions. |
| 2 | **Make `extract_ast()` delegate to `Extractor.extract()`** | Small | `ast.py:1063` calls local `extract()` directly. Should route through `Extractor`. Note: `extract_ast()` takes `files` dict from detect, not a repo path — the signature gap must be reconciled. |
| 3 | **Absorb `ast.py:extract()` into `Extractor`** | Medium | The real orchestrator at L865 (~200 lines). Currently `Extractor.extract()` just delegates to it. Make it a private method `_extract()` on the class. |
| 4 | **Make `extract_python()`, `extract_js()` private** | Small | L682, L690. Only called internally by `extract()`. Prefix with `_` or move into class. |
| 5 | **Migrate e2e tests to `Extractor`** | Medium | `e2e/graph_gen/test_graph_gen.py` and `test_hybrid_search.py` import `detect` + `extract_ast` directly from submodules. Switch to `Extractor`. |
| 6 | **Update `pipeline/types.py`** | Small | `ExtractAstFn` protocol expects `files: dict` (detect output format). If canonical API becomes `Extractor.extract(repo_path)`, this signature changes. |
| — | **Skip `detect.py`** — `detect()` called from 4 places; extension constants legitimately shared with `graph/analyze.py`. Keep as-is. | — | — |
