import logging
from pathlib import Path

import pytest

from codeknow.extract.ast import extract_ast
from codeknow.extract.detect import detect
from codeknow.graph.build import build
from codeknow.graph.cluster import cluster, cohesion_score

CODE_TEST_SMALL = Path(__file__).parent / "code-test-small"
logger = logging.getLogger(__name__)


def _run_pipeline():
    root = CODE_TEST_SMALL
    logger.info("Running pipeline on %s", root)
    discovery = detect(root)
    logger.info(
        "detect(): %d code files, %d total",
        len(discovery["files"].get("code", [])),
        discovery.get("total_files", 0),
    )
    extraction = extract_ast(discovery["files"])
    logger.info(
        "extract_ast(): %d nodes, %d edges",
        len(extraction["nodes"]),
        len(extraction["edges"]),
    )
    G = build([extraction])
    logger.info("build(): %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    communities = cluster(G)
    logger.info("cluster(): %d communities", len(communities))
    return G, communities, discovery, extraction


@pytest.fixture(scope="module")
def pipeline():
    return _run_pipeline()


def test_detect_finds_code_files(pipeline):
    _, _, discovery, _ = pipeline
    code_files = discovery["files"].get("code", [])
    logger.info("code files: %d", len(code_files))
    assert len(code_files) > 0


def test_graph_summary(pipeline):
    G, communities, _discovery, _extraction = pipeline
    logger.info("=== Node labels ===")
    for nid, data in G.nodes(data=True):
        logger.info(
            "  %s: label=%r file_type=%s",
            nid,
            data.get("label"),
            data.get("file_type"),
        )
    logger.info("=== Edge relations ===")
    for u, v, data in G.edges(data=True):
        logger.info("  %s -> %s: relation=%s", u, v, data.get("relation"))
    logger.info("=== Community sizes ===")
    for cid, members in communities.items():
        logger.info("  community %d: %d nodes", cid, len(members))


def test_graph_has_file_nodes(pipeline):
    G, _, _, _ = pipeline
    file_nodes = [
        (nid, d)
        for nid, d in G.nodes(data=True)
        if d.get("file_type") == "code" and "." in d.get("label", "")
    ]
    logger.info("file nodes: %d", len(file_nodes))
    for nid, d in file_nodes:
        logger.info("  %s: %s", nid, d.get("label"))
    assert len(file_nodes) > 0


def test_graph_has_class_nodes(pipeline):
    G, _, _, _ = pipeline
    class_like = [
        (nid, d)
        for nid, d in G.nodes(data=True)
        if d.get("file_type") == "code"
        and "(" not in d.get("label", "")
        and "." not in d.get("label", "")
    ]
    logger.info("class-like nodes: %d", len(class_like))
    for nid, d in class_like:
        logger.info("  %s: %s", nid, d.get("label"))
    assert len(class_like) > 0


def test_graph_has_function_nodes(pipeline):
    G, _, _, _ = pipeline
    func_nodes = [
        (nid, d)
        for nid, d in G.nodes(data=True)
        if d.get("file_type") == "code" and "()" in d.get("label", "")
    ]
    logger.info("function nodes: %d", len(func_nodes))
    for nid, d in func_nodes:
        logger.info("  %s: %s", nid, d.get("label"))
    assert len(func_nodes) > 0


def test_graph_has_contains_edges(pipeline):
    G, _, _, _ = pipeline
    contains_edges = [
        (u, v) for u, v, d in G.edges(data=True) if d.get("relation") == "contains"
    ]
    logger.info("contains edges: %d", len(contains_edges))
    assert len(contains_edges) > 0


def test_graph_has_import_edges(pipeline):
    _, _, _, extraction = pipeline
    import_edges = [
        e for e in extraction["edges"] if e.get("relation") == "imports_from"
    ]
    logger.info("imports_from edges: %d", len(import_edges))
    assert len(import_edges) > 0


def test_cluster_covers_all_nodes(pipeline):
    G, communities, _, _ = pipeline
    all_members = []
    for members in communities.values():
        all_members.extend(members)
    assert set(all_members) == set(G.nodes())
    assert len(all_members) == G.number_of_nodes()
    logger.info(
        "cluster covers %d/%d nodes in %d communities",
        len(all_members),
        G.number_of_nodes(),
        len(communities),
    )


def test_cluster_has_valid_cohesion(pipeline):
    G, communities, _, _ = pipeline
    for cid, members in communities.items():
        score = cohesion_score(G, members)
        logger.info(
            "community %d (%d nodes): cohesion=%.2f",
            cid,
            len(members),
            score,
        )
        assert 0.0 <= score <= 1.0


def test_graph_node_count_is_positive(pipeline):
    G, _, _, _ = pipeline
    logger.info(
        "graph has %d nodes, %d edges",
        G.number_of_nodes(),
        G.number_of_edges(),
    )
    assert G.number_of_nodes() > 10
