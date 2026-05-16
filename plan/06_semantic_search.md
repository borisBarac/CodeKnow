# PRD: Semantic Search for CodeKnow

[implemented]

## Problem

The pipeline produces a rich knowledge graph (nodes, edges, communities, chunks) but retrieval is limited to **keyword matching** on node labels via the MCP engine (`graph/engine.py`). Users cannot ask natural-language questions like "how does authentication work?" and get relevant code back.

## Goal

Add a semantic search layer that:
1. Embeds all code chunks after the pipeline builds them
2. Stores embeddings in ChromaDB with enriched metadata (node labels, community IDs)
3. Provides a hybrid search API that merges vector similarity with graph structure via `chunk.hash`

## Non-Goals

- LLM-generated community summaries (can add later)
- Reranking with cross-encoders
- Multi-repo search (single repo per index)
- Streaming results

> **⚠️ IMPORTANT — Diffing Approach**
>
> The current Phase 1 implementation embeds and upserts **all** chunks on every pipeline run. This will be replaced with a **diffing approach**: on subsequent runs, compare the new chunk set against what's already in ChromaDB for that repo, and only embed/upsert **new or changed** chunks while **deleting stale** ones.
>
> `delete_by_slug()` exists now as a safety valve (nuke and re-embed), but the diffing logic will make it unnecessary for normal operation.
>
> **Design implications for diffing (keep in mind during implementation):**
> - Every chunk's `hash` field is a SHA-256 of its content — this is the natural diff key
> - The `slug` metadata field on every chunk is critical for both current deletion and future per-repo diffing
> - `store_chunks()` already uses upsert semantics (ChromaDB `upsert` with IDs = chunk hashes), so re-upserting unchanged chunks is safe but wasteful
> - Future diff stage: fetch all existing hashes for a repo via `collection.get(where={"slug": slug})`, intersect with new chunk hashes, only embed the delta

## Current State

| Component | Status |
|---|---|
| `vector/embeddings.py` | Done — Ollama + OpenRouter factory |
| `vector/chroma.py` | Done — `HttpClient`, `slug` param, `delete_by_slug()`, `delete_by_file()` |
| `vector/store.py` | Done — `VectorStore` protocol with `slug`, `delete_by_slug`, `delete_by_file` |
| `vector/_utils.py` | Done — `read_chunk_content()` |
| `vector/metadata.py` | Done — `build_chunk_metadata()` enriches chunks with `node_labels`, `community_ids` |
| `vector/pipeline_stage.py` | Done — `embed()` stage with `no_embed` guard, `embed_stats` |
| Pipeline integration | Done — `run_pipeline()` calls `embed()` after `cluster` |
| Chunk metadata | Done — ChromaDB stores `file`, `start_line`, `end_line`, `slug`, `node_labels`, `community_ids` |
| Tests | Done — `tests/test_embed_stage.py` (4 test classes, 295 lines) |
| Hybrid search | **Missing** — no merge layer between graph + vector |
| Search API | **Missing** — no REST endpoint, only MCP stdio |

## Design

### 1. Pipeline Stage: `embed`

Position: after `cluster`, before `save_pipeline_result`.

```
resolve → detect → extract_ast → [extract_semantic] → build_graph → map_chunks → cluster → embed → serve
```

**Stage I/O:**
- Input: `PipelineResult` (graph + chunk_map + communities)
- Output: `PipelineResult` (with `embed_stats`) — side effect is upserting to ChromaDB

**Behavior:**
1. For each chunk in `chunk_map`, read source text via `read_chunk_content()`
2. Build enriched metadata per chunk via `build_chunk_metadata()`:
   - `file` (already stored by ChromaStore)
   - `start_line`, `end_line` (already stored by ChromaStore)
   - `slug` (passed via `slug` param to `store_chunk_map()`)
   - `node_labels` — list of node labels overlapping this chunk (via `extra_metadata`)
   - `community_ids` — list of community IDs for those nodes (via `extra_metadata`)
3. Embed all chunk texts via `EmbeddingConfig` (default: Ollama `qwen3-embedding:4b`)
4. Upsert to ChromaDB via `ChromaStore.store_chunk_map(slug=slug, extra_metadata=metadata)`
5. Write `embed_stats.json` to output dir (count, model, provider, duration)

**PipelineConfig fields:**
```python
no_embed: bool = False
embed_provider: str = "ollama"
embed_model: str = "qwen3-embedding:4b"
chroma_host: str | None = None
chroma_port: int | None = None
chroma_collection: str | None = None
embed_base_url: str | None = None
```

**ChromaDB collection naming**: `codeknow_{owner}_{repo}` (derived from repo URL) for isolation between repos.

### 2. Metadata Enrichment

`ChromaStore.store_chunks()` stores `file`, `start_line`, `end_line`, and `slug`. Graph context is enriched via the `extra_metadata` parameter.

**Helper: `build_chunk_metadata()`** (`vector/metadata.py`)

Input: `PipelineResult`
Output: `dict[str, dict]` keyed by `chunk.hash`

For each chunk, resolve:
1. Which nodes overlap this chunk? (reverse index from `build_reverse_index()`)
2. What are those nodes' labels? → `node_labels: str` (joined with `|`)
3. What communities are those nodes in? → `community_ids: str` (joined with `,`)

> **Empty values**: if a chunk has zero overlapping nodes, omit `node_labels` and `community_ids` keys from its metadata dict entirely. Do not store empty strings — they're filterable in ChromaDB and would cause false matches.

**ChromaDB metadata per chunk (final):**
```json
{
  "file": "src/auth/session.py",
  "start_line": 42,
  "end_line": 58,
  "slug": "owner-repo",
  "node_labels": "ValidateToken|create_session",
  "community_ids": "2,5"
}
```

### 3. Repo Deletion

`delete_by_slug()` and `delete_by_file()` are implemented in `ChromaStore` and defined in `VectorStore` protocol. Used as a safety valve to wipe and re-embed.

- **`ChromaStore.delete_by_slug(slug: str) -> int`** — deletes all chunks matching `slug` metadata
- **`ChromaStore.delete_by_file(file: str) -> int`** — deletes all chunks matching `file` metadata

### 4. Hybrid Search Merge (Phase 2+)

The merge layer joins vector search results with graph context.

```
User query: "how does authentication work?"
    │
    ├─► Vector search (ChromaDB)
    │    → top-K chunks by cosine similarity
    │    → [{hash, distance, document, metadata}]
    │
    ├─► Graph lookup (from PipelineResult or graph.json)
    │    → for each chunk.hash, find:
    │      - nodes that reference this chunk
    │      - each node's community, neighbors, edges
    │
    └─► Merge on chunk.hash
         → enriched result per chunk:
           {
             chunk_text, chunk_hash, distance,
             nodes: [{id, label, community, edges}],
             communities: [{id, size, node_count}]
           }
```

---

## Phase 1 Implementation Checklist: Wire Embedding into Pipeline

> **Note**: `PipelineResult` is a `@dataclass(frozen=True)`. The embed stage uses `dataclasses.replace()` to return a new instance with `embed_stats` set.

### 1. Schema & config additions

- [x] **`schemas.py`** — `EmbedStats` model (`chunks_embedded`, `provider`, `model`, `duration_seconds`)
- [x] **`pipeline/config.py`** — `PipelineConfig` fields: `no_embed`, `embed_provider`, `embed_model`, `chroma_host`, `chroma_port`, `chroma_collection`, `embed_base_url`
- [x] **`pipeline/config.py`** — `slug` property on `PipelineConfig`
- [x] **`pipeline/types.py`** — `embed_stats: dict | None = None` on `PipelineResult`
- [x] **`pipeline/types.py`** — `"embed"` in `STAGES` list between `"cluster"` and `"serve"`
- [x] **`pipeline/types.py`** — `STAGE_IO["embed"]` entry
- [x] **`pipeline/types.py`** — `EmbedFn` protocol and `embed_fn` parameter on `run_pipeline()`

### 2. Metadata enrichment helper

- [x] **`vector/metadata.py`** — `build_chunk_metadata()` with `build_reverse_index()`, `node_labels`, `community_ids`

### 3. ChromaStore + VectorStore enriched metadata

- [x] **`vector/store.py`** — `extra_metadata` param on `store_chunks()` and `store_chunk_map()` in `VectorStore` protocol
- [x] **`vector/chroma.py`** — `extra_metadata` param on `store_chunks()` and `store_chunk_map()`

### 4. Pipeline stage: `embed()`

- [x] **`vector/pipeline_stage.py`** — `embed()` function with `no_embed` guard, `EmbeddingConfig`, `ChromaStore`, `build_chunk_metadata()`, `dataclasses.replace()`

### 5. Wire into `run_pipeline()`

- [x] **`pipeline/runner.py`** — `_embed(result)` called at end of `run_pipeline()`
- [x] **`pipeline/io.py`** — `save_pipeline_result()` writes `embed_stats.json`

### 6. Tests

- [x] **`tests/test_embed_stage.py`** — `embed()` called when enabled
- [x] **`tests/test_embed_stage.py`** — `embed()` skipped when `no_embed=True`
- [x] **`tests/test_embed_stage.py`** — `build_chunk_metadata()` produces correct metadata
- [x] **`tests/test_embed_stage.py`** — `ChromaStore.store_chunks()` merges `extra_metadata`
- [x] **`tests/test_embed_stage.py`** — `delete_by_slug()` calls correct `where` filter

---

## Known Issues (Fixed)

| Issue | File | Status |
|---|---|---|
| OpenRouter base URL had double `/embeddings` | `vector/embeddings.py` | Fixed |
| Dead no-op `try: pass except ImportError` | `vector/chroma.py` | Fixed |
| Silent chunk loss on missing source files (no logging) | `vector/chroma.py` | Fixed |
| No way to configure embedding base URL through `PipelineConfig` | `pipeline/config.py`, `vector/pipeline_stage.py` | Fixed |

## Remaining Work

- **Diffing**: Only embed new/changed chunks on subsequent runs (currently re-embeds all)
- **Hybrid search merge**: Join vector search results with graph context (Phase 2+)
- **Search API**: REST endpoint for semantic queries
- **ChromaDB `$contains` filtering**: `node_labels` stored as pipe-separated string; substring matches are imprecise (e.g. `"Auth"` matches `"Authenticate"`)

**Files changed:** `schemas.py`, `pipeline/config.py`, `pipeline/types.py`, `pipeline/runner.py`, `pipeline/io.py`, `vector/chroma.py`, `vector/store.py`, `vector/embeddings.py`
**Files created:** `vector/metadata.py`, `vector/pipeline_stage.py`, `tests/test_embed_stage.py`
