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
