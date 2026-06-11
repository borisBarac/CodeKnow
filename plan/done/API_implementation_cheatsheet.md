# CodeKnow-Lib Cheat Sheet for API Implementation

## 4 Endpoints → Library Module Mapping

---

### 1. `POST /v1/build` — Build a graph from a GitHub repo

| Step | What you need | Library module | Key function/class |
|---|---|---|---|
| Download repo to `./temp` | Clone via SSH URL | `git_download.downloader` | `download(repo_url, target_path) -> Path` |
| Track the repo mapping | URL → local path registry | `git_download.repo_map` | `register(url, path)`, `get_path(url)` |
| Get commit hash | Read HEAD of cloned repo | `git_download.downloader` | `get_commit_hash(target_path) -> str` |
| Detect/classify files | Walk the tree, filter | `extract.detect` | `detect(root) -> FileDiscovery` |
| AST extraction | Tree-sitter parsing | `extract.ast` | `extract_ast(files) -> dict` |
| Build NetworkX graph | Merge extractions | `graph.build` | `build(extractions) -> nx.Graph` |
| Map chunks to nodes | AST-aware chunking | `graph.chunk_mapper` | `map_chunks(graph, files) -> (Graph, ChunkMap)` |
| Cluster communities | Leiden/Louvain | `graph.cluster` | `cluster(G) -> dict[int, list[str]]` |
| Embed chunks to ChromaDB | Vector embeddings | `vector.pipeline_stage` | `embed(result) -> PipelineResult` |
| Save graph + metadata to `./graph` | Serialize to disk | `pipeline.io` | `save_pipeline_result(result) -> Path` (auto-calls `save_metadata`) |
| **Or: run the whole thing** | Full pipeline | `pipeline.runner` | `run_pipeline(config) -> PipelineResult` (returns `commit_hash`) |
| Pipeline config | Configure everything | `pipeline.config` | `PipelineConfig(repo_url, output_dir=...)` (supports SSH URLs) |
| Delete repo after build | Remove temp clone | stdlib `shutil.rmtree` | `shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)` |

**Note:** `run_pipeline()` now captures `commit_hash` automatically via `get_commit_hash()` and returns it on `PipelineResult.commit_hash`. `save_pipeline_result()` auto-generates `metadata.json` alongside graph files.

**Note:** `PipelineConfig` now supports SSH URL format (`git@github.com:owner/repo.git`) for slug derivation. Override `output_dir` param or use `CODEKNOW_OUTPUT_DIR` / `CODEKNOW_GRAPH_DIR` env vars.

---

### 2. `POST /v1/search` — Search across multiple graphs

| Step | What you need | Library module | Key function/class |
|---|---|---|---|
| Multi-graph search | Search across all indexed graphs | `vector.multi_search` | `multi_graph_search(query, graph_base_dir, slugs, total_limit) -> HybridSearchResponse` |
| Single-graph search | Hybrid vector + graph search | `vector.search` | `hybrid_search(query, output_dir, collection_name, n_results) -> HybridSearchResponse` |
| Load a graph from disk | Read graph.json | `pipeline.io` | `load_graph(path) -> nx.Graph` |
| Graph query engine | Node search, BFS/DFS | `graph.engine` | `_find_node(G, label)`, `_bfs(G, seeds, depth)`, `_subgraph_to_text(G, nodes, edges)` |
| Vector store | ChromaDB client | `vector.chroma` | `ChromaStore(config, embeddings).search(query, n_results)` |
| Embedding setup | Create embedder | `vector.embeddings` | `create_embeddings(EmbeddingConfig()) -> Embeddings` |

**Multi-graph search:** `multi_graph_search()` discovers all graph subdirectories (via `metadata.json` presence), runs `hybrid_search()` per graph, merges and sorts results by `(provenance, distance, path_length)`, and returns a single `HybridSearchResponse`. Pass `slugs=["owner-repo1", ...]` to limit to specific repos, or `slugs=None` to search all.

**Response schema:** `HybridSearchResponse` from `schemas.py` — `query, vector_hits, graph_expanded, results: list[HybridSearchResult]` where each result has `chunk_hash, file, start_line, end_line, content, distance, node_labels, community_ids, provenance, graph_path, slug`.

---

### 3. `DELETE /v1/repos` — Delete a graph

| Step | What you need | Library module | Key function/class |
|---|---|---|---|
| Find graph files | Locate on disk | `git_download.repo_map` | `get_path(repo_url) -> Path or None` |
| Delete graph files | Remove from `./graph` | stdlib `shutil.rmtree` | `shutil.rmtree(GRAPH_DIR / slug, ignore_errors=True)` |
| Delete temp clone | Remove from `./temp` | stdlib `shutil.rmtree` | `shutil.rmtree(TEMP_DIR / slug, ignore_errors=True)` |
| Delete vector embeddings | Remove from ChromaDB | `vector.chroma` | `ChromaStore.delete_by_slug(slug)` |
| Unregister repo mapping | Remove from registry | `git_download.repo_map` | `unregister(repo_url) -> dict[str, str]` |

**Note:** `unregister()` is now available. It removes the URL → path entry from the repo map and returns the updated mapping.

---

### 4. `GET /v1/repos` — List all indexed repos

| Step | What you need | Library module | Key function/class |
|---|---|---|---|
| Scan graph directories | Iterate `./graph/*` | stdlib `Path.iterdir()` | Check for `metadata.json` in each subdir |
| Read metadata | Load metadata.json | `pipeline.io` | `load_metadata(output_dir) -> dict | None` |
| List registered repos | URL → path mapping | `git_download.repo_map` | `list_all() -> dict[str, str]` |
| Check graph health | Verify graph files | `pipeline.io` | `load_graph(path) -> nx.Graph` (will raise if corrupt) |

**Note:** Each graph's output directory now contains `metadata.json` (auto-generated by `save_pipeline_result()`), which includes `github_ssh_url`, `slug`, `commit_hash`, `built_at`, `node_count`, `edge_count`, `community_count`. The `GET /v1/repos` endpoint scans `GRAPH_DIR/*/metadata.json` and returns them.

---

## File Store Layout

```
./temp/                          # Temporary repo clones (deleted after build)
  owner-repo/                    # git clone target

./graph/                         # Persistent graph storage
  owner-repo/
    graph.json                   # NetworkX node-link format
    chunk_map.json               # File → chunk mapping
    embed_stats.json             # Embedding statistics
    metadata.json                # Auto-generated: github_ssh_url, slug, commit_hash, built_at, node_count, edge_count, community_count
```

**Path config via env vars:**
- `CODEKNOW_GRAPH_DIR` — graph output base (default `./graph`)
- `CODEKNOW_TEMP_DIR` — temp clone base (default `./temp`)
- `CODEKNOW_OUTPUT_DIR` — override `PipelineConfig` output dir

---

## Redis Usage (for caching loaded graphs)

| Capability | Module | Function |
|---|---|---|
| Redis cache store | `cache.redis` | `AsyncRedisCacheStore(client, graph_id, ttl)` |
| Key scheme | `cache.redis` | `ck:cache:{graph_id}:{hash}` (data), `ck:index:{graph_id}` (index) |
| Factory | `cache.factory` | `get_cache_store(backend="redis", redis_client=client)` |
| Load graph into Redis | **NOT AUTOMATED** | You'll need to load `graph.json` → `nx.Graph` → serialize into Redis manually |

**Note:** The existing Redis cache is for **extraction result caching** (per-file AST results), not for graph storage. You'll likely want a separate Redis key scheme for loaded graphs (e.g., `ck:graph:{slug}` → JSON blob of the full graph).

---

## Module Reference (Quick Lookup)

### `git_download/`
- `downloader.download(repo_url, target_path) -> Path` — Clone or pull a repo
- `downloader.is_cloned(target_path) -> bool` — Check if `.git/` exists
- `downloader.get_commit_hash(target_path) -> str` — Return HEAD commit hex SHA
- `repo_map.register(url, path)` — Add URL → path entry
- `repo_map.get_path(url) -> Path | None` — Look up local path
- `repo_map.get_url(path) -> str | None` — Reverse lookup
- `repo_map.unregister(repo_url) -> dict[str, str]` — Remove entry, return updated mapping
- `repo_map.list_all() -> dict[str, str]` — All registered repos
- `repo_map.load() / save(mapping)` — Low-level read/write

### `extract/`
- `detect.detect(root) -> FileDiscovery` — Walk tree, classify files, check corpus health
- `detect.classify_file(path) -> FileType | None` — Single file classification
- `ast.extract_ast(files) -> dict` — Pipeline-stage AST extraction
- `ast.extract(paths) -> dict` — Lower-level multi-file extraction with caching
- `semantic.extract_semantic(files) -> ExtractionResult` — **STUB** (returns empty)

### `graph/`
- `build.build_from_json(extraction) -> nx.Graph` — Single extraction → graph
- `build.build(extractions) -> nx.Graph` — Merge multiple extractions
- `build.build_merge(chunks, graph_path) -> nx.Graph` — Incremental build
- `chunk_mapper.map_chunks(graph, files) -> (Graph, ChunkMap)` — Pipeline stage
- `chunk_mapper.build_reverse_index(graph) -> dict[str, list[str]]` — hash → node IDs
- `cluster.cluster(G) -> dict[int, list[str]]` — Community detection
- `cluster.cohesion_score(G, nodes) -> float` — Intra-community density
- `analyze.god_nodes(G, top_n)` — Most-connected entities
- `analyze.surprising_connections(G, communities)` — Non-obvious cross-file edges
- `analyze.graph_diff(G_old, G_new)` — Compare two graph snapshots
- `engine._find_node(G, label)` — Fuzzy node search
- `engine._bfs(G, seeds, depth)` — BFS subgraph expansion
- `engine._subgraph_to_text(G, nodes, edges)` — Render for LLM context

### `vector/`
- `embeddings.EmbeddingConfig` — Settings (provider, model, base_url)
- `embeddings.create_embeddings(config) -> Embeddings` — Factory
- `chroma.ChromaConfig` — Host, port, collection name
- `chroma.ChromaStore(config, embeddings)` — ChromaDB backend
- `chroma.ChromaStore.search(query, n_results) -> list[SearchResult]`
- `chroma.ChromaStore.store_chunk_map(chunk_map, slug, extra_metadata)`
- `chroma.ChromaStore.delete_by_slug(slug)` — Remove all chunks for a repo
- `search.hybrid_search(query, output_dir, collection_name) -> HybridSearchResponse`
- `multi_search.multi_graph_search(query, graph_base_dir, slugs, total_limit) -> HybridSearchResponse` — Search across multiple graphs, merge and rank results
- `pipeline_stage.embed(result) -> PipelineResult` — Full embed pipeline stage
- `metadata.build_chunk_metadata(result) -> dict[str, dict]` — Attach node labels/communities to chunks

### `pipeline/`
- `config.PipelineConfig` — Dataclass: `repo_url`, `output_dir`, `no_embed`, chroma settings, etc. Supports both HTTPS and SSH GitHub URLs.
- `config.PipelineConfig.slug` — Property: extracts `owner-repo` from URL (HTTPS or SSH format)
- `runner.run_pipeline(config) -> PipelineResult` — Orchestrate all 8 stages; captures `commit_hash` before cleanup
- `io.load_graph(path) -> nx.Graph` — Read graph.json from disk
- `io.load_metadata(output_dir) -> dict | None` — Read metadata.json (returns `None` if missing)
- `io.save_metadata(result) -> Path` — Write metadata.json (auto-called by `save_pipeline_result`)
- `io.save_pipeline_result(result) -> Path` — Write graph.json + chunk_map.json + embed_stats.json + metadata.json
- `io.communities_from_graph(G) -> dict[int, list[str]]` — Extract community attrs
- `types.PipelineResult` — Frozen dataclass: `graph, communities, chunk_map, discovery, stats, config, commit_hash, embed_stats, graph_path`
- `types.STAGES` — `["resolve", "detect", "extract_ast", "build_graph", "map_chunks", "cluster", "embed"]`

### `cache/`
- `protocol.CacheStore` — Async Protocol: `get`, `store`, `has`, `delete`, `evict`, `close`
- `file.FileCacheStore` — JSON files in `graph-out/cache/`
- `redis.AsyncRedisCacheStore(client, graph_id, ttl)` — Redis-backed
- `factory.get_cache_store(backend) -> CacheStore` — Pick backend via `"file"` or `"redis"`
- `hash.file_hash(path, root) -> str` — SHA-256 content hash

### `schemas.py`
- `Chunk(file, start_line, end_line, hash)` — Code chunk with SHA-256
- `ChunkRef(hash)` — Lightweight chunk reference
- `Node(id, label, file_type, source_file, source_location, chunks, community)`
- `Edge(source, target, relation, confidence, confidence_score, source_file, weight)`
- `ExtractionResult(nodes, edges, hyperedges, input_tokens, output_tokens)`
- `FileDiscovery(files, total_files, total_words, needs_graph, warning, skipped_sensitive)`
- `EmbedStats(chunks_embedded, provider, model, duration_seconds)`
- `HybridSearchResult(chunk_hash, file, start_line, end_line, content, distance, node_labels, community_ids, provenance, graph_path, slug)`
- `HybridSearchResponse(query, vector_hits, graph_expanded, results)`
- `ChunkMap = dict[str, list[Chunk]]`
- `CommunityMap = dict[int, list[str]]`

### `validate.py`
- `validate_extraction(data) -> list[str]` — Returns error strings (empty = valid)
- `assert_valid(data)` — Raises `ValueError` if invalid

---

## Gaps (things that still need new code)

1. **Redis graph loading** — Cache layer is for extractions, not full graphs. Need a new Redis key scheme for loaded graphs (e.g., `ck:graph:{slug}` → JSON blob of the full graph). The existing Redis cache (`AsyncRedisCacheStore`) stores per-file AST extraction results, not graph data.
