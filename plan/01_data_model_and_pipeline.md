# Plan 1: Data Model & Pipeline Design

## Goal
Define the core data structures (Node, Edge, ChunkMap) and the extraction pipeline that produces them.

## Context
This is the foundation — every other plan depends on these schemas being stable.
The pipeline is: `detect() → extract_ast() → extract_semantic() → build_graph() → map_chunks() → cluster() → serve()`

> **Note**: There is no separate `ingest()` stage. File walking is integrated into `detect()`.

## Checklist

- [x] Define `Node` schema: `id`, `label`, `file_type`, `source_file`, `source_location`, `chunks[]`, `community`, `end_line` (Optional)
  - `schemas.py:52-68`. Extra fields: `source_location` (str), `end_line` (Optional[int]), `model_config = {"extra": "allow"}`.
- [x] Define `Edge` schema: `source`, `target`, `relation`, `confidence`, `confidence_score`, `source_file`, `source_location`, `weight`
  - `schemas.py:71-86`. Extra fields: `source_file`, `source_location`, `weight: float = 1.0`, `model_config = {"extra": "allow"}`.
- [x] Define `ChunkMap` schema: file path → array of `{start_line, end_line, hash}`
  - `schemas.py:110`: `ChunkMap = dict[str, list[Chunk]]`
- [x] Define `Chunk` sub-schema: `file`, `start_line`, `end_line`, `hash` (SHA-256 of content)
  - `schemas.py:26-43` with `end_after_start` validator. Also `ChunkRef` (hash-only) at `schemas.py:46-49`.
- [x] Document the `chunk.hash` field as the join key between graph nodes and vector search results
  - `schemas.py:29-31` (Chunk docstring) and `chunk_mapper.py:3-5` (module docstring).
- [x] Define confidence labels: `EXTRACTED` (AST), `INFERRED` (LLM), `AMBIGUOUS` (low-score)
  - `schemas.py:20-23`: `ConfidenceLabel(str, Enum)`.
- [x] Map each pipeline stage to its input/output:
  - `detect()` → file type identification + corpus stats + .graphignore + incremental mode (`extract/detect.py`)
  - `extract_ast()` → structural entities via tree-sitter, 18+ languages, caching, cross-file calls (`extract/ast.py`)
  - `extract_semantic()` → conceptual entities via LangChain (configurable model, currently **stub** — returns empty `ExtractionResult`) (`extract/semantic.py`)
  - `build_graph()` → NetworkX graph from extracted entities, dedup, normalization, incremental merge (`graph/build.py`)
  - `map_chunks()` → link nodes to code chunk hashes (**opt-in** — not called by default, requires `map_chunks_fn` kwarg) (`graph/chunk_mapper.py`)
  - `cluster()` → Leiden community detection with Louvain fallback, oversized community splitting (`graph/cluster.py`)
  - `serve()` → MCP stdio server with 7 query tools (query_graph, get_node, get_neighbors, get_community, god_nodes, graph_stats, shortest_path) (`serve/engine.py`)
- [x] Validate schemas against existing codeknow output format for compatibility
  - All new fields Optional with defaults. `extra = "allow"` on Node/Edge. `build.py:49-50` handles legacy `links` → `edges` migration.

## Additional Schemas (implemented, not in original plan)

- **`ExtractionResult`** (`schemas.py:89-97`): nodes, edges, hyperedges, input_tokens, output_tokens
- **`FileDiscovery`** (`schemas.py:99-108`): classified files, total_files, total_words, needs_graph, warning, skipped_sensitive
- **`CommunityMap`** (`schemas.py:113-114`): `dict[int, list[str]]` — community_id → node IDs

## Pipeline Infrastructure (implemented, not in original plan)

- **`PipelineResult` dataclass** (`pipeline.py:20-28`): holds graph, communities, chunk_map, discovery, stats
- **6 Protocol types** (`pipeline.py:31-58`): `DetectFn`, `ExtractAstFn`, `ExtractSemanticFn`, `BuildGraphFn`, `MapChunksFn`, `ClusterFn`
- **`run_pipeline()` orchestrator** (`pipeline.py:103-168`): composable via dependency injection (`*_fn` kwargs)
- **`no_semantic` flag** (`pipeline.py:112`): skips semantic extraction when True
- **`_assign_communities()` helper** (`pipeline.py:171-177`): writes community IDs onto graph nodes

## Unplanned Modules (implemented, not in original plan)

- **`graph/analyze.py`** (592 lines): god_nodes, surprising_connections (cross-file/cross-community scoring), suggest_questions, graph_diff
- **`cache.py`** (181 lines): per-file extraction cache with SHA-256 keys, semantic cache, YAML frontmatter stripping
- ~~**`security.py`** (DELETED)~~ — `sanitize_label()` relocated to `serve/engine.py`. URL validation, safe_fetch, and path guards removed (not yet needed).
- **`validate.py`** (139 lines): extraction JSON validation against codeknow schema

## Key Decisions
- Graph storage is file-based (local filesystem): `graph.json` + `chunk_map.json`
- For corpora under ~50K nodes, load entire graph into NetworkX on each request (<2s)
- Chunk size: ~100 lines with 20-line overlap (configurable)
- Build on existing codeknow data model — extend nodes with `chunks[]` field, don't break existing format
- **Composable pipeline via dependency injection** — all stages injectable as `*_fn` kwargs
- **Louvain fallback** when graspologic (Leiden) not installed
- **Oversized community splitting** (>25% of graph re-clustered)
- **Incremental detection** via mtime manifest (`detect_incremental`)
- **`extra = "allow"`** on Node/Edge for forward compatibility
