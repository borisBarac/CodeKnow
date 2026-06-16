from pathlib import Path

from codeknow.extract.extractor import Extractor


class TestExtractorPython:
    def test_extracts_python_class(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("class Foo:\n    pass\n")
        result = Extractor().extract(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        assert "Foo" in labels
        foo_node = next(n for n in result["nodes"] if n["label"] == "Foo")
        assert foo_node["file_type"] == "code"

    def test_extracts_python_function(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        result = Extractor().extract(tmp_path)
        assert any(n["label"] == "hello()" for n in result["nodes"])

    def test_extracts_python_method(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "class Foo:\n    def bar(self):\n        pass\n"
        )
        result = Extractor().extract(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        assert "Foo" in labels
        assert ".bar()" in labels

    def test_extracts_python_imports(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import os\n")
        result = Extractor().extract(tmp_path)
        relations = [e["relation"] for e in result["edges"]]
        assert "imports" in relations

    def test_extracts_python_from_imports(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("from os.path import join\n")
        result = Extractor().extract(tmp_path)
        relations = [e["relation"] for e in result["edges"]]
        assert "imports_from" in relations


class TestExtractorCrossFile:
    def test_cross_file_import_resolution(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text(
            "from b import Bar\n\nclass Foo:\n    x = Bar()\n"
        )
        (tmp_path / "b.py").write_text("class Bar:\n    pass\n")
        result = Extractor().extract(tmp_path)
        uses_edges = [e for e in result["edges"] if e["relation"] == "uses"]
        assert len(uses_edges) > 0

    def test_cross_file_import_creates_imports_from_edge(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("from b import Bar\n")
        (tmp_path / "b.py").write_text("class Bar:\n    pass\n")
        result = Extractor().extract(tmp_path)
        relations = [e["relation"] for e in result["edges"]]
        assert "imports_from" in relations

    def test_mixed_paths_keep_python_alignment(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        captured: dict[str, int] = {}

        monkeypatch.setattr(
            "codeknow.extract.extractor._resolve_cross_file_imports",
            lambda results, paths: captured.update(
                {"results": len(results), "paths": len(paths)}
            )
            or [],
        )
        monkeypatch.setattr(
            Extractor,
            "_DISPATCH",
            {
                ".py": lambda _path: {
                    "nodes": [],
                    "edges": [],
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        )

        extractor = Extractor()
        extractor._extract([tmp_path / "a.py", tmp_path / "b.md", tmp_path / "c.py"])

        assert captured == {"results": 2, "paths": 2}


class TestExtractorGraphignore:
    def test_respects_graphignore(self, tmp_path: Path) -> None:
        (tmp_path / ".graphignore").write_text("vendor/\n")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "lib.py").write_text("class Vendor:\n    pass\n")
        (tmp_path / "main.py").write_text("class Main:\n    pass\n")
        result = Extractor().extract(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        assert "Vendor" not in labels
        assert "Main" in labels


class TestExtractorJS:
    def test_extracts_js_class(self, tmp_path: Path) -> None:
        (tmp_path / "app.js").write_text("class Foo {\n  constructor() {}\n}\n")
        result = Extractor().extract(tmp_path)
        assert any(n["label"] == "Foo" for n in result["nodes"])

    def test_extracts_ts_class(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text("class Foo {\n  greet(): void {}\n}\n")
        result = Extractor().extract(tmp_path)
        assert any(n["label"] == "Foo" for n in result["nodes"])


class TestExtractorDiscovery:
    def test_skips_sensitive_files(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SECRET=abc\n")
        (tmp_path / "main.py").write_text("class Safe:\n    pass\n")
        result = Extractor().extract(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        assert "Safe" in labels
        assert not any("SECRET" in n.get("label", "") for n in result["nodes"])

    def test_skips_noise_dirs(self, tmp_path: Path) -> None:
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "pkg.py").write_text("class Pkg:\n    pass\n")
        (tmp_path / "main.py").write_text("class Main:\n    pass\n")
        result = Extractor().extract(tmp_path)
        labels = [n["label"] for n in result["nodes"]]
        assert "Pkg" not in labels
        assert "Main" in labels

    def test_empty_repo_returns_empty(self, tmp_path: Path) -> None:
        result = Extractor().extract(tmp_path)
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_returns_standard_keys(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("class Foo:\n    pass\n")
        result = Extractor().extract(tmp_path)
        assert "nodes" in result
        assert "edges" in result
        assert "input_tokens" in result
        assert "output_tokens" in result


class TestExtractorPythonRationale:
    def test_extracts_docstring_as_rationale(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            'class Foo:\n    """This is a detailed docstring for Foo."""\n    pass\n'
        )
        result = Extractor().extract(tmp_path)
        rationale_nodes = [
            n for n in result["nodes"] if n.get("file_type") == "rationale"
        ]
        assert len(rationale_nodes) > 0

    def test_extracts_rationale_comment(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text(
            "# NOTE: this is an important design decision\nx = 1\n"
        )
        result = Extractor().extract(tmp_path)
        rationale_nodes = [
            n for n in result["nodes"] if n.get("file_type") == "rationale"
        ]
        assert len(rationale_nodes) > 0


class TestExtractorCaching:
    def test_second_call_returns_same_result(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("class Foo:\n    pass\n")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        ext = Extractor(cache_dir=cache_dir)
        r1 = ext.extract(tmp_path)
        r2 = ext.extract(tmp_path)
        assert len(r1["nodes"]) == len(r2["nodes"])
        assert len(r1["edges"]) == len(r2["edges"])

    def test_cache_hit_reanchors_source_file(self, tmp_path: Path) -> None:
        """Cached extraction with stale paths must be re-anchored to the
        current file path.

        1. Extract in dir A (populating cache at a/graph-out/cache/).
        2. Copy dir A (including cache) to dir B.
        3. Extract from B — every file hits stale cache from A.
        4. Assert source_file references B, not A.
        """
        import shutil

        dir_a = tmp_path / "a"
        dir_a.mkdir()
        (dir_a / "main.py").write_text("class Foo:\n    pass\n")

        Extractor(cache_dir=dir_a).extract(dir_a)
        shutil.copytree(dir_a, tmp_path / "b")
        dir_b = tmp_path / "b"

        result_b = Extractor(cache_dir=dir_b).extract(dir_b)

        for node in result_b["nodes"]:
            src = node.get("source_file", "")
            if src:
                assert str(dir_b) in src, (
                    f"source_file {src!r} should reference dir_b, not dir_a"
                )
                assert str(dir_a) not in src

    def test_cache_hit_remaps_stale_file_node_id(self, tmp_path: Path) -> None:
        """Cached file node IDs (location-dependent) must be remapped so
        cross-file edges connect after a stale cache hit.

        Uses two files with a cross-file import to exercise the stale ID
        remap for both source and target of edges.
        """
        import shutil

        dir_a = tmp_path / "a"
        dir_a.mkdir()
        for name, content in (
            ("a.py", "from b import Bar\n\nclass Foo:\n    x = Bar()\n"),
            ("b.py", "class Bar:\n    pass\n"),
        ):
            (dir_a / name).write_text(content)

        result_a = Extractor(cache_dir=dir_a).extract(dir_a)

        shutil.copytree(dir_a, tmp_path / "b")
        dir_b = tmp_path / "b"

        result_b = Extractor(cache_dir=dir_b).extract(dir_b)

        file_ids_a = {n["id"] for n in result_a["nodes"] if n["label"].endswith(".py")}
        file_ids_b = {n["id"] for n in result_b["nodes"] if n["label"].endswith(".py")}
        assert file_ids_a == file_ids_b, (
            "File node IDs should be relative-path-based and identical "
            f"across dirs. A={file_ids_a}, B={file_ids_b}"
        )

    def test_cache_hit_preserves_raw_calls(self, tmp_path: Path) -> None:
        """raw_calls must survive the cache roundtrip so inferred calls
        edges are generated on cache hits.
        """
        (tmp_path / "main.py").write_text(
            "class Foo:\n    def bar(self):\n        external_func()\n"
        )
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        ext = Extractor(cache_dir=cache_dir)
        ext.extract(tmp_path)

        from codeknow.cache import load_cached

        cached = load_cached(tmp_path / "main.py", cache_dir)
        assert cached is not None
        assert "raw_calls" in cached
        assert len(cached["raw_calls"]) > 0


class TestExtractorWordCount:
    def test_total_words_increments_for_documents(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "Hello world this is a test document with enough words to count.\n"
        )
        ext = Extractor()
        discovery = ext.discover(tmp_path)
        assert discovery["total_words"] > 0

    def test_total_words_zero_when_no_documents(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("class Foo:\n    pass\n")
        ext = Extractor()
        discovery = ext.discover(tmp_path)
        assert discovery["total_words"] == 0
