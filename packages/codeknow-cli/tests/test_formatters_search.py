from __future__ import annotations

import re
from io import StringIO
from unittest.mock import patch

from codeknow_cli.client import SearchHit, SearchResult
from codeknow_cli.formatters.search import (
    _build_file_label,
    _build_provenance_label,
    _truncate_content,
    format_search_results,
)
from rich.console import Console

_SAMPLE_RESULT = SearchResult(
    vector_hits=3,
    graph_expanded=1,
    query="q",
    results=[
        SearchHit(
            file="src/foo.py",
            start_line=10,
            end_line=25,
            provenance="vector",
            distance=0.12,
            slug="my-repo",
            graph_path="A -> B -> C",
            content="def hello(): pass",
        ),
        SearchHit(
            file="src/bar.py",
            start_line=None,
            end_line=None,
            provenance="graph",
            weight=0.85,
            content="",
        ),
    ],
)


def _make_console() -> Console:
    buf = StringIO()
    return Console(
        file=buf,
        force_terminal=True,
        width=120,
        legacy_windows=False,
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _run_plain(query: str, result: dict) -> str:
    buf = StringIO()
    with (
        patch("sys.stdout.isatty", return_value=False),
        patch("sys.stdout", buf),
    ):
        format_search_results(query, result)
    return buf.getvalue()


class TestBuildFileLabel:
    def test_with_line_range(self) -> None:
        hit = SearchHit(file="src/a.py", start_line=1, end_line=10, provenance="vector")
        assert _build_file_label(hit) == "src/a.py:1-10"

    def test_without_line_range(self) -> None:
        hit = SearchHit(
            file="src/a.py", start_line=None, end_line=None, provenance="vector"
        )
        assert _build_file_label(hit) == "src/a.py"

    def test_missing_file(self) -> None:
        assert (
            _build_file_label(
                SearchHit(
                    file="?", start_line=None, end_line=None, provenance="unknown"
                )
            )
            == "?"
        )


class TestBuildProvenanceLabel:
    def test_with_distance(self) -> None:
        hit = SearchHit(
            file="a.py",
            start_line=None,
            end_line=None,
            provenance="vector",
            distance=0.5,
        )
        assert _build_provenance_label(hit) == "vector (distance: 0.5)"

    def test_with_weight(self) -> None:
        hit = SearchHit(
            file="a.py", start_line=None, end_line=None, provenance="graph", weight=0.9
        )
        assert _build_provenance_label(hit) == "graph (weight: 0.9)"

    def test_without_metrics(self) -> None:
        hit = SearchHit(
            file="a.py", start_line=None, end_line=None, provenance="vector"
        )
        assert _build_provenance_label(hit) == "vector"

    def test_missing_provenance(self) -> None:
        hit = SearchHit(
            file="a.py", start_line=None, end_line=None, provenance="unknown"
        )
        assert _build_provenance_label(hit) == "unknown"


class TestTruncateContent:
    def test_short_content(self) -> None:
        assert _truncate_content("hello") == "hello"

    def test_exact_limit(self) -> None:
        content = "x" * 200
        assert _truncate_content(content) == content

    def test_over_limit(self) -> None:
        content = "x" * 250
        result = _truncate_content(content)
        assert result == "x" * 200 + "..."
        assert len(result) == 203


class TestFormatSearchResultsPlain:
    def test_plain_output_has_query(self) -> None:
        output = _run_plain("my query", _SAMPLE_RESULT)
        assert "Query: my query" in output

    def test_plain_output_has_hit_counts(self) -> None:
        output = _run_plain("q", _SAMPLE_RESULT)
        assert "Hits: 3 vector, 1 graph-expanded" in output

    def test_plain_output_has_file_labels(self) -> None:
        output = _run_plain("q", _SAMPLE_RESULT)
        assert "File: src/foo.py:10-25" in output
        assert "File: src/bar.py" in output

    def test_plain_output_has_provenance(self) -> None:
        output = _run_plain("q", _SAMPLE_RESULT)
        assert "Provenance: vector (distance: 0.12)" in output
        assert "Provenance: graph (weight: 0.85)" in output

    def test_plain_output_has_slug_and_path(self) -> None:
        output = _run_plain("q", _SAMPLE_RESULT)
        assert "Slug: my-repo" in output
        assert "Path: A -> B -> C" in output

    def test_plain_empty_results(self) -> None:
        result = SearchResult(vector_hits=0, graph_expanded=0, query="q", results=[])
        output = _run_plain("q", result)
        assert "Query: q" in output
        assert "Result" not in output

    def test_plain_no_slug_no_path(self) -> None:
        result = SearchResult(
            vector_hits=1,
            graph_expanded=0,
            query="q",
            results=[
                SearchHit(
                    file="a.py",
                    start_line=None,
                    end_line=None,
                    provenance="vector",
                    distance=0.1,
                    content="x",
                ),
            ],
        )
        output = _run_plain("q", result)
        assert "Slug" not in output
        assert "Path" not in output


class TestFormatSearchResultsRich:
    def test_rich_output_contains_file(self) -> None:
        console = _make_console()
        with patch(
            "codeknow_cli.formatters.search.Console",
            return_value=console,
        ):
            format_search_results("my query", _SAMPLE_RESULT)
        output = _strip_ansi(console.file.getvalue())  # type: ignore[union-attr]
        assert "src/foo.py:10-25" in output

    def test_rich_output_contains_provenance(self) -> None:
        console = _make_console()
        with patch(
            "codeknow_cli.formatters.search.Console",
            return_value=console,
        ):
            format_search_results("q", _SAMPLE_RESULT)
        output = _strip_ansi(console.file.getvalue())  # type: ignore[union-attr]
        assert "distance: 0.12" in output
        assert "weight: 0.85" in output

    def test_rich_output_contains_content(self) -> None:
        console = _make_console()
        with patch(
            "codeknow_cli.formatters.search.Console",
            return_value=console,
        ):
            format_search_results("q", _SAMPLE_RESULT)
        output = _strip_ansi(console.file.getvalue())  # type: ignore[union-attr]
        assert "def hello(): pass" in output
