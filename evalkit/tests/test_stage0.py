"""Tests for Stage 0 deterministic checks (no LLM)."""

from pathlib import Path

from evalkit.judge.stage0 import (
    extract_snippet,
    resolve_citation_path,
    stage0,
    verify_existence,
)


def test_verify_existence_reports_true_for_existing_files(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "oauth.py").write_text("x = 1\n", encoding="utf-8")
    result = verify_existence(["src/oauth.py:144"], tmp_path)
    assert result == {"src/oauth.py:144": True}


def test_verify_existence_reports_false_for_missing_files(tmp_path: Path):
    result = verify_existence(["nope/missing.py:5"], tmp_path)
    assert result == {"nope/missing.py:5": False}


def test_verify_existence_mixed(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = verify_existence(["a.py:1", "b.py:1"], tmp_path)
    assert result == {"a.py:1": True, "b.py:1": False}


def test_verify_existence_accepts_absolute_path(tmp_path: Path):
    """Citations may be absolute (hybrid) or repo-relative (grep).

    Pathlib's ``/`` discards the left operand when the right is absolute, so a
    single ``(repo_root / path).exists()`` covers both formats. This locks that
    behaviour in — it is load-bearing for the eval (the two tools emit
    different path formats).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text("x = 1\n", encoding="utf-8")
    result = verify_existence([f"{elsewhere}:1"], repo)
    assert result[f"{elsewhere}:1"] is True


def test_verify_existence_resolves_root_relative_grep_paths(tmp_path: Path):
    """Grep emits root-relative paths like ``/lib/route.js:455``.

    The leading slash must NOT cause the citation to be checked against the
    host's ``/lib/route.js`` (which doesn't exist) and marked nonexistent. It
    should fall back to ``repo_root / "lib/route.js"``. Without this fallback
    grep is systematically penalised in the eval even when it cited a real
    repo file.
    """
    repo = tmp_path / "fastify-main"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "route.js").write_text("module.exports = {}\n", encoding="utf-8")

    result = verify_existence(["/lib/route.js:455"], repo)
    assert result["/lib/route.js:455"] is True


def test_resolve_citation_path_returns_none_for_truly_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Root-relative that does not exist under repo_root either.
    assert resolve_citation_path("/nope/missing.js", repo) is None
    # Relative that does not exist.
    assert resolve_citation_path("nope/missing.js", repo) is None


def test_resolve_citation_path_prefers_literal_absolute_when_it_exists(tmp_path: Path):
    """Don't strip a real absolute path's leading slash unnecessarily.

    If ``/abs/path`` literally exists, return it rather than the stripped form
    — so genuine in-repo absolute citations (hybrid) keep working.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    real_abs = tmp_path / "lib" / "route.js"
    real_abs.parent.mkdir(parents=True)
    real_abs.write_text("x = 1\n", encoding="utf-8")
    resolved = resolve_citation_path(str(real_abs), repo)
    assert resolved == real_abs


def test_stage0_extracts_snippet_for_root_relative_citation(tmp_path: Path):
    """End-to-end: a grep-style root-relative citation yields a real snippet."""
    repo = tmp_path / "fastify-main"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "route.js").write_text(
        "module.exports = function () {\n  return 42\n}\n", encoding="utf-8"
    )

    result = stage0(["/lib/route.js:2"], repo)
    assert result.existence_rate == 1.0
    assert result.snippets["/lib/route.js:2"] is not None
    assert "return 42" in result.snippets["/lib/route.js:2"]


def _write_lines(path: Path, n: int) -> None:
    path.write_text(
        "\n".join(f"line {i}" for i in range(1, n + 1)) + "\n", encoding="utf-8"
    )


def test_extract_snippet_returns_context_window(tmp_path: Path):
    f = tmp_path / "a.py"
    _write_lines(f, 10)
    assert extract_snippet(f, 5, context=2) == "line 3\nline 4\nline 5\nline 6\nline 7"


def test_extract_snippet_clamps_to_start(tmp_path: Path):
    f = tmp_path / "a.py"
    _write_lines(f, 10)
    assert (
        extract_snippet(f, 1, context=5)
        == "line 1\nline 2\nline 3\nline 4\nline 5\nline 6"
    )


def test_extract_snippet_clamps_to_end(tmp_path: Path):
    f = tmp_path / "a.py"
    _write_lines(f, 10)
    assert extract_snippet(f, 10, context=2) == "line 8\nline 9\nline 10"


def test_extract_snippet_missing_file_returns_empty(tmp_path: Path):
    assert extract_snippet(tmp_path / "nope.py", 5) == ""


def test_stage0_aggregates_existence_and_snippets(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "oauth.py").write_text(
        "def refresh():\n    pass\n", encoding="utf-8"
    )

    result = stage0(["src/oauth.py:1", "missing.py:9"], tmp_path)

    assert result.existence_rate == 0.5
    assert result.existence_map == {"src/oauth.py:1": True, "missing.py:9": False}
    assert result.snippets["src/oauth.py:1"] == "def refresh():\n    pass"
    assert result.snippets["missing.py:9"] is None


def test_stage0_empty_citations_is_none(tmp_path: Path):
    result = stage0([], tmp_path)
    assert result.existence_rate is None  # vacuous, not 0%
    assert result.existence_map == {}
    assert result.snippets == {}
