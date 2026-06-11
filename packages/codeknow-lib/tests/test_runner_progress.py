"""Tests for the pipeline runner's progress_callback parameter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from codeknow.pipeline.runner import _STAGES, run_pipeline


def _make_fake_graph() -> MagicMock:
    g = MagicMock()
    g.number_of_nodes.return_value = 0
    g.number_of_edges.return_value = 0
    return g


@pytest.fixture
def fake_config() -> MagicMock:
    config = MagicMock()
    config.repo_url = "git@github.com:test/repo.git"
    config.slug = "test-repo"
    return config


@pytest.fixture
def fake_graph() -> MagicMock:
    return _make_fake_graph()


def _run_with_callback(
    fake_config: MagicMock,
    fake_graph: MagicMock,
    callback: MagicMock | None,
) -> object:
    with (
        patch(
            "codeknow.pipeline.runner.get_commit_hash",
            return_value="abc123",
        ),
        patch(
            "codeknow.pipeline.runner.save_pipeline_result",
            return_value="/fake/test.json",
        ),
    ):
        return run_pipeline(
            fake_config,
            resolve_fn=lambda _c: "/fake/root",
            detect_fn=lambda _r: {
                "files": {},
                "total_files": 0,
                "total_words": 0,
            },
            extract_ast_fn=lambda _f: {
                "nodes": [],
                "edges": [],
                "input_tokens": 0,
                "output_tokens": 0,
            },
            build_graph_fn=lambda _e: fake_graph,
            map_chunks_fn=lambda g, _f: (g, {}),
            cluster_fn=lambda _g: {},
            embed_fn=lambda r: r,
            progress_callback=callback,
        )


def test_progress_callback_called_seven_times(
    fake_config: MagicMock,
    fake_graph: MagicMock,
) -> None:
    callback = MagicMock()
    _run_with_callback(fake_config, fake_graph, callback)

    assert callback.call_count == 7

    stage_names = [call.args[0] for call in callback.call_args_list]
    assert stage_names == [s[0] for s in _STAGES]

    percentages = [call.args[1] for call in callback.call_args_list]
    assert percentages == [s[1] for s in _STAGES]


def test_no_callback_works(
    fake_config: MagicMock,
    fake_graph: MagicMock,
) -> None:
    result = _run_with_callback(fake_config, fake_graph, None)
    assert result is not None


def test_stages_constant_has_seven_entries() -> None:
    assert len(_STAGES) == 7
    stages = [s[0] for s in _STAGES]
    assert stages == [
        "resolve",
        "detect",
        "extract_ast",
        "build",
        "map_chunks",
        "cluster",
        "embed",
    ]


def test_stages_percentages_are_ascending() -> None:
    percentages = [s[1] for s in _STAGES]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100
