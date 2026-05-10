"""Tests for semantically_similar_to edge support."""

import networkx as nx
import pytest

from codeknow.graph.analyze import _surprise_score
from codeknow.graph.build import build_from_json


def _make_extraction_with_semantic_edge():
    """Two nodes in separate files connected by a semantically_similar_to edge."""
    return {
        "nodes": [
            {
                "id": "a_validate_input",
                "label": "validate_input",
                "file_type": "code",
                "source_file": "auth/validators.py",
                "source_location": "L5",
            },
            {
                "id": "b_check_input",
                "label": "check_input",
                "file_type": "code",
                "source_file": "api/checks.py",
                "source_location": "L12",
            },
        ],
        "edges": [
            {
                "source": "a_validate_input",
                "target": "b_check_input",
                "relation": "semantically_similar_to",
                "confidence": "INFERRED",
                "confidence_score": 0.82,
                "source_file": "auth/validators.py",
                "source_location": None,
                "weight": 0.82,
            }
        ],
        "input_tokens": 100,
        "output_tokens": 50,
    }


def _make_graph_with_semantic_edge():
    return build_from_json(_make_extraction_with_semantic_edge())


def _make_two_edge_graph():
    """Graph with one semantically_similar_to edge and one references edge, both cross-file."""  # noqa: E501
    G = nx.Graph()
    for nid, label, src in [
        ("a", "ValidateInput", "auth/validators.py"),
        ("b", "CheckInput", "api/checks.py"),
        ("c", "LoadConfig", "config/loader.py"),
        ("d", "ReadConfig", "utils/reader.py"),
    ]:
        G.add_node(nid, label=label, source_file=src, file_type="code")
    # semantically_similar_to edge
    G.add_edge(
        "a",
        "b",
        relation="semantically_similar_to",
        confidence="INFERRED",
        confidence_score=0.82,
        source_file="auth/validators.py",
        weight=0.82,
        _src="a",
        _tgt="b",
    )
    # plain references edge (same confidence tier)
    G.add_edge(
        "c",
        "d",
        relation="references",
        confidence="INFERRED",
        confidence_score=0.7,
        source_file="config/loader.py",
        weight=0.7,
        _src="c",
        _tgt="d",
    )
    return G


def test_semantic_edge_survives_build_from_json():
    G = _make_graph_with_semantic_edge()
    assert G.number_of_edges() == 1
    _u, _v, data = next(iter(G.edges(data=True)))
    assert data["relation"] == "semantically_similar_to"


def test_semantic_edge_nodes_present():
    G = _make_graph_with_semantic_edge()
    assert "a_validate_input" in G.nodes
    assert "b_check_input" in G.nodes


def test_semantic_edge_confidence_score_preserved():
    G = _make_graph_with_semantic_edge()
    _u, _v, data = next(iter(G.edges(data=True)))
    assert data.get("confidence_score") == pytest.approx(0.82)
    assert data.get("confidence") == "INFERRED"


def test_semantic_edge_scores_higher_than_references():
    G = _make_two_edge_graph()
    node_community = {"a": 0, "b": 0, "c": 1, "d": 1}

    score_sem, _reasons_sem = _surprise_score(
        G,
        "a",
        "b",
        G.edges["a", "b"],
        node_community,
        "auth/validators.py",
        "api/checks.py",
    )
    score_ref, _ = _surprise_score(
        G,
        "c",
        "d",
        G.edges["c", "d"],
        node_community,
        "config/loader.py",
        "utils/reader.py",
    )
    assert score_sem > score_ref


def test_semantic_edge_reason_mentions_similarity():
    G = _make_two_edge_graph()
    node_community = {"a": 0, "b": 0, "c": 1, "d": 1}

    _, reasons = _surprise_score(
        G,
        "a",
        "b",
        G.edges["a", "b"],
        node_community,
        "auth/validators.py",
        "api/checks.py",
    )
    assert any("similar" in r for r in reasons)
