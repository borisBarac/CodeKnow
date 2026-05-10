# PRD: Semantic Search for CodeKnow

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
| Pipeline integration | **Missing** — `run_pipeline()` never calls embedding code |
| Chunk metadata | **Partial** — ChromaDB stores `file`, `start_line`, `end_line`, `slug`. Missing `node_labels`, `community_ids` |
| Hybrid search | **Missing** — no merge layer between graph + vector |
| Search API | **Missing** — no REST endpoint, only MCP stdio |

## Design

### 1. New Pipeline Stage: `embed`

Position: after `cluster`, before `save_pipeline_result`.

```
resolve → detect → extract_ast → build_graph → map_chunks → cluster → embed → save
```

**Stage I/O:**
- Input: `PipelineResult` (graph + chunk_map + communities)
- Output: `PipelineResult` (unchanged) — side effect is upserting to ChromaDB

**Behavior:**
1. For each chunk in `chunk_map`, read source text via `read_chunk_content()`
2. Build enriched metadata per chunk via `build_chunk_metadata()`:
   - `file` (already stored by ChromaStore)
   - `start_line`, `end_line` (already stored by ChromaStore)
   - `slug` (passed via `slug` param to `store_chunk_map()`)
   - **NEW**: `node_labels` — list of node labels overlapping this chunk (via `extra_metadata`)
   - **NEW**: `community_ids` — list of community IDs for those nodes (via `extra_metadata`)
3. Embed all chunk texts via `EmbeddingConfig` (default: Ollama `qwen3-embedding:4b`)
4. Upsert to ChromaDB via `ChromaStore.store_chunk_map(slug=slug, extra_metadata=metadata)`
5. Write `embed_stats.json` to output dir (count, model, provider, duration)

**PipelineConfig additions:**
```python
no_embed: bool = False
embed_provider: str = "ollama"
embed_model: str = "qwen3-embedding:4b"
chroma_host: str | None = None
chroma_port: int | None = None
chroma_collection: str | None = None
```

**ChromaDB collection naming**: `codeknow_{owner}_{repo}` (derived from repo URL) for isolation between repos.

### 2. Metadata Enrichment

`ChromaStore.store_chunks()` already stores `file`, `start_line`, `end_line`, and `slug`. We need to enrich it with graph context via a new `extra_metadata` parameter.

**New helper: `build_chunk_metadata()`**

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

### 3. Repo Deletion (Already Implemented)

`delete_by_slug()` and `delete_by_file()` are already implemented in `ChromaStore` and defined in `VectorStore` protocol. Used as a safety valve to wipe and re-embed.

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

> **Note**: `PipelineResult` is a `@dataclass(frozen=True)`. The embed stage must use `dataclasses.replace()` to return a new instance with `embed_stats` set. Never mutate fields directly.

### 1. Schema & config additions

- [ ] **`schemas.py`** — Add `EmbedStats` model:
  ```python
  class EmbedStats(BaseModel):
      chunks_embedded: int
      provider: str
      model: str
      duration_seconds: float
  ```
- [ ] **`pipeline.py`** — Add fields to `PipelineConfig`:
  - `no_embed: bool = False`
  - `embed_provider: str = "ollama"`
  - `embed_model: str = "qwen3-embedding:4b"`
  - `chroma_host: str | None = None`
  - `chroma_port: int | None = None`
  - `chroma_collection: str | None = None`
- [ ] **`pipeline.py`** — Add `slug` property to `PipelineConfig` (avoids duplicating regex logic between `resolve()` and `embed()`):
  ```python
  @property
  def slug(self) -> str:
      match = _GITHUB_RE.match(self.repo_url)
      if not match:
          return self.repo_url.replace("/", "-").replace(".git", "")
      return f"{match.group('owner')}-{match.group('repo')}"
  ```
- [ ] **`pipeline.py`** — Add `embed_stats: dict | None = None` to `PipelineResult`
- [ ] **`pipeline.py`** — Add `"embed"` to `STAGES` list between `"cluster"` and `"serve"`
- [ ] **`pipeline.py`** — Add `STAGE_IO["embed"]` entry:
  ```python
  "embed": {
      "input": "PipelineResult (graph + chunk_map + communities)",
      "output": "PipelineResult (with embed_stats, side effect: upsert to ChromaDB)",
  },
  ```
- [ ] **`pipeline.py`** — Add `EmbedFn` protocol and `embed_fn` parameter (consistent with all other stages):
  ```python
  class EmbedFn(Protocol):
      def __call__(self, result: PipelineResult, **kwargs: Any) -> PipelineResult: ...
  ```
  Add `embed_fn: EmbedFn | None = None` parameter to `run_pipeline()`, use as `_embed = embed_fn or embed`

### 2. Metadata enrichment helper

- [ ] **New file: `src/codeknow/vector/metadata.py`** — `build_chunk_metadata()`:
  - Input: `PipelineResult` (needs graph, chunk_map, communities)
  - Use `build_reverse_index()` from `graph/chunk_mapper.py` to get `hash → [node_ids]`
  - For each chunk hash, resolve:
    - `node_labels` — labels of overlapping nodes, joined with `|`
    - `community_ids` — community IDs of those nodes, joined with `,`
    - Omit keys entirely when values are empty (no overlapping nodes)
  - Return: `dict[str, dict]` keyed by `chunk.hash`

### 3. Update ChromaStore + VectorStore for enriched metadata

> **Already done**: `slug` param, `delete_by_slug()`, `delete_by_file()` are implemented in both `vector/store.py` and `vector/chroma.py`.

- [ ] **`vector/store.py`** — Add `extra_metadata: dict[str, dict] | None = None` param to `store_chunks()` and `store_chunk_map()` in the `VectorStore` protocol
- [ ] **`vector/chroma.py`** — Add `extra_metadata: dict[str, dict] | None = None` parameter to `store_chunks()` and `store_chunk_map()`
  - In `store_chunks()`: merge `extra_metadata[chunk.hash]` into per-chunk metadata before upsert (adds `node_labels`, `community_ids`)
  - In `store_chunk_map()`: forward `extra_metadata` to `store_chunks()` unchanged
  - Keep existing `file`, `start_line`, `end_line`, `slug` as baseline

### 4. Pipeline stage: `embed()`

- [ ] **New file: `src/codeknow/vector/pipeline_stage.py`** — `embed()` function:
  - Input: `PipelineResult`
  - Guard: if `config.no_embed` → return result unchanged
  - Steps:
    1. Create `EmbeddingConfig(provider=config.embed_provider, model=config.embed_model)`
    2. Create `Embeddings` via `create_embeddings(config)`
    3. Create `ChromaConfig(host=config.chroma_host, port=config.chroma_port, collection_name=...)`
       - Collection name: `config.chroma_collection` if set, else `codeknow_{slug}` using `config.slug`
    4. Call `build_chunk_metadata(result)` to get enriched metadata
    5. Call `ChromaStore.store_chunk_map(result.chunk_map, slug=config.slug, extra_metadata=metadata)`
    6. Return `dataclasses.replace(result, embed_stats={...})`

### 5. Wire into `run_pipeline()`

- [ ] **`pipeline.py`** — In `run_pipeline()`, after building `PipelineResult` at the current return point:
  - The `PipelineResult` is already constructed at line 254. No restructuring needed.
  - Import and call `_embed(result)` before the return
  - Return the new `PipelineResult` from `dataclasses.replace()` (with `embed_stats`)
  - Update `resolve()` to use `config.slug` instead of inline slug derivation
- [ ] **`pipeline.py`** — Update `save_pipeline_result()` to write `embed_stats.json`
- [ ] **`vector/embeddings.py`** — Fix docstring line 17: change `"nomic-embed-text"` to `"qwen3-embedding:4b"`

### 6. Tests

- [ ] **`tests/test_embed_stage.py`** — Test that `embed()` is called during `run_pipeline()` with `no_embed=False`
- [ ] **`tests/test_embed_stage.py`** — Test that `embed()` is skipped when `no_embed=True`
- [ ] **`tests/test_embed_stage.py`** — Test `build_chunk_metadata()` produces correct `node_labels` and `community_ids`
- [ ] **`tests/test_embed_stage.py`** — Test `ChromaStore.store_chunks()` merges `extra_metadata` correctly (mock ChromaDB)
- [ ] **`tests/test_embed_stage.py`** — Test `delete_by_slug()` calls `collection.get/delete` with correct `where` filter (mock ChromaDB)

**Files changed:** `schemas.py`, `pipeline.py`, `vector/chroma.py`, `vector/store.py`
**Files created:** `vector/metadata.py`, `vector/pipeline_stage.py`, `tests/test_embed_stage.py`
