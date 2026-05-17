"""Tests for confidence_score on edges."""

from typing import Any

from codeknow.graph.build import build_from_json


def _make_extraction(**edge_overrides: Any) -> dict[str, Any]:
    """Return a minimal extraction dict with one edge of each confidence type."""
    return {
        "nodes": [
            {"id": "n_a", "label": "A", "file_type": "code", "source_file": "a.py"},
            {"id": "n_b", "label": "B", "file_type": "code", "source_file": "b.py"},
            {"id": "n_c", "label": "C", "file_type": "document", "source_file": "c.md"},
            {"id": "n_d", "label": "D", "file_type": "document", "source_file": "d.md"},
        ],
        "edges": [
            {
                "source": "n_a",
                "target": "n_b",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": "a.py",
                "weight": 1.0,
            },
            {
                "source": "n_b",
                "target": "n_c",
                "relation": "implements",
                "confidence": "INFERRED",
                "confidence_score": 0.75,
                "source_file": "b.py",
                "weight": 0.8,
            },
            {
                "source": "n_c",
                "target": "n_d",
                "relation": "references",
                "confidence": "AMBIGUOUS",
                "confidence_score": 0.2,
                "source_file": "c.md",
                "weight": 0.5,
            },
        ],
        "input_tokens": 100,
        "output_tokens": 50,
    }


def test_extracted_edges_have_score_1():
    """EXTRACTED edges must have confidence_score == 1.0."""
    G = build_from_json(_make_extraction())
    for u, v, d in G.edges(data=True):
        if d.get("confidence") == "EXTRACTED":
            assert d.get("confidence_score") == 1.0, (
                f"EXTRACTED edge ({u},{v}) should have "
                f"confidence_score=1.0, got {d.get('confidence_score')}"
            )


def test_inferred_edges_score_in_range():
    """INFERRED edges must have confidence_score between 0.0 and 1.0."""
    G = build_from_json(_make_extraction())
    found = False
    for u, v, d in G.edges(data=True):
        if d.get("confidence") == "INFERRED":
            found = True
            score = d.get("confidence_score")
            assert score is not None, (
                f"INFERRED edge ({u},{v}) missing confidence_score"
            )
            assert 0.0 <= score <= 1.0, (
                f"INFERRED edge ({u},{v}) confidence_score={score} out of range [0,1]"
            )
    assert found, "No INFERRED edges found in test fixture"


def test_ambiguous_edges_score_at_most_04():
    """AMBIGUOUS edges must have confidence_score <= 0.4."""
    G = build_from_json(_make_extraction())
    found = False
    for u, v, d in G.edges(data=True):
        if d.get("confidence") == "AMBIGUOUS":
            found = True
            score = d.get("confidence_score")
            assert score is not None, (
                f"AMBIGUOUS edge ({u},{v}) missing confidence_score"
            )
            assert score <= 0.4, (
                f"AMBIGUOUS edge ({u},{v}) confidence_score={score} should be <= 0.4"
            )
    assert found, "No AMBIGUOUS edges found in test fixture"
