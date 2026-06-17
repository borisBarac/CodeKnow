# ruff: noqa: S101, SLF001

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
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
                for required in support.REQUIRED_INDEX_FILES
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


def _minimal_profile() -> dict:
    return {
        "hybrid": {
            "grounding_mean": 3.0,
            "faithfulness_mean": 3.0,
            "consistency_pct": None,
            "preference_win_rate_pct": 50.0,
            "preference_win_rate_ci": None,
            "cost": {
                "median_tokens": 0.0,
                "median_search_calls": 0.0,
                "median_wall_clock_s": 0.0,
            },
        },
        "grep": {
            "grounding_mean": 3.0,
            "faithfulness_mean": 3.0,
            "consistency_pct": None,
            "preference_win_rate_pct": 50.0,
            "preference_win_rate_ci": None,
            "cost": {
                "median_tokens": 0.0,
                "median_search_calls": 0.0,
                "median_wall_clock_s": 0.0,
            },
        },
        "bias_check": {"length_winrate_correlation": None, "bias_flagged": False},
        "stats": {
            "binomial_preference_p": None,
            "wilcoxon_grounding_p": None,
            "wilcoxon_faithfulness_p": None,
        },
    }


def _task(task_id: str) -> Any:
    eval_fastify = importlib.import_module("eval_fastify")
    return eval_fastify.Task(
        task_id=task_id,
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt="Where is the login entry point?",
    )


def _jout(task_id: str, tool: str, seed: int, grounding: int) -> Any:
    eval_fastify = importlib.import_module("eval_fastify")
    return eval_fastify.JudgeOutput(
        task_id=task_id,
        tool=tool,
        seed=seed,
        grounding=grounding,
        existence_rate=1.0,
        faithfulness=3,
        ungrounded_claims=[],
        hallucinated_paths=[],
    )


def test_write_report_renders_every_seed_in_multi_seed_eval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Multi-seed evals must surface every seed, not just the last one.

    Regression: ``out_by_key = {(task_id, tool): o}`` used to overwrite earlier
    seeds, so the per-task detail showed one arbitrary seed while the profile
    aggregated all of them. With EVAL_SEEDS>=2 both ``seed=0`` and ``seed=1``
    must appear per tool.
    """
    eval_fastify = importlib.import_module("eval_fastify")
    monkeypatch.setattr(eval_fastify, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(eval_fastify, "PROFILE_MD", tmp_path / "profile.md")

    task = _task("T-1")
    outputs = [
        _jout("T-1", "hybrid", 0, 4),
        _jout("T-1", "hybrid", 1, 2),
        _jout("T-1", "grep", 0, 3),
        _jout("T-1", "grep", 1, 5),
    ]
    pairwise = [
        eval_fastify.PairwiseJudgment(task_id="T-1", winner="Tie", confidence="low")
    ]

    eval_fastify.write_report([task], outputs, pairwise, _minimal_profile())

    report = (tmp_path / "profile.md").read_text(encoding="utf-8")
    # All four seeds are present.
    assert "seed=0" in report
    assert "seed=1" in report
    # Both grounding scores for hybrid (4 and 2) appear, proving no overwrite.
    assert "grounding 4/5" in report
    assert "grounding 2/5" in report
    assert "grounding 5/5" in report  # grep seed=1


def test_write_report_single_seed_omits_seed_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Single-seed evals keep the concise display (no ``seed=0`` prefix)."""
    eval_fastify = importlib.import_module("eval_fastify")
    monkeypatch.setattr(eval_fastify, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(eval_fastify, "PROFILE_MD", tmp_path / "profile.md")

    task = _task("T-1")
    outputs = [_jout("T-1", "hybrid", 0, 4), _jout("T-1", "grep", 0, 3)]
    pairwise = [
        eval_fastify.PairwiseJudgment(task_id="T-1", winner="hybrid", confidence="high")
    ]

    eval_fastify.write_report([task], outputs, pairwise, _minimal_profile())

    report = (tmp_path / "profile.md").read_text(encoding="utf-8")
    assert "seed=" not in report
    assert "grounding 4/5" in report


class _FakeUsageResponse:
    """Mimics a LangChain LLMResult: ``.content`` for invoke, ``.llm_output``."""

    def __init__(self, content: str, usage: dict | None = None) -> None:
        self.content = content
        self.llm_output = {"token_usage": usage} if usage else None


class _FakeChatModel:
    """Records the invoke config and fires the callback like LangChain would."""

    def __init__(self, answer: str, usage: dict) -> None:
        self.answer = answer
        self.usage = usage
        self.received_callbacks: list | None = None

    def invoke(self, _messages: object, **kwargs: object) -> _FakeUsageResponse:
        cfg = kwargs.get("config") or {}
        callbacks = cfg.get("callbacks", [])  # type: ignore[union-attr]
        self.received_callbacks = callbacks
        for cb in callbacks:
            cb.on_chat_model_start({}, [])
            cb.on_llm_end(_FakeUsageResponse(self.answer, self.usage))
        return _FakeUsageResponse(self.answer)


def test_synthesize_answer_accounts_for_cost_via_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery LLM call must fold its tokens/turns into the run's cost.

    Regression: ``_synthesize_answer`` used to invoke the model with no
    callback, so a run that fell back to synthesis looked artificially cheap
    on the cost axis. With the callback wired through, the synthesis call's
    ``llm_turns`` and token usage are captured.
    """
    eval_fastify = importlib.import_module("eval_fastify")

    fake = _FakeChatModel(
        answer="synthesized answer",
        usage={"prompt_tokens": 100, "completion_tokens": 20},
    )
    monkeypatch.setattr(eval_fastify, "_make_chat_model", lambda: fake)

    callback = eval_fastify.CostCallback(search_tool_name="hybrid")
    item = _task("T-1")

    answer = eval_fastify._synthesize_answer(item, ["result line 1"], callback=callback)

    assert answer == "synthesized answer"
    # The callback reached the fake model's invoke call.
    assert callback in (fake.received_callbacks or [])
    # Cost was accumulated from the synthesis call.
    assert callback.cost.llm_turns == 1
    assert callback.cost.tokens_in == 100
    assert callback.cost.tokens_out == 20
    # search_calls is untouched by synthesis (only the search tool bumps it).
    assert callback.cost.search_calls == 0


def test_synthesize_answer_works_without_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compat: synthesis still returns an answer with no callback."""
    eval_fastify = importlib.import_module("eval_fastify")

    fake = _FakeChatModel(
        answer="no-callback answer", usage={"prompt_tokens": 50, "completion_tokens": 5}
    )
    monkeypatch.setattr(eval_fastify, "_make_chat_model", lambda: fake)

    item = _task("T-1")
    answer = eval_fastify._synthesize_answer(item, ["result line 1"])

    assert answer == "no-callback answer"
    assert fake.received_callbacks == []


def test_synthesize_answer_no_tool_outputs_returns_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eval_fastify = importlib.import_module("eval_fastify")
    monkeypatch.setattr(
        eval_fastify, "_make_chat_model", lambda: _FakeChatModel("x", {})
    )
    assert (
        eval_fastify._synthesize_answer(_task("T-1"), [])
        == "(no search results retrieved)"
    )
