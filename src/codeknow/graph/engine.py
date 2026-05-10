# MCP stdio server - exposes graph query tools to Claude and other agents
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_LABEL_LEN = 256


def sanitize_label(text: str | None) -> str:
    if text is None:
        return ""
    text = _CONTROL_CHAR_RE.sub("", str(text))
    if len(text) > _MAX_LABEL_LEN:
        text = text[:_MAX_LABEL_LEN]
    return text


def _load_graph(graph_path: str) -> nx.Graph:
    try:
        resolved = Path(graph_path).resolve()
        if resolved.suffix != ".json":
            msg = f"Graph path must be a .json file, got: {graph_path!r}"
            raise ValueError(msg)  # noqa: TRY301
        if not resolved.exists():
            msg = f"Graph file not found: {resolved}"
            raise FileNotFoundError(msg)  # noqa: TRY301
        safe = resolved
        data = json.loads(safe.read_text(encoding="utf-8"))
        try:
            return json_graph.node_link_graph(data, edges="links")  # type: ignore[no-any-return]
        except TypeError:
            return json_graph.node_link_graph(data)  # type: ignore[no-any-return]
    except (ValueError, FileNotFoundError):
        sys.exit(1)


def _communities_from_graph(G: nx.Graph) -> dict[int, list[str]]:
    """Reconstruct community dict from community property stored on nodes."""
    communities: dict[int, list[str]] = {}
    for node_id, data in G.nodes(data=True):
        cid = data.get("community")
        if cid is not None:
            communities.setdefault(int(cid), []).append(node_id)
    return communities


def _strip_diacritics(text: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _score_nodes(G: nx.Graph, terms: list[str]) -> list[tuple[float, str]]:
    scored = []
    norm_terms = [_strip_diacritics(t).lower() for t in terms]
    for nid, data in G.nodes(data=True):
        norm_label = (
            data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()
        )
        source = (data.get("source_file") or "").lower()
        score = sum(1 for t in norm_terms if t in norm_label) + sum(
            0.5 for t in norm_terms if t in source
        )
        if score > 0:
            scored.append((score, nid))
    return sorted(scored, reverse=True)


def _bfs(
    G: nx.Graph, start_nodes: list[str], depth: int
) -> tuple[set[str], list[tuple]]:
    visited: set[str] = set(start_nodes)
    frontier = set(start_nodes)
    edges_seen: list[tuple] = []
    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            for neighbor in G.neighbors(n):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
                    edges_seen.append((n, neighbor))
        visited.update(next_frontier)
        frontier = next_frontier
    return visited, edges_seen


def _dfs(
    G: nx.Graph, start_nodes: list[str], depth: int
) -> tuple[set[str], list[tuple]]:
    visited: set[str] = set()
    edges_seen: list[tuple] = []
    stack = [(n, 0) for n in reversed(start_nodes)]
    while stack:
        node, d = stack.pop()
        if node in visited or d > depth:
            continue
        visited.add(node)
        for neighbor in G.neighbors(node):
            if neighbor not in visited:
                stack.append((neighbor, d + 1))
                edges_seen.append((node, neighbor))
    return visited, edges_seen


def _subgraph_to_text(
    G: nx.Graph, nodes: set[str], edges: list[tuple], token_budget: int = 2000
) -> str:
    """Render subgraph as text, cutting at token_budget (approx 3 chars/token)."""
    char_budget = token_budget * 3
    lines = []
    for nid in sorted(nodes, key=G.degree, reverse=True):
        d = G.nodes[nid]
        label = sanitize_label(d.get("label", nid))
        src = d.get("source_file", "")
        loc = d.get("source_location", "")
        comm = d.get("community", "")
        line = f"NODE {label} [src={src} loc={loc} community={comm}]"
        lines.append(line)
    for u, v in edges:
        if u in nodes and v in nodes:
            raw = G[u][v]
            d = (
                next(iter(raw.values()), {})
                if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph))
                else raw
            )
            ul = sanitize_label(G.nodes[u].get("label", u))
            vl = sanitize_label(G.nodes[v].get("label", v))
            rel = d.get("relation", "")
            conf = d.get("confidence", "")
            line = f"EDGE {ul} --{rel} [{conf}]--> {vl}"
            lines.append(line)
    output = "\n".join(lines)
    if len(output) > char_budget:
        output = (
            output[:char_budget] + f"\n... (truncated to ~{token_budget} token budget)"
        )
    return output


def _find_node(G: nx.Graph, label: str) -> list[str]:
    """Return node IDs whose label or ID matches the search term
    (diacritic-insensitive).
    """
    term = _strip_diacritics(label).lower()
    return [
        nid
        for nid, d in G.nodes(data=True)
        if term
        in (d.get("norm_label") or _strip_diacritics(d.get("label") or "").lower())
        or term == nid.lower()
    ]


def _filter_blank_stdin() -> None:
    """Filter blank lines from stdin before MCP reads it.

    Some MCP clients (Claude Desktop, etc.) send blank lines between JSON
    messages. The MCP stdio transport tries to parse every line as a
    JSONRPCMessage, so a bare newline triggers a Pydantic ValidationError.
    This installs an OS-level pipe that relays stdin while dropping blanks.
    """
    import os
    import threading

    r_fd, w_fd = os.pipe()
    saved_fd = os.dup(sys.stdin.fileno())

    def _relay() -> None:
        try:
            with open(saved_fd, "rb") as src, open(w_fd, "wb") as dst:  # noqa: PTH123
                for line in src:
                    if line.strip():
                        dst.write(line)
                        dst.flush()
        except Exception:
            pass

    threading.Thread(target=_relay, daemon=True).start()
    os.dup2(r_fd, sys.stdin.fileno())
    os.close(r_fd)
    sys.stdin = open(0, closefd=False)
