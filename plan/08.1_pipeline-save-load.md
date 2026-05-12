
# IMPLEMENTED

# Phase 1: Pipeline Save/Load + E2E Tests

Prerequisite for hybrid search — ensures `graph.json` is always persisted by the pipeline
and provides a public `load_graph()` for downstream consumers.

---

## 1A. Add `load_graph()` to `pipeline/io.py`

| File | Change |
|---|---|
| `src/codeknow/pipeline/io.py` | Add `load_graph()` function (~25 lines) |

Currently `io.py` only has `save_pipeline_result()`. The only graph loading code is a private
`_load_graph()` inside `engine.py` (tied to the MCP server with `sys.exit(1)` — not reusable).

```python
def load_graph(
    output_dir: Path,
    graph_filename: str = "graph.json",
) -> nx.Graph:
```

Logic:
- Resolve `<output_dir>/<graph_filename>`
- Validate: must be `.json`, must exist
- Read JSON, parse with `json_graph.node_link_graph(data, edges="links")`
- Fall back to `json_graph.node_link_graph(data)` for older NetworkX
- Raise `FileNotFoundError` / `ValueError` (library-safe, no `sys.exit`)

Also extract `communities_from_graph()` as a public helper from `engine.py:_communities_from_graph`
(consumers of `load_graph` need to reconstruct communities from node attributes):

```python
def communities_from_graph(G: nx.Graph) -> dict[int, list[str]]:
```

---

## 1B. Move `save_pipeline_result()` into `run_pipeline()`

| File | Change |
|---|---|
| `src/codeknow/pipeline/runner.py` | Call `save_pipeline_result(result)` before return |
| `src/codeknow/cli.py` | Remove `save_pipeline_result(result)` call and its import from `run_pipeline_cli()` |

### runner.py

```python
# Before (last 2 lines):
    return _embed(result)

# After:
    result = _embed(result)
    save_pipeline_result(result)
    return result
```

### cli.py

```python
# Before:
    from .pipeline import PipelineConfig, run_pipeline, save_pipeline_result
    ...
    result = run_pipeline(config)
    save_pipeline_result(result)

# After:
    from .pipeline import PipelineConfig, run_pipeline
    ...
    run_pipeline(config)
```

---

## 1C. Update `e2e/graph_gen/test_graph_gen.py`

Current test runs a partial pipeline (detect → extract_ast → build → cluster) but never saves
or loads. Add roundtrip tests:

```python
def test_save_and_load_graph_roundtrip(pipeline, tmp_path):
    G, communities, discovery, extraction = pipeline
    from codeknow.graph.chunk_mapper import map_chunks
    from codeknow.pipeline.io import save_pipeline_result, load_graph
    from codeknow.pipeline.stages import _assign_communities
    from codeknow.pipeline.types import PipelineResult
    from codeknow.pipeline.config import PipelineConfig

    # Enrich graph with chunks and communities
    G_enriched, chunk_map = map_chunks(G, discovery["files"])
    _assign_communities(G_enriched, communities)

    # Save
    config = PipelineConfig(
        repo_url="https://github.com/test/code-test-small",
        output_dir=tmp_path,
    )
    result = PipelineResult(
        graph=G_enriched,
        communities=communities,
        chunk_map=chunk_map,
        discovery=discovery,
        stats={},
        config=config,
    )
    save_pipeline_result(result)

    # Load
    G_loaded = load_graph(tmp_path)

    # Assert roundtrip fidelity
    assert G_loaded.number_of_nodes() == G_enriched.number_of_nodes()
    assert G_loaded.number_of_edges() == G_enriched.number_of_edges()
    for nid in G_enriched.nodes():
        assert nid in G_loaded.nodes()
        original = G_enriched.nodes[nid]
        loaded = G_loaded.nodes[nid]
        assert loaded.get("label") == original.get("label")
        assert loaded.get("community") == original.get("community")
        assert loaded.get("source_file") == original.get("source_file")


def test_load_graph_reconstructs_communities(pipeline, tmp_path):
    G, communities, discovery, _ = pipeline
    from codeknow.graph.chunk_mapper import map_chunks
    from codeknow.pipeline.io import save_pipeline_result, load_graph, communities_from_graph
    from codeknow.pipeline.stages import _assign_communities
    from codeknow.pipeline.types import PipelineResult
    from codeknow.pipeline.config import PipelineConfig

    G_enriched, chunk_map = map_chunks(G, discovery["files"])
    _assign_communities(G_enriched, communities)

    config = PipelineConfig(
        repo_url="https://github.com/test/code-test-small",
        output_dir=tmp_path,
    )
    result = PipelineResult(
        graph=G_enriched,
        communities=communities,
        chunk_map=chunk_map,
        discovery=discovery,
        stats={},
        config=config,
    )
    save_pipeline_result(result)

    G_loaded = load_graph(tmp_path)
    loaded_communities = communities_from_graph(G_loaded)

    for cid, members in communities.items():
        assert set(loaded_communities.get(cid, [])) == set(members)


def test_chunk_map_roundtrip(pipeline, tmp_path):
    G, communities, discovery, _ = pipeline
    import json
    from codeknow.graph.chunk_mapper import map_chunks
    from codeknow.pipeline.io import save_pipeline_result, load_graph
    from codeknow.pipeline.stages import _assign_communities
    from codeknow.pipeline.types import PipelineResult
    from codeknow.pipeline.config import PipelineConfig

    G_enriched, chunk_map = map_chunks(G, discovery["files"])
    _assign_communities(G_enriched, communities)

    config = PipelineConfig(
        repo_url="https://github.com/test/code-test-small",
        output_dir=tmp_path,
    )
    result = PipelineResult(
        graph=G_enriched,
        communities=communities,
        chunk_map=chunk_map,
        discovery=discovery,
        stats={},
        config=config,
    )
    save_pipeline_result(result)

    # Load chunk_map.json from disk
    chunk_map_data = json.loads((tmp_path / "chunk_map.json").read_text())

    # Verify all files and hashes preserved
    for fpath, chunks in chunk_map.items():
        assert fpath in chunk_map_data
        original_hashes = {c.hash for c in chunks}
        loaded_hashes = {c["hash"] for c in chunk_map_data[fpath]}
        assert original_hashes == loaded_hashes
```

---

## 1D. Export `load_graph` and `communities_from_graph`

| File | Change |
|---|---|
| `src/codeknow/pipeline/__init__.py` | Add `load_graph`, `communities_from_graph` to imports and `__all__` |

---

## File Summary

| # | File | Action |
|---|---|---|
| 1 | `src/codeknow/pipeline/io.py` | Modify — add `load_graph()`, `communities_from_graph()` |
| 2 | `src/codeknow/pipeline/runner.py` | Modify — add save call |
| 3 | `src/codeknow/cli.py` | Modify — remove save from pipeline CLI |
| 4 | `src/codeknow/pipeline/__init__.py` | Modify — add `load_graph`, `communities_from_graph` exports |
| 5 | `e2e/graph_gen/test_graph_gen.py` | Modify — add roundtrip tests |

---

## Verification

```bash
uv run pytest e2e/graph_gen/test_graph_gen.py -v
dev-check
```
