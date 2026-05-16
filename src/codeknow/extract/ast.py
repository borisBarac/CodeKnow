"""Deterministic structural extraction from source code using
tree-sitter. Outputs nodes+edges dicts.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeknow.cache import load_cached, save_cached

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node


def _make_id(*parts: str) -> str:
    """Build a stable node ID from one or more name parts."""
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()


# ── LanguageConfig dataclass ─────────────────────────────────────────────────


@dataclass
class LanguageConfig:
    ts_module: str  # e.g. "tree_sitter_python"
    ts_language_fn: str = "language"  # attr to call: e.g. tslang.language()

    class_types: frozenset = frozenset()
    function_types: frozenset = frozenset()
    import_types: frozenset = frozenset()
    call_types: frozenset = frozenset()

    # Name extraction
    name_field: str = "name"
    name_fallback_child_types: tuple = ()

    # Body detection
    body_field: str = "body"
    body_fallback_child_types: tuple = ()  # e.g. ("declaration_list",
    # "compound_statement")

    # Call name extraction
    call_function_field: str = "function"  # field on call node for callee
    call_accessor_node_types: frozenset = frozenset()  # member/attribute nodes
    call_accessor_field: str = "attribute"  # field on accessor for method name

    # Stop recursion at these types in walk_calls
    function_boundary_types: frozenset = frozenset()

    # Import handler: called for import nodes instead of generic handling
    import_handler: Callable | None = None

    # Extra label formatting for functions: if True, functions get "name()" label
    function_label_parens: bool = True


# ── Generic helpers ───────────────────────────────────────────────────────────


def _read_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _resolve_name(node: Node, source: bytes, config: LanguageConfig) -> str | None:
    """Get the name from a node using config.name_field, falling back to child types."""
    n = node.child_by_field_name(config.name_field)
    if n:
        return _read_text(n, source)
    for child in node.children:
        if child.type in config.name_fallback_child_types:
            return _read_text(child, source)
    return None


def _find_body(node: Node, config: LanguageConfig) -> Node | None:
    """Find the body node using config.body_field, falling back to child types."""
    b = node.child_by_field_name(config.body_field)
    if b:
        return b
    for child in node.children:
        if child.type in config.body_fallback_child_types:
            return child
    return None


# ── Import handlers ───────────────────────────────────────────────────────────


def _import_python(
    node: Node, source: bytes, file_nid: str, stem: str, edges: list[Any], str_path: str
) -> None:
    t = node.type
    if t == "import_statement":
        for child in node.children:
            if child.type in ("dotted_name", "aliased_import"):
                raw = _read_text(child, source)
                module_name = raw.split(" as ")[0].strip().lstrip(".")
                tgt_nid = _make_id(module_name)
                edges.append(
                    {
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "source_location": f"L{node.start_point[0] + 1}",
                        "weight": 1.0,
                    }
                )
    elif t == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node:
            raw = _read_text(module_node, source)
            if raw.startswith("."):
                # Relative import - resolve to full path so IDs match file node IDs
                dots = len(raw) - len(raw.lstrip("."))
                module_name = raw.lstrip(".")
                base = Path(str_path).parent
                for _ in range(dots - 1):
                    base = base.parent
                rel = (
                    (module_name.replace(".", "/") + ".py")
                    if module_name
                    else "__init__.py"
                )
                tgt_nid = _make_id(str(base / rel))
            else:
                tgt_nid = _make_id(raw)
            edges.append(
                {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports_from",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
            )


def _import_js(
    node: Node, source: bytes, file_nid: str, stem: str, edges: list[Any], str_path: str
) -> None:
    for child in node.children:
        if child.type == "string":
            raw = _read_text(child, source).strip("'\"` ")
            if not raw:
                break
            if raw.startswith("."):
                # Relative import - resolve to full path so IDs match file node IDs
                # normpath removes ".." segments so the ID matches
                # the target file's own node ID
                resolved = Path(os.path.normpath(Path(str_path).parent / raw))
                # TypeScript ESM: imports written as .js but actual file is .ts/.tsx
                if resolved.suffix == ".js":
                    resolved = resolved.with_suffix(".ts")
                elif resolved.suffix == ".jsx":
                    resolved = resolved.with_suffix(".tsx")
                tgt_nid = _make_id(str(resolved))
            else:
                # Bare/scoped import (node_modules) - use last
                # segment; dropped as external
                module_name = raw.split("/")[-1]
                if not module_name:
                    break
                tgt_nid = _make_id(module_name)
            edges.append(
                {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports_from",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
            )
            break


# ── JS/TS extra walk for arrow functions ──────────────────────────────────────


def _js_extra_walk(
    node: Node,
    source: bytes,
    file_nid: str,
    stem: str,
    str_path: str,
    nodes: list[Any],
    edges: list[Any],
    seen_ids: set[str],
    function_bodies: list,
    parent_class_nid: str | None,
    add_node_fn: Callable[[str, str, int, int | None], None],
    add_edge_fn: Callable[[str, str, str, int], None],
) -> bool:
    """Handle lexical_declaration (arrow functions) for JS/TS.
    Returns True if handled.
    """
    if node.type == "lexical_declaration":
        for child in node.children:
            if child.type == "variable_declarator":
                value = child.child_by_field_name("value")
                if value and value.type == "arrow_function":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        func_name = _read_text(name_node, source)
                        line = child.start_point[0] + 1
                        func_nid = _make_id(stem, func_name)
                        add_node_fn(
                            func_nid, f"{func_name}()", line, child.end_point[0] + 1
                        )
                        add_edge_fn(file_nid, func_nid, "contains", line)
                        body = value.child_by_field_name("body")
                        if body:
                            function_bodies.append((func_nid, body))
        return True
    return False


# ── Language configs ──────────────────────────────────────────────────────────

_PYTHON_CONFIG = LanguageConfig(
    ts_module="tree_sitter_python",
    class_types=frozenset({"class_definition"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"import_statement", "import_from_statement"}),
    call_types=frozenset({"call"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"attribute"}),
    call_accessor_field="attribute",
    function_boundary_types=frozenset({"function_definition"}),
    import_handler=_import_python,
)

_JS_CONFIG = LanguageConfig(
    ts_module="tree_sitter_javascript",
    class_types=frozenset({"class_declaration"}),
    function_types=frozenset({"function_declaration", "method_definition"}),
    import_types=frozenset({"import_statement"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"member_expression"}),
    call_accessor_field="property",
    function_boundary_types=frozenset(
        {"function_declaration", "arrow_function", "method_definition"}
    ),
    import_handler=_import_js,
)

_TS_CONFIG = LanguageConfig(
    ts_module="tree_sitter_typescript",
    ts_language_fn="language_typescript",
    class_types=frozenset({"class_declaration"}),
    function_types=frozenset({"function_declaration", "method_definition"}),
    import_types=frozenset({"import_statement"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"member_expression"}),
    call_accessor_field="property",
    function_boundary_types=frozenset(
        {"function_declaration", "arrow_function", "method_definition"}
    ),
    import_handler=_import_js,
)

# ── Generic extractor ─────────────────────────────────────────────────────────


def _extract_generic(path: Path, config: LanguageConfig) -> dict:
    """Generic AST extractor driven by LanguageConfig."""
    try:
        mod = importlib.import_module(config.ts_module)
        from tree_sitter import Language, Parser

        lang_fn = getattr(mod, config.ts_language_fn, None)
        if lang_fn is None:
            # Generic fallback: try default "language" attr
            lang_fn = getattr(mod, "language", None)
        if lang_fn is None:
            return {
                "nodes": [],
                "edges": [],
                "error": f"No language function in {config.ts_module}",
            }
        language = Language(lang_fn())
    except ImportError:
        return {"nodes": [], "edges": [], "error": f"{config.ts_module} not installed"}
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    try:
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = path.stem
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    function_bodies: list[tuple[str, object]] = []

    def add_node(nid: str, label: str, line: int, end_line: int | None = None) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nd: dict[str, Any] = {
                "id": nid,
                "label": label,
                "file_type": "code",
                "source_file": str_path,
                "source_location": f"L{line}",
            }
            if end_line is not None:
                nd["end_line"] = end_line
            nodes.append(nd)

    def add_edge(
        src: str,
        tgt: str,
        relation: str,
        line: int,
        confidence: str = "EXTRACTED",
        weight: float = 1.0,
    ) -> None:
        edges.append(
            {
                "source": src,
                "target": tgt,
                "relation": relation,
                "confidence": confidence,
                "source_file": str_path,
                "source_location": f"L{line}",
                "weight": weight,
            }
        )

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name, 1)

    def walk(node: Node, parent_class_nid: str | None = None) -> None:
        t = node.type

        # Import types
        if t in config.import_types:
            if config.import_handler:
                config.import_handler(node, source, file_nid, stem, edges, str_path)
            return

        # Class types
        if t in config.class_types:
            # Resolve class name
            name_node = node.child_by_field_name(config.name_field)
            if name_node is None:
                for child in node.children:
                    if child.type in config.name_fallback_child_types:
                        name_node = child
                        break
            if not name_node:
                return
            class_name = _read_text(name_node, source)
            class_nid = _make_id(stem, class_name)
            line = node.start_point[0] + 1
            add_node(class_nid, class_name, line, node.end_point[0] + 1)
            add_edge(file_nid, class_nid, "contains", line)

            # Python-specific: inheritance
            if config.ts_module == "tree_sitter_python":
                args = node.child_by_field_name("superclasses")
                if args:
                    for arg in args.children:
                        if arg.type == "identifier":
                            base = _read_text(arg, source)
                            base_nid = _make_id(stem, base)
                            if base_nid not in seen_ids:
                                base_nid = _make_id(base)
                                if base_nid not in seen_ids:
                                    nodes.append(
                                        {
                                            "id": base_nid,
                                            "label": base,
                                            "file_type": "code",
                                            "source_file": "",
                                            "source_location": "",
                                        }
                                    )
                                    seen_ids.add(base_nid)
                            add_edge(class_nid, base_nid, "inherits", line)

            # Find body and recurse
            body = _find_body(node, config)
            if body:
                for child in body.children:
                    walk(child, parent_class_nid=class_nid)
            return

        # Function types
        if t in config.function_types:
            name_node = node.child_by_field_name(config.name_field)
            if name_node is None:
                for child in node.children:
                    if child.type in config.name_fallback_child_types:
                        name_node = child
                        break
            func_name = _read_text(name_node, source) if name_node else None

            if not func_name:
                return

            line = node.start_point[0] + 1
            if parent_class_nid:
                func_nid = _make_id(parent_class_nid, func_name)
                add_node(func_nid, f".{func_name}()", line, node.end_point[0] + 1)
                add_edge(parent_class_nid, func_nid, "method", line)
            else:
                func_nid = _make_id(stem, func_name)
                add_node(func_nid, f"{func_name}()", line, node.end_point[0] + 1)
                add_edge(file_nid, func_nid, "contains", line)

            body = _find_body(node, config)
            if body:
                function_bodies.append((func_nid, body))
            return

        # JS/TS arrow functions — language-specific extra handling
        if config.ts_module in ("tree_sitter_javascript", "tree_sitter_typescript"):
            if _js_extra_walk(
                node,
                source,
                file_nid,
                stem,
                str_path,
                nodes,
                edges,
                seen_ids,
                function_bodies,
                parent_class_nid,
                add_node,
                add_edge,
            ):
                return

        # Default: recurse
        for child in node.children:
            walk(child, parent_class_nid=None)

    walk(root)

    # ── Call-graph pass ───────────────────────────────────────────────────────
    label_to_nid: dict[str, str] = {}
    for n in nodes:
        raw = n["label"]
        normalised = raw.strip("()").lstrip(".")
        label_to_nid[normalised.lower()] = n["id"]

    raw_calls: list[
        dict
    ] = []  # unresolved calls for cross-file resolution in extract()
    seen_call_pairs: set[tuple[str, str]] = set()

    def walk_calls(node: Node, caller_nid: str) -> None:
        if node.type in config.function_boundary_types:
            return

        if node.type in config.call_types:
            callee_name: str | None = None

            # Generic: get callee from call_function_field
            func_node = (
                node.child_by_field_name(config.call_function_field)
                if config.call_function_field
                else None
            )
            if func_node:
                if func_node.type == "identifier":
                    callee_name = _read_text(func_node, source)
                elif func_node.type in config.call_accessor_node_types:
                    if config.call_accessor_field:
                        attr = func_node.child_by_field_name(config.call_accessor_field)
                        if attr:
                            callee_name = _read_text(attr, source)
                else:
                    callee_name = _read_text(func_node, source)

            if callee_name:
                tgt_nid = label_to_nid.get(callee_name.lower())
                if tgt_nid and tgt_nid != caller_nid:
                    pair = (caller_nid, tgt_nid)
                    if pair not in seen_call_pairs:
                        seen_call_pairs.add(pair)
                        line = node.start_point[0] + 1
                        edges.append(
                            {
                                "source": caller_nid,
                                "target": tgt_nid,
                                "relation": "calls",
                                "confidence": "EXTRACTED",
                                "source_file": str_path,
                                "source_location": f"L{line}",
                                "weight": 1.0,
                            }
                        )
                elif callee_name and not tgt_nid:
                    # Callee not in this file — save for
                    # cross-file resolution in extract()
                    raw_calls.append(
                        {
                            "caller_nid": caller_nid,
                            "callee": callee_name,
                            "source_file": str_path,
                            "source_location": f"L{node.start_point[0] + 1}",
                        }
                    )

        for child in node.children:
            walk_calls(child, caller_nid)

    for caller_nid, body_node in function_bodies:
        walk_calls(body_node, caller_nid)

    # ── Clean edges ───────────────────────────────────────────────────────────
    valid_ids = seen_ids
    clean_edges = []
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in valid_ids and (
            tgt in valid_ids or edge["relation"] in ("imports", "imports_from")
        ):
            clean_edges.append(edge)

    return {"nodes": nodes, "edges": clean_edges, "raw_calls": raw_calls}


# ── Python rationale extraction ───────────────────────────────────────────────

_RATIONALE_PREFIXES = (
    "# NOTE:",
    "# IMPORTANT:",
    "# HACK:",
    "# WHY:",
    "# RATIONALE:",
    "# TODO:",
    "# FIXME:",
)


def _extract_python_rationale(path: Path, result: dict) -> None:
    """Post-pass: extract docstrings and rationale comments from Python source.
    Mutates result in-place by appending to result['nodes'] and result['edges'].
    """
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        language = Language(tspython.language())
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception:
        return

    stem = path.stem
    str_path = str(path)
    nodes = result["nodes"]
    edges = result["edges"]
    seen_ids = {n["id"] for n in nodes}
    file_nid = _make_id(str(path))

    def _get_docstring(body_node: Node | None) -> tuple[str, int] | None:
        if not body_node:
            return None
        for child in body_node.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        text = source[sub.start_byte : sub.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                        text = text.strip("\"'").strip('"""').strip("'''").strip()
                        if len(text) > 20:
                            return text, child.start_point[0] + 1
            break
        return None

    def _add_rationale(text: str, line: int, parent_nid: str) -> None:
        label = (
            text[:80].replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
        )
        rid = _make_id(stem, "rationale", str(line))
        if rid not in seen_ids:
            seen_ids.add(rid)
            nodes.append(
                {
                    "id": rid,
                    "label": label,
                    "file_type": "rationale",
                    "source_file": str_path,
                    "source_location": f"L{line}",
                }
            )
        edges.append(
            {
                "source": rid,
                "target": parent_nid,
                "relation": "rationale_for",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{line}",
                "weight": 1.0,
            }
        )

    # Module-level docstring
    ds = _get_docstring(root)
    if ds:
        _add_rationale(ds[0], ds[1], file_nid)

    # Class and function docstrings
    def walk_docstrings(node: Node, parent_nid: str) -> None:
        t = node.type
        if t == "class_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                class_name = source[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                nid = _make_id(stem, class_name)
                ds = _get_docstring(body)
                if ds:
                    _add_rationale(ds[0], ds[1], nid)
                for child in body.children:
                    walk_docstrings(child, nid)
            return
        if t == "function_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                func_name = source[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                nid = (
                    _make_id(parent_nid, func_name)
                    if parent_nid != file_nid
                    else _make_id(stem, func_name)
                )
                ds = _get_docstring(body)
                if ds:
                    _add_rationale(ds[0], ds[1], nid)
            return
        for child in node.children:
            walk_docstrings(child, parent_nid)

    walk_docstrings(root, file_nid)

    # Rationale comments (# NOTE:, # IMPORTANT:, etc.)
    source_text = source.decode("utf-8", errors="replace")
    for lineno, line_text in enumerate(source_text.splitlines(), start=1):
        stripped = line_text.strip()
        if any(stripped.startswith(p) for p in _RATIONALE_PREFIXES):
            _add_rationale(stripped, lineno, file_nid)


# ── Public API ────────────────────────────────────────────────────────────────


def extract_python(path: Path) -> dict:
    """Extract classes, functions, and imports from a .py file via tree-sitter AST."""
    result = _extract_generic(path, _PYTHON_CONFIG)
    if "error" not in result:
        _extract_python_rationale(path, result)
    return result


def extract_js(path: Path) -> dict:
    """Extract classes, functions, arrow functions, and imports
    from a .js/.ts/.tsx file.
    """
    config = _TS_CONFIG if path.suffix in (".ts", ".tsx") else _JS_CONFIG
    return _extract_generic(path, config)


# ── Cross-file import resolution ──────────────────────────────────────────────


def _resolve_cross_file_imports(
    per_file: list[dict],
    paths: list[Path],
) -> list[dict]:
    """Two-pass import resolution: turn file-level imports into class-level edges.

    Pass 1 - build a global map: class/function name → node_id, per stem.
    Pass 2 - for each `from .module import Name`, look up Name in the global
              map and add a direct INFERRED edge from each class in the
              importing file to the imported entity.

    This turns:
        auth.py --imports_from--> models.py          (obvious, filtered out)
    Into:
        DigestAuth --uses--> Response  [INFERRED]    (cross-file, interesting!)
        BasicAuth  --uses--> Request   [INFERRED]
    """
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser
    except ImportError:
        return []

    language = Language(tspython.language())
    parser = Parser(language)

    # Pass 1: name → node_id across all files
    # Map: stem → {ClassName: node_id}
    stem_to_entities: dict[str, dict[str, str]] = {}
    for file_result in per_file:
        for node in file_result.get("nodes", []):
            src = node.get("source_file", "")
            if not src:
                continue
            stem = Path(src).stem
            label = node.get("label", "")
            nid = node.get("id", "")
            # Only index real classes/functions (not file nodes, not method stubs)
            if label and not label.endswith((")", ".py")) and "_" not in label[:1]:
                stem_to_entities.setdefault(stem, {})[label] = nid

    # Pass 2: for each file, find `from .X import A, B, C` and resolve
    new_edges: list[dict] = []

    for file_result, path in zip(per_file, paths, strict=False):
        stem = path.stem
        str_path = str(path)

        # Find all classes defined in this file (the importers)
        local_classes = [
            n["id"]
            for n in file_result.get("nodes", [])
            if n.get("source_file") == str_path
            and not n["label"].endswith((")", ".py"))
            and n["id"] != _make_id(stem)  # exclude file-level node
        ]
        if not local_classes:
            continue

        # Parse imports from this file
        try:
            source = path.read_bytes()
            tree = parser.parse(source)
        except Exception:  # noqa: S112
            continue

        def walk_imports(node: Node) -> None:
            if node.type == "import_from_statement":
                # Find the module name - handles both absolute and relative imports.
                # Relative: `from .models import X` → relative_import → dotted_name
                # Absolute: `from models import X`  → module_name field
                target_stem: str | None = None
                for child in node.children:
                    if child.type == "relative_import":
                        # Dig into relative_import → dotted_name → identifier
                        for sub in child.children:
                            if sub.type == "dotted_name":
                                raw = source[sub.start_byte : sub.end_byte].decode(
                                    "utf-8", errors="replace"
                                )
                                target_stem = raw.split(".")[-1]
                                break
                        break
                    if child.type == "dotted_name" and target_stem is None:
                        raw = source[child.start_byte : child.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                        target_stem = raw.split(".")[-1]

                if not target_stem or target_stem not in stem_to_entities:
                    return

                # Collect imported names: dotted_name children of import_from_statement
                # that come AFTER the 'import' keyword token.
                imported_names: list[str] = []
                past_import_kw = False
                for child in node.children:
                    if child.type == "import":
                        past_import_kw = True
                        continue
                    if not past_import_kw:
                        continue
                    if child.type == "dotted_name":
                        imported_names.append(
                            source[child.start_byte : child.end_byte].decode(
                                "utf-8", errors="replace"
                            )
                        )
                    elif child.type == "aliased_import":
                        # `import X as Y` - take the original name
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            imported_names.append(
                                source[
                                    name_node.start_byte : name_node.end_byte
                                ].decode("utf-8", errors="replace")
                            )

                line = node.start_point[0] + 1
                for name in imported_names:
                    tgt_nid = stem_to_entities[target_stem].get(name)
                    if tgt_nid:
                        for src_class_nid in local_classes:
                            new_edges.append(
                                {
                                    "source": src_class_nid,
                                    "target": tgt_nid,
                                    "relation": "uses",
                                    "confidence": "INFERRED",
                                    "source_file": str_path,
                                    "source_location": f"L{line}",
                                    "weight": 0.8,
                                }
                            )
            for child in node.children:
                walk_imports(child)

        walk_imports(tree.root_node)

    return new_edges


# ── Main extract and collect_files ────────────────────────────────────────────


def _check_tree_sitter_version() -> None:
    """Raise a clear error if tree-sitter is too old for the new Language API."""
    try:
        from tree_sitter import LANGUAGE_VERSION
    except ImportError:
        msg = "tree-sitter is not installed. Run: pip install 'tree-sitter>=0.23.0'"
        raise ImportError(msg)
    # Language API v2 starts at LANGUAGE_VERSION 14
    if LANGUAGE_VERSION < 14:
        import tree_sitter as _ts

        msg = (
            f"tree-sitter {getattr(_ts, '__version__', 'unknown')} is too old. "
            f"graph requires tree-sitter >= 0.23.0 (Language API v2). "
            f"Run: pip install --upgrade tree-sitter"
        )
        raise RuntimeError(msg)


def extract(paths: list[Path], cache_root: Path | None = None) -> dict:
    """Extract AST nodes and edges from a list of code files.

    Two-pass process:
    1. Per-file structural extraction (classes, functions, imports)
    2. Cross-file import resolution: turns file-level imports into
       class-level INFERRED edges (DigestAuth --uses--> Response)

    Args:
        paths: files to extract from
        cache_root: explicit root for graph-out/cache/ (overrides the
            inferred common path prefix). Pass Path('.') when running on a
            subdirectory so the cache stays at ./graph-out/cache/.

    """
    _check_tree_sitter_version()
    per_file: list[dict] = []

    # Infer a common root for cache keys (use first diverging
    # segment, not sum of all matches)
    try:
        if not paths:
            root = Path()
        elif len(paths) == 1:
            root = paths[0].parent
        else:
            min_parts = min(len(p.parts) for p in paths)
            common_len = 0
            for i in range(min_parts):
                if len({p.parts[i] for p in paths}) == 1:
                    common_len += 1
                else:
                    break
            root = Path(*paths[0].parts[:common_len]) if common_len else Path()
    except Exception:
        root = Path()
    root = root.resolve()

    _DISPATCH: dict[str, Any] = {
        ".py": extract_python,
        ".js": extract_js,
        ".jsx": extract_js,
        ".mjs": extract_js,
        ".ejs": extract_js,
        ".ts": extract_js,
        ".tsx": extract_js,
    }

    total = len(paths)
    _PROGRESS_INTERVAL = 100
    for i, path in enumerate(paths):
        if total >= _PROGRESS_INTERVAL and i % _PROGRESS_INTERVAL == 0 and i > 0:
            pass
        extractor = _DISPATCH.get(path.suffix)
        if extractor is None:
            continue
        cached = load_cached(path, cache_root or root)
        if cached is not None:
            per_file.append(cached)
            continue
        result = extractor(path)
        if "error" not in result:
            save_cached(path, result, cache_root or root)
        per_file.append(result)
    if total >= _PROGRESS_INTERVAL:
        pass

    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    for result in per_file:
        all_nodes.extend(result.get("nodes", []))
        all_edges.extend(result.get("edges", []))

    # Remap file node IDs from absolute-path-derived to project-relative so
    # graph.json edge endpoints are stable across machines (#502)
    id_remap: dict[str, str] = {}
    for path in paths:
        old_id = _make_id(str(path))
        try:
            new_id = _make_id(str(path.relative_to(root)))
        except ValueError:
            continue
        if old_id != new_id:
            id_remap[old_id] = new_id
    if id_remap:
        for n in all_nodes:
            if n.get("id") in id_remap:
                n["id"] = id_remap[n["id"]]
        for e in all_edges:
            if e.get("source") in id_remap:
                e["source"] = id_remap[e["source"]]
            if e.get("target") in id_remap:
                e["target"] = id_remap[e["target"]]

    # Add cross-file class-level edges (Python only - uses Python parser internally)
    py_paths = [p for p in paths if p.suffix == ".py"]
    if py_paths:
        py_results = [
            r for r, p in zip(per_file, paths, strict=False) if p.suffix == ".py"
        ]
        try:
            cross_file_edges = _resolve_cross_file_imports(py_results, py_paths)
            all_edges.extend(cross_file_edges)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Cross-file import resolution failed, skipping: %s", exc
            )

    # Cross-file call resolution for all languages
    # Each extractor saved unresolved calls in raw_calls. Now that we have all
    # nodes from all files, resolve any callee that exists in another file.
    global_label_to_nid: dict[str, str] = {}
    for n in all_nodes:
        raw = n.get("label", "")
        normalised = raw.strip("()").lstrip(".")
        if normalised:
            global_label_to_nid[normalised.lower()] = n["id"]

    existing_pairs = {(e["source"], e["target"]) for e in all_edges}
    for result in per_file:
        for rc in result.get("raw_calls", []):
            callee = rc.get("callee", "")
            if not callee:
                continue
            tgt = global_label_to_nid.get(callee.lower())
            caller = rc["caller_nid"]
            if tgt and tgt != caller and (caller, tgt) not in existing_pairs:
                existing_pairs.add((caller, tgt))
                all_edges.append(
                    {
                        "source": caller,
                        "target": tgt,
                        "relation": "calls",
                        "confidence": "INFERRED",
                        "confidence_score": 0.8,
                        "source_file": rc.get("source_file", ""),
                        "source_location": rc.get("source_location"),
                        "weight": 1.0,
                    }
                )

    return {
        "nodes": all_nodes,
        "edges": all_edges,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def collect_files(
    target: Path, *, follow_symlinks: bool = False, root: Path | None = None
) -> list[Path]:
    if target.is_file():
        return [target]
    _EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".ejs"}
    from codeknow.extract.detect import _is_ignored, _load_graphignore

    ignore_root = root if root is not None else target
    patterns = _load_graphignore(ignore_root)

    def _ignored(p: Path) -> bool:
        return bool(patterns and _is_ignored(p, ignore_root, patterns))

    if not follow_symlinks:
        results: list[Path] = []
        for ext in sorted(_EXTENSIONS):
            results.extend(
                p
                for p in target.rglob(f"*{ext}")
                if not any(part.startswith(".") for part in p.parts) and not _ignored(p)
            )
        return sorted(results)
    # Walk with symlink following + cycle detection
    results = []
    for dirpath, dirnames, filenames in os.walk(target, followlinks=True):
        if Path(dirpath).is_symlink():
            real = str(Path(dirpath).resolve())
            parent_real = str(Path(dirpath).parent.resolve())
            if parent_real == real or parent_real.startswith(real + os.sep):
                dirnames.clear()
                continue
        dp = Path(dirpath)
        if any(part.startswith(".") for part in dp.parts):
            dirnames.clear()
            continue
        for fname in filenames:
            p = dp / fname
            if (
                p.suffix in _EXTENSIONS
                and not fname.startswith(".")
                and not _ignored(p)
            ):
                results.append(p)
    return sorted(results)


def extract_ast(files: dict[str, list[str]], **kwargs: Any) -> dict[str, Any]:
    """Pipeline wrapper: extract AST nodes from all code files.

    Args:
        files: ``{file_type: [paths]}`` from ``detect()``.

    Returns:
        Extraction dict with ``nodes``, ``edges``, ``input_tokens``, ``output_tokens``.

    """
    code_paths = [Path(p) for p in files.get("code", [])]
    if not code_paths:
        return {"nodes": [], "edges": [], "input_tokens": 0, "output_tokens": 0}
    return extract(code_paths)
