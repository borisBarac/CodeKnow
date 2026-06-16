# ruff: noqa: S101, SLF001

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

EVALS_DIR = Path(__file__).resolve().parent.parent
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))


def test_index_health_returns_false_when_chroma_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    support = importlib.import_module("support.fastify_eval_support")
    graph_dir = tmp_path / "fastify-graph"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}", encoding="utf-8")
    (graph_dir / "chunk_map.json").write_text(
        json.dumps(
            {
                f"/repo/{required}": [
                    {"hash": f"h-{i}", "file": f"/repo/{required}"} for i in range(50)
                ]
                for i, required in enumerate(support.REQUIRED_INDEX_FILES)
            }
        ),
        encoding="utf-8",
    )

    class UnreachableStore:
        def count(self) -> int:
            msg = "Chroma is down"
            raise ConnectionError(msg)

    def make_unreachable_store() -> UnreachableStore:
        return UnreachableStore()

    monkeypatch.setattr(support, "GRAPH_DIR", graph_dir)
    monkeypatch.setattr(support, "_make_store", make_unreachable_store)

    assert support._index_is_healthy() is False


def test_rebuild_preflights_before_resetting_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = importlib.import_module("build_fastify_graph")
    reset_graph = Mock()
    reset_chroma = Mock()

    monkeypatch.setattr(build, "check_chroma", Mock(side_effect=ConnectionError))
    monkeypatch.setattr(build, "reset_graph_dir", reset_graph)
    monkeypatch.setattr(build, "reset_chroma_collection", reset_chroma)

    with pytest.raises(ConnectionError):
        build._rebuild_index()

    reset_graph.assert_not_called()
    reset_chroma.assert_not_called()


def test_fastify_eval_uses_langchain_grep_search_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eval_fastify = importlib.import_module("eval_fastify")

    class FakeGraphSearcher:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(eval_fastify, "_make_store", Mock(return_value=object()))
    monkeypatch.setattr(eval_fastify, "GraphSearcher", FakeGraphSearcher)

    tools = eval_fastify._build_tools()
    grep_tool = tools["grep"]

    assert grep_tool.name == "grep_search"

    result = grep_tool.invoke(
        {
            "pattern": "setNotFoundHandler",
            "path": "/",
            "include": "*.js",
            "output_mode": "content",
        }
    )

    assert "/test/404s.test.js:" in result
    assert "setNotFoundHandler" in result


def test_hybrid_results_include_precise_line_numbers() -> None:
    eval_fastify = importlib.import_module("eval_fastify")
    response = SimpleNamespace(
        results=[
            SimpleNamespace(
                file=str(eval_fastify.REPO_DIR / "test" / "route.test.js"),
                start_line=1,
                end_line=1,
                content="test('HEAD route')",
                provenance="vector",
            ),
            SimpleNamespace(
                file=str(eval_fastify.REPO_DIR / "lib" / "route.js"),
                start_line=452,
                end_line=454,
                content="if (shouldExposeHead) {\n  prepareRoute.call(this)\n}",
                provenance="vector",
            ),
        ]
    )

    formatted = eval_fastify._format_hybrid_results(response)

    assert "=== lib/route.js:452-454 (provenance=vector) ===" in formatted
    assert "=== test/route.test.js:1 (provenance=vector) ===" in formatted
    assert formatted.index("=== lib/route.js") < formatted.index("=== test/route")
    assert "452 | if (shouldExposeHead) {" in formatted
    assert "453 |   prepareRoute.call(this)" in formatted
    assert "454 | }" in formatted
