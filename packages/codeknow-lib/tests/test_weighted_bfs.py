"""Tests for weighted BFS in _bfs_seeds (hybrid search graph expansion).

RED/Green TDD:
- Tests 1-4: regression (pass now, must keep passing after refactor)
- Tests 5-8: RED (fail with current deque-based BFS, pass after heapq implementation)
"""

import networkx as nx
import pytest
from codeknow.vector.search import _bfs_seeds


def _graph(nodes, edges):
    """Build a small test graph.

    Args:
        nodes: list of (id, label) tuples
        edges: list of (source, target, relation) tuples

    """
    G = nx.Graph()
    for nid, label in nodes:
        G.add_node(nid, label=label)
    for src, tgt, rel in edges:
        G.add_edge(src, tgt, relation=rel)
    return G


# ── Regression tests ──────────────────────────────────────────────────


def test_zero_weight_edges_not_traversed():
    G = _graph(
        [("seed", "Seed"), ("a", "NodeA"), ("b", "NodeB")],
        [("seed", "a", "unknown_structural"), ("seed", "b", "untyped_edge")],
    )
    result = _bfs_seeds(G, ["seed"], depth=2)
    assert result == {}


def test_paths_include_labels_and_arrows():
    G = _graph(
        [("seed", "AuthService"), ("a", "TokenValidator")],
        [("seed", "a", "calls")],
    )
    result = _bfs_seeds(G, ["seed"], depth=2)
    path, weight = result["a"]
    assert path == ["AuthService", "→calls→", "TokenValidator"]
    assert weight == 0.7


def test_depth_limit_respected():
    G = _graph(
        [("seed", "Seed"), ("a", "A"), ("b", "B"), ("c", "C")],
        [("seed", "a", "calls"), ("a", "b", "calls"), ("b", "c", "calls")],
    )
    result = _bfs_seeds(G, ["seed"], depth=1)
    assert "a" in result
    assert "b" not in result
    assert "c" not in result


def test_seed_cap_large_graph():
    G = nx.Graph()
    seeds = []
    for i in range(60):
        sid, tid = f"s{i}", f"t{i}"
        G.add_node(sid, label=f"S{i}")
        G.add_node(tid, label=f"T{i}")
        G.add_edge(sid, tid, relation="calls")
        seeds.append(sid)
    for i in range(5000):
        G.add_node(f"f{i}", label=f"F{i}")
    assert G.number_of_nodes() > 5000
    result = _bfs_seeds(G, seeds, depth=1)
    assert len(result) == 50


# ── RED tests (fail with current code, pass with weighted BFS) ──────────


def test_high_weight_explored_first():
    G = _graph(
        [("seed", "Seed"), ("a", "NodeA"), ("b", "NodeB")],
        [("seed", "a", "calls"), ("seed", "b", "semantically_similar_to")],
    )
    result = _bfs_seeds(G, ["seed"], depth=2)
    assert list(result.keys()) == ["b", "a"]


def test_cumulative_weight_prioritizes_semantic_paths():
    G = _graph(
        [
            ("seed", "Seed"),
            ("a", "A"),
            ("b", "B"),
            ("c", "C"),
            ("d", "D"),
            ("e", "E"),
        ],
        [
            ("seed", "a", "semantically_similar_to"),
            ("seed", "b", "calls"),
            ("seed", "c", "calls"),
            ("a", "d", "calls"),
            ("b", "e", "calls"),
        ],
    )
    result = _bfs_seeds(G, ["seed"], depth=2)
    assert list(result.keys()) == ["a", "d", "b", "e", "c"]


def test_unknown_relation_skipped():
    G = _graph(
        [("seed", "Seed"), ("a", "NodeA")],
        [("seed", "a", "brand_new_relation")],
    )
    result = _bfs_seeds(G, ["seed"], depth=2)
    assert result == {}


def test_max_graph_results_budget():
    G = nx.Graph()
    G.add_node("seed", label="Seed")
    for i in range(60):
        G.add_node(f"n{i}", label=f"N{i}")
        G.add_edge("seed", f"n{i}", relation="calls")
    result = _bfs_seeds(G, ["seed"], depth=1, max_results=50)
    assert len(result) == 50


def test_complex_multi_seed_mixed_relations():
    G = _graph(
        [
            ("seed1", "S1"), ("seed2", "S2"), ("seed3", "S3"),
            ("a", "A"), ("b", "B"), ("c", "C"),
            ("d", "D"), ("e", "E"), ("f", "F"),
            ("g", "G"), ("h", "H"), ("i", "I"),
            ("j", "J"), ("k", "K"), ("l", "L"),
            ("m", "M"), ("n", "N"), ("o", "O"),
            ("p", "P"), ("q", "Q"), ("r", "R"),
            ("s", "S"), ("t", "T"),
        ],
        [
            ("seed1", "a", "semantically_similar_to"),
            ("seed1", "b", "semantically_similar_to"),
            ("seed1", "c", "calls"),
            ("a", "d", "calls"),
            ("b", "e", "inherits"),
            ("c", "f", "calls"),
            ("d", "j", "calls"),
            ("e", "k", "rationale_for"),
            ("seed2", "g", "inherits"),
            ("seed2", "i", "rationale_for"),
            ("g", "h", "semantically_similar_to"),
            ("h", "l", "calls"),
            ("i", "m", "calls"),
            ("m", "n", "calls"),
            ("seed3", "o", "semantically_similar_to"),
            ("seed3", "q", "inherits"),
            ("seed3", "t", "calls"),
            ("o", "p", "calls"),
            ("q", "r", "rationale_for"),
            ("r", "s", "calls"),
        ],
    )
    result = _bfs_seeds(G, ["seed1", "seed2", "seed3"], depth=3)

    expected_order = [
        "a", "d", "j", "b", "e", "k", "c", "f",
        "i", "m", "n", "g", "h", "l", "o", "p",
        "q", "r", "s", "t",
    ]
    assert list(result.keys()) == expected_order

    _, weight_a = result["a"]
    assert weight_a == 1.0
    _, weight_k = result["k"]
    assert weight_k == 2.7
    _, weight_e = result["e"]
    assert weight_e == pytest.approx(1.8)

    path_j, weight_j = result["j"]
    assert path_j == ["S1", "→semantically_similar_to→", "A", "→calls→", "D", "→calls→", "J"]
    assert weight_j == pytest.approx(2.4)
