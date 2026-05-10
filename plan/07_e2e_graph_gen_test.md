# Plan: E2E Knowledge Graph Test

## Goal

Test the knowledge graph creation pipeline end-to-end on a real codebase (`code-test-small/`) — from file detection through graph building and clustering.

## Scope

**In scope:**
- `detect()` — file discovery
- `extract_ast()` — tree-sitter AST extraction
- `build()` — NetworkX graph construction
- `cluster()` — community detection

**Out of scope:**
- `resolve()` — no git clone needed (code is local)
- `extract_semantic()` — requires LLM
- `map_chunks()` — chunk linking
- `serve()` — query engine
- `run_pipeline()` — the orchestrator; we call stages directly

## Files

- **Delete:** `e2e/graph_gen/test_graph_gen.ts` (empty)
- **Create:** `e2e/graph_gen/test_graph_gen.py`
- **Test target:** `e2e/graph_gen/code-test-small/` (Next.js tRPC chat app)

## Approach

Call individual pipeline stage functions directly — no mocks, no subprocess, no CLI. Each test calls a shared `_run_pipeline()` helper that runs `detect → extract_ast → build → cluster` on the real `code-test-small/` codebase.

## Logging

Every stage logs counts. A dedicated `test_graph_summary` test dumps the full graph structure for diagnostics. Run with `-s` to see output.

```python
import logging
from pathlib import Path

from codeknow.extract.ast import extract_ast
from codeknow.extract.detect import detect
from codeknow.graph.build import build
from codeknow.graph.cluster import cluster, cohesion_score

CODE_TEST_SMALL = Path(__file__).parent / "code-test-small"
logger = logging.getLogger(__name__)


def _run_pipeline():
    root = CODE_TEST_SMALL
    logger.info("Running pipeline on %s", root)
    discovery = detect(root)
    logger.info(
        "detect(): %d code files, %d total",
        len(discovery["files"].get("code", [])),
        discovery.get("total_files", 0),
    )
    extraction = extract_ast(discovery["files"])
    logger.info(
        "extract_ast(): %d nodes, %d edges",
        len(extraction["nodes"]),
        len(extraction["edges"]),
    )
    G = build([extraction])
    logger.info("build(): %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    communities = cluster(G)
    logger.info("cluster(): %d communities", len(communities))
    return G, communities, discovery, extraction
```

## Tests

| # | Test | Asserts | Logging |
|---|------|---------|---------|
| 1 | `test_detect_finds_code_files` | `discovery["files"]["code"]` is non-empty | Log file count by type |
| 2 | `test_graph_summary` | Always passes — diagnostic output only | Log all node labels, edge relations, community sizes |
| 3 | `test_graph_has_file_nodes` | Graph has nodes with `file_type="code"` for discovered files | Log file nodes found |
| 4 | `test_graph_has_class_nodes` | Graph has class nodes (e.g. `IterableEventEmitter`) | Log all class-like nodes |
| 5 | `test_graph_has_function_nodes` | Graph has function/method nodes (labels with `()`) | Log function nodes |
| 6 | `test_graph_has_contains_edges` | At least one edge with `relation="contains"` | Log contains edges count |
| 7 | `test_graph_has_import_edges` | At least one edge with `relation="imports_from"` | Log import edges count |
| 8 | `test_cluster_covers_all_nodes` | Every node appears in exactly one community | Log community membership stats |
| 9 | `test_cluster_has_valid_cohesion` | Each community cohesion 0.0–1.0 | Log per-community scores |
| 10 | `test_graph_node_count_is_positive` | Graph has > 10 nodes | Log total counts |

## Verification

```bash
uv run pytest e2e/graph_gen/test_graph_gen.py -v -s
uv run ruff check e2e/graph_gen/test_graph_gen.py
uv run ruff format e2e/graph_gen/test_graph_gen.py
```

All commands use `uv run` — no bare `python`, `pytest`, or `ruff`.
