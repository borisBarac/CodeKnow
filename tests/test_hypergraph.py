"""Tests for hyperedge support in build_from_json."""
from __future__ import annotations

import networkx as nx

from codeknow.graph.build import build_from_json


SAMPLE_EXTRACTION = {
    "nodes": [
        {"id": "BasicAuth", "label": "BasicAuth", "file_type": "code", "source_file": "auth.py"},
        {"id": "DigestAuth", "label": "DigestAuth", "file_type": "code", "source_file": "auth.py"},
        {"id": "Request", "label": "Request", "file_type": "code", "source_file": "http.py"},
        {"id": "Response", "label": "Response", "file_type": "code", "source_file": "http.py"},
        {"id": "BaseClient", "label": "BaseClient", "file_type": "code", "source_file": "client.py"},
    ],
    "edges": [
        {"source": "BasicAuth", "target": "Request", "relation": "uses", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "auth.py"},
    ],
    "hyperedges": [
        {
            "id": "auth_flow",
            "label": "Auth Flow",
            "nodes": ["BasicAuth", "DigestAuth", "Request", "Response", "BaseClient"],
            "relation": "participate_in",
            "confidence": "INFERRED",
            "confidence_score": 0.75,
            "source_file": "auth.py",
        }
    ],
    "input_tokens": 10,
    "output_tokens": 5,
}


def test_build_from_json_stores_hyperedges():
    G = build_from_json(SAMPLE_EXTRACTION)
    assert "hyperedges" in G.graph
    assert len(G.graph["hyperedges"]) == 1
    assert G.graph["hyperedges"][0]["id"] == "auth_flow"


def test_build_from_json_no_hyperedges():
    extraction = {**SAMPLE_EXTRACTION, "hyperedges": []}
    G = build_from_json(extraction)
    assert G.graph.get("hyperedges", []) == []


def test_build_from_json_missing_hyperedges_key():
    extraction = {k: v for k, v in SAMPLE_EXTRACTION.items() if k != "hyperedges"}
    G = build_from_json(extraction)
    assert G.graph.get("hyperedges", []) == []
