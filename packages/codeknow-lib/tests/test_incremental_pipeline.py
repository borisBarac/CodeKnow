from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest
from codeknow.git_download import GitChange
from codeknow.pipeline import PipelineConfig, load_current, run_pipeline

if TYPE_CHECKING:
    from pathlib import Path


def _discovery(*paths: Path) -> dict:
    return {
        "files": {"code": [str(path) for path in paths]},
        "total_files": len(paths),
        "total_words": len(paths),
    }


def _extraction(labels: dict[str, list[str]]) -> dict:
    nodes = []
    for file, file_labels in labels.items():
        for line, label in enumerate(file_labels, start=1):
            nodes.append(
                {
                    "id": f"{file}:{label}",
                    "label": label,
                    "source_file": file,
                    "source_location": f"L{line}",
                    "end_line": line,
                }
            )
    return {"nodes": nodes, "edges": []}


def _build(extractions: list[dict]) -> nx.Graph:
    graph = nx.Graph()
    for node in extractions[0]["nodes"]:
        graph.add_node(node["id"], **node)
    return graph


def _run(
    config: PipelineConfig,
    root: Path,
    discovery: dict,
    extraction: dict,
    *,
    embed_fn=None,
):
    embed = embed_fn or (lambda result, **_kwargs: result)
    return run_pipeline(
        config,
        resolve_fn=lambda _config: root,
        detect_fn=lambda _root: discovery,
        extract_ast_fn=lambda _discovery: extraction,
        build_graph_fn=_build,
        cluster_fn=lambda graph: {0: list(graph.nodes)},
        embed_fn=embed,
    )


def test_incremental_build_reuses_unchanged_chunks_and_removes_stale_nodes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    first = root / "first.py"
    second = root / "second.py"
    first.write_text("old\nremoved\n", encoding="utf-8")
    second.write_text("stable\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        initial = _run(
            config,
            root,
            _discovery(first, second),
            _extraction({"first.py": ["old", "removed"], "second.py": ["stable"]}),
        )

    previous = load_current(output)
    assert previous is not None
    metadata = json.loads((previous.directory / "metadata.json").read_text())
    assert metadata["commit_hash"] == "old"
    unchanged_chunks = initial.chunk_map["second.py"]
    first.write_text("new\n", encoding="utf-8")
    embed_spy = MagicMock(side_effect=lambda result, **_kwargs: result)

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", "first.py")],
        ),
    ):
        updated = _run(
            config,
            root,
            _discovery(first, second),
            _extraction({"first.py": ["new"], "second.py": ["stable"]}),
            embed_fn=embed_spy,
        )

    assert updated.changed_paths == frozenset({"first.py"})
    assert updated.chunk_map["second.py"] == unchanged_chunks
    assert "first.py:removed" not in updated.graph
    assert updated.prior_collection_name == previous.collection_name
    current = load_current(output)
    assert current is not None
    assert current.generation_id != previous.generation_id
    assert embed_spy.call_count == 1


def test_same_commit_returns_active_generation_without_pipeline_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        first = _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["value"]}),
        )

    pointer_before = (output / "current.json").read_bytes()
    detect = MagicMock(side_effect=AssertionError("detect must not run"))
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="same"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
    ):
        second = run_pipeline(
            config,
            resolve_fn=lambda _config: root,
            detect_fn=detect,
        )

    assert second.generation_id == first.generation_id
    assert second.changed_paths == frozenset()
    assert (output / "current.json").read_bytes() == pointer_before
    detect.assert_not_called()


def test_failed_incremental_embedding_keeps_active_generation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "graph"
    root.mkdir()
    source = root / "main.py"
    source.write_text("old\n", encoding="utf-8")
    config = PipelineConfig(
        repo_url="https://github.com/owner/repo",
        output_dir=output,
        no_embed=True,
    )
    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="old"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["old"]}),
        )

    pointer_before = (output / "current.json").read_bytes()
    generations_before = set((output / "generations").iterdir())
    source.write_text("new\n", encoding="utf-8")

    with (
        patch("codeknow.pipeline.runner.get_commit_hash", return_value="new"),
        patch("codeknow.pipeline.runner.get_remote_branch", return_value="main"),
        patch("codeknow.pipeline.runner.commit_exists", return_value=True),
        patch(
            "codeknow.pipeline.runner.diff_changes",
            return_value=[GitChange("M", "main.py")],
        ),
        pytest.raises(RuntimeError, match="embedding failed"),
    ):
        _run(
            config,
            root,
            _discovery(source),
            _extraction({"main.py": ["new"]}),
            embed_fn=MagicMock(side_effect=RuntimeError("embedding failed")),
        )

    assert (output / "current.json").read_bytes() == pointer_before
    assert set((output / "generations").iterdir()) == generations_before
