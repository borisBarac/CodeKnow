"""Node → code chunk mapping via SHA-256 hashes.

This is the bridge between graph retrieval and vector retrieval. The ``chunk.hash``
field is the join key — both systems reference the same chunk hashes.

Chunk creation strategy:
- Code files: AST-aware chunking (tree-sitter top-level node boundaries)
- Doc/non-code files: naive line-based chunking
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import TYPE_CHECKING

from codeknow.schemas import Chunk, ChunkMap

if TYPE_CHECKING:
    import networkx as nx

DEFAULT_CHUNK_SIZE = 100
DEFAULT_OVERLAP = 20

_AST_CONFIGS: dict[str, tuple[str, str]] = {
    ".py": ("tree_sitter_python", "language"),
    ".js": ("tree_sitter_javascript", "language"),
    ".mjs": ("tree_sitter_javascript", "language"),
    ".ejs": ("tree_sitter_javascript", "language"),
    ".ts": ("tree_sitter_typescript", "language_typescript"),
    ".tsx": ("tree_sitter_typescript", "language_typescript"),
}

_STRUCTURAL_TYPES: dict[str, frozenset] = {
    ".py": frozenset(
        {
            "class_definition",
            "function_definition",
            "decorated_definition",
        }
    ),
    ".js": frozenset(
        {
            "class_declaration",
            "function_declaration",
            "lexical_declaration",
            "export_statement",
        }
    ),
    ".mjs": frozenset(
        {
            "class_declaration",
            "function_declaration",
            "lexical_declaration",
            "export_statement",
        }
    ),
    ".ejs": frozenset(
        {
            "class_declaration",
            "function_declaration",
            "lexical_declaration",
            "export_statement",
        }
    ),
    ".ts": frozenset(
        {
            "class_declaration",
            "function_declaration",
            "lexical_declaration",
            "export_statement",
            "abstract_class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "module",
        }
    ),
    ".tsx": frozenset(
        {
            "class_declaration",
            "function_declaration",
            "lexical_declaration",
            "export_statement",
            "abstract_class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "module",
        }
    ),
}

_CODE_EXTENSIONS = set(_AST_CONFIGS.keys())


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def chunk_file_ast(
    path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split a code file into chunks aligned to AST node boundaries.

    Walks top-level tree-sitter nodes and groups them into chunks that
    respect class/function boundaries. Falls back to linear chunking on
    parse failure.
    """
    p = Path(path)
    ext = p.suffix.lower()
    config = _AST_CONFIGS.get(ext)
    if config is None:
        return chunk_file_linear(path, chunk_size, overlap)

    ts_module, ts_lang_fn = config
    structural = _STRUCTURAL_TYPES.get(ext, frozenset())

    try:
        mod = importlib.import_module(ts_module)
        from tree_sitter import Language, Parser

        lang_fn = getattr(mod, ts_lang_fn, None)
        if lang_fn is None:
            lang_fn = getattr(mod, "language", None)
        if lang_fn is None:
            return chunk_file_linear(path, chunk_size, overlap)
        language = Language(lang_fn())
        parser = Parser(language)
        source = p.read_bytes()
        tree = parser.parse(source)
    except Exception:
        return chunk_file_linear(path, chunk_size, overlap)

    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        return [Chunk(file=path, start_line=1, end_line=1, hash=_hash_content(""))]

    spans: list[tuple[int, int]] = []
    for child in tree.root_node.children:
        start_line = child.start_point[0] + 1
        end_line = child.end_point[0] + 1

        if child.type in structural:
            spans.append((start_line, end_line))
        elif spans and spans[-1][1] == start_line - 1:
            prev_start, _ = spans[-1]
            spans[-1] = (prev_start, end_line)
        else:
            spans.append((start_line, end_line))

    if not spans:
        spans = [(1, total_lines)]

    chunks: list[Chunk] = []
    for s_start, s_end in spans:
        s_end = min(s_end, total_lines)
        line_count = s_end - s_start + 1

        if line_count <= chunk_size:
            content = _lines_content(lines, s_start, s_end)
            chunks.append(
                Chunk(
                    file=path,
                    start_line=s_start,
                    end_line=s_end,
                    hash=_hash_content(content),
                )
            )
        else:
            pos = s_start
            while pos <= s_end:
                end = min(pos + chunk_size - 1, s_end)
                content = _lines_content(lines, pos, end)
                chunks.append(
                    Chunk(
                        file=path,
                        start_line=pos,
                        end_line=end,
                        hash=_hash_content(content),
                    )
                )
                if end >= s_end:
                    break
                pos += chunk_size - overlap

    return chunks or chunk_file_linear(path, chunk_size, overlap)


def _lines_content(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def chunk_file_linear(
    path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split a file into overlapping line-based chunks.

    Used for docs/markdown/non-code files where AST doesn't apply.
    """
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError:
        return []

    total = len(lines)
    if total == 0:
        return [Chunk(file=path, start_line=1, end_line=1, hash=_hash_content(""))]

    chunks: list[Chunk] = []
    start = 0
    while start < total:
        end = min(start + chunk_size, total)
        content = "".join(lines[start:end])
        chunks.append(
            Chunk(
                file=path,
                start_line=start + 1,
                end_line=end,
                hash=_hash_content(content),
            )
        )
        if end >= total:
            break
        start += chunk_size - overlap

    return chunks


def build_chunk_map(
    files: dict[str, list[str]],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> ChunkMap:
    """Build a ChunkMap from all discovered files.

    Routes code files (.py/.js/.ts/etc) through AST-aware chunking
    and all other files through naive line-based chunking.
    """
    chunk_map: ChunkMap = {}
    for file_list in files.values():
        for fpath in file_list:
            ext = Path(fpath).suffix.lower()
            if ext in _CODE_EXTENSIONS:
                chunks = chunk_file_ast(fpath, chunk_size, overlap)
            else:
                chunks = chunk_file_linear(fpath, chunk_size, overlap)
            if chunks:
                chunk_map[fpath] = chunks
    return chunk_map


def _parse_source_location(loc: str) -> int | None:
    """Parse ``'L42'`` → ``42``. Returns None if unparseable."""
    if isinstance(loc, str) and loc.startswith("L"):
        try:
            return int(loc[1:])
        except ValueError:
            return None
    return None


def resolve_node_chunks(
    node_data: dict,
    chunk_map: ChunkMap,
) -> list[str]:
    """Find overlapping chunk hashes for a node.

    Given a node with ``source_file`` + ``source_location`` (start line),
    find all chunks whose line range overlaps with the node.
    """
    source_file = node_data.get("source_file", "")
    if not source_file:
        return []

    start_line = _parse_source_location(node_data.get("source_location", ""))
    if start_line is None:
        return []

    end_line = node_data.get("end_line", start_line)

    chunks = chunk_map.get(source_file, [])
    overlapping: list[str] = []
    for chunk in chunks:
        if chunk.start_line <= end_line and chunk.end_line >= start_line:
            overlapping.append(chunk.hash)
    return overlapping


def map_chunks(
    graph: nx.Graph,
    files: dict[str, list[str]],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> tuple[nx.Graph, ChunkMap]:
    """Pipeline stage: link graph nodes to code chunk hashes.

    1. Chunk all source files → build ChunkMap
    2. For each node, find overlapping chunks
    3. Write ``chunks`` list onto each node

    Returns the enriched graph and the chunk_map.
    """
    chunk_map = build_chunk_map(files, chunk_size, overlap)

    for _nid, data in graph.nodes(data=True):
        hashes = resolve_node_chunks(data, chunk_map)
        data["chunks"] = [{"hash": h} for h in hashes]

    return graph, chunk_map


def build_reverse_index(graph: nx.Graph) -> dict[str, list[str]]:
    """Build hash → [node_ids] reverse index from the graph's node chunks.

    Used for vector search → graph node lookup.
    """
    index: dict[str, list[str]] = {}
    for nid, data in graph.nodes(data=True):
        for chunk in data.get("chunks", []):
            h = chunk.get("hash")
            if h:
                index.setdefault(h, []).append(nid)
    return index
