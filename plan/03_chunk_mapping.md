# Plan 3: Chunk Mapping Strategy

## Goal
Implement the `map_chunks()` pipeline stage that links graph nodes to code chunks via SHA-256 hashes — the bridge between graph retrieval and vector retrieval.

## Context
This is the core innovation. Vector search returns chunks by embedding similarity; graph returns nodes by structural traversal. The `chunk.hash` field is the join key that lets you merge both result sets.

## What codeknow provides

### Node `source_location` + `end_line` format
Every node from AST extraction carries:
```python
{
    "id": "session_validatetoken",
    "label": "ValidateToken()",
    "file_type": "code",
    "source_file": "/abs/path/to/src/auth/session.py",
    "source_location": "L42",
    "end_line": 85,
}
```
- `source_location` is set in `_extract_generic()` as `f"L{line}"` where `line = node.start_point[0] + 1` (`ast.py:314`)
- `end_line` is set from `node.end_point[0] + 1` at all `add_node()` call sites (`ast.py:366, 415, 419`)

### File discovery
`detect.py:detect()` returns files classified by type:
```python
{"files": {"code": [...], "document": [...], "paper": [...]}, ...}
```

### Node schema
`Node.end_line: Optional[int] = None` exists in `schemas.py:66` — populated by `_extract_generic()` for all class and function nodes.

## Chunk Creation Strategy

### AST-aware chunking (IMPLEMENTED)
Split source code files using tree-sitter's AST boundaries to create semantically meaningful chunks.
```python
def chunk_file_ast(path, chunk_size=100, overlap=20) -> list[Chunk]: ...
```
Implemented at `chunk_mapper.py:50-141`. Uses per-language tree-sitter configs (`_AST_CONFIGS`) and structural node type tables (`_STRUCTURAL_TYPES`).

### Naive line-based chunking (IMPLEMENTED)
For docs/markdown/non-code files:
```python
def chunk_file_linear(path, chunk_size=100, overlap=20) -> list[Chunk]: ...
```
Implemented at `chunk_mapper.py:148-186`.

### Strategy selection by file type (IMPLEMENTED)
`build_chunk_map()` at `chunk_mapper.py:189-209` dispatches:
- Code files (.py, .js, .jsx, .ts, .tsx, .mjs, .ejs) → AST-aware via `chunk_file_ast()`
- All other files → naive line-based via `chunk_file_linear()`

## Node ↔ Chunk Resolution

### Implemented: `resolve_node_chunks()` (`chunk_mapper.py:222-246`)
```python
def resolve_node_chunks(node_data: dict, chunk_map: ChunkMap) -> list[str]:
    # Finds overlapping chunks by line range: chunk.start_line <= end and chunk.end_line >= start
```
Works correctly with `end_line` populated — a 100-line function at L42 matches all chunks covering L42–L142.

### Implemented: `build_reverse_index()` (`chunk_mapper.py:273-284`)
Returns `hash → [node_ids]` for vector→graph lookup.

## Implementation History

### Phase 1: Cleanup (completed)
- [x] **ast.py**: add `seen_call_pairs` initialization before `walk_calls()` (~line 461)
- [x] **ast.py**: fix stale comment "Fallback for PHP" → "Generic fallback" (line 278)
- [x] **ast.py**: fix stale comment "JS/TS arrow functions and C# namespaces" → drop C# (line 428)
- [x] **ast.py**: remove dead `extra_walk_fn` field from `LanguageConfig` (line 56)
- [x] **detect.py**: prune `CODE_EXTENSIONS` to `{".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".ejs"}`
- [x] **detect.py**: remove `.blade.php` case in `classify_file()`
- [x] **end_line BLOCKER resolved**: `_extract_generic` `add_node()` accepts and persists `end_line`; all class/function call sites pass `node.end_point[0] + 1`

### Phase 2: Validation (completed)
- [x] `ruff check` passes
- [x] Compile check passes

### Phase 3: New implementation (completed)
- [x] **`chunk_file_ast()`** — `chunk_mapper.py:50-141` (tree-sitter AST-aware boundaries)
- [x] **`build_chunk_map()` dispatch** — routes code files → AST, others → linear (`chunk_mapper.py:189-209`)
- [x] **`map_chunks` wired as default** — `pipeline.py:126,132,152` (always runs, no longer conditional)
- [x] **`save_pipeline_result()`** — writes `graph.json` + `chunk_map.json` to `graph-out/` (`pipeline.py:191-215`)

## Checklist

- [x] **Populate `end_line` on AST-extracted nodes** — `add_node()` accepts `end_line` (`ast.py:306`); class/function call sites pass `node.end_point[0] + 1` (`ast.py:366, 415, 419`)
- [x] **Implement naive line chunking** — `chunk_file_linear()` at `chunk_mapper.py:148-186`
- [x] **Generate SHA-256 hashes** — `_hash_content()` at `chunk_mapper.py:46-47`
- [x] **Build in-memory ChunkMap** — `build_chunk_map()` at `chunk_mapper.py:189-209`
- [x] **Implement node↔chunk resolution** — `resolve_node_chunks()` at `chunk_mapper.py:222-246`
- [x] **Write `chunks[]` into each node** — `map_chunks()` at `chunk_mapper.py:249-270`
- [x] **Implement reverse lookup** — `build_reverse_index()` at `chunk_mapper.py:273-284`
- [x] **Implement forward lookup** — via node `chunks: list[ChunkRef]` field
- [x] **Handle edge cases:**
  - Nodes with no `source_file` → `chunks = []` (`chunk_mapper.py:232`)
  - Nodes spanning multiple chunks → all overlapping hashes linked
  - Chunks referenced by multiple nodes → reverse lookup returns all
  - Empty files → single chunk with hash of `""` (`chunk_mapper.py:90`)
  - Small files (< chunk_size) → single chunk = whole file
- [x] **Implement AST-aware chunking** — `chunk_file_ast()` at `chunk_mapper.py:50-141`
- [x] **Implement chunking dispatch** — `build_chunk_map()` routes by extension (`chunk_mapper.py:202-206`)
- [x] **Wire `map_chunks()` as default in `pipeline.py`** — imported as `_default_map_chunks` at `pipeline.py:126`, always runs (`pipeline.py:132,152`)
- [x] **Serialize `chunk_map.json` to disk** — `save_pipeline_result()` at `pipeline.py:191-215`
- [ ] **Serialize `chunk_index.json` to disk** — `build_reverse_index()` produces in-memory dict only. No code writes it to `graph-out/chunk_index.json`.
- [ ] **Coordinate hash with vector pipeline** — extract chunking logic into shared module so both graph and vector pipelines produce identical hashes.

## Hash Contract

The `chunk.hash` field is the join key between the graph pipeline and the vector search pipeline. Both systems **must** produce identical hashes for the same content.

**Current implementation** (`chunk_mapper.py:46-47`):
```python
def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```
This hashes UTF-8 re-encoded text (after `read_text(encoding="utf-8", errors="replace")` + `splitlines(keepends=True)` + `join`).

**Contract requirements:**
1. **Same chunk boundaries** — both pipelines must use the same chunking config
2. **Same input bytes** — currently UTF-8 re-encoded text, NOT raw file bytes. Both pipelines must use the same encoding/normalization.
3. **Same hash function** — `sha256` in both pipelines
4. **Shared configuration** — chunking parameters should live in a single shared module

## Local File Layout After Build
```
./graph-out/
  graph.json          ← nodes with chunks[] populated
  chunk_map.json      ← file → [{start_line, end_line, hash}]
  chunk_index.json    ← hash → [node_ids] (reverse lookup) [NOT YET WRITTEN]
  cache/              ← per-file extraction cache
  cost.json           ← cumulative token tracking
  manifest.json       ← file mtimes for incremental updates
  converted/          ← office file conversions
```

## Key Decisions
- **AST-aware chunking** for code files, **naive line chunking** for docs — best semantic quality
- Chunk hash = `sha256(utf8_encoded_text)` — must match vector pipeline exactly
- 20-line overlap ensures nodes near chunk boundaries are still linked
- `chunk_map.json` is the forward index (file → chunks), `chunk_index.json` is the reverse (hash → nodes)
- `end_line` populated on all class/function nodes — non-breaking (old nodes without it fall back to single-line)
- Reuse existing tree-sitter parsers from `extract/ast.py` — no new dependencies

## Integration Point
This runs as a pipeline stage between `build_graph()` and `cluster()`:
```
detect() → extract_ast() → extract_semantic() → build_graph() → map_chunks() → cluster() → serve()
```
Or equivalently, as a post-build pass that enriches an existing `graph.json`.
