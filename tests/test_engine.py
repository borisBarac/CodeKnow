import networkx as nx
import pytest

from codeknow.graph.engine import (
    _bfs,
    _communities_from_graph,
    _dfs,
    _find_node,
    _score_nodes,
    _strip_diacritics,
    _subgraph_to_text,
    sanitize_label,
)


@pytest.fixture
def G():
    g = nx.Graph()

    g.add_node(
        "server_app",
        label="ChatApp",
        file_type="code",
        source_file="src/server/app.py",
        community=0,
    )
    g.add_node(
        "server_auth",
        label="AuthMiddleware",
        file_type="code",
        source_file="src/server/auth.py",
        community=0,
    )
    g.add_node(
        "server_routes",
        label="handleRequest()",
        file_type="code",
        source_file="src/server/routes.py",
        community=0,
    )
    g.add_node(
        "server_db",
        label="db.py",
        file_type="code",
        source_file="src/server/db.py",
        community=0,
    )

    g.add_node(
        "ui_page",
        label="Dashboard",
        file_type="code",
        source_file="src/ui/page.tsx",
        community=1,
    )
    g.add_node(
        "ui_sidebar",
        label="Sidebar",
        file_type="code",
        source_file="src/ui/sidebar.tsx",
        community=1,
    )
    g.add_node(
        "ui_hooks",
        label="useAuth()",
        file_type="code",
        source_file="src/ui/hooks.ts",
        community=1,
    )
    g.add_node(
        "ui_utils",
        label="formatDate()",
        file_type="code",
        source_file="src/ui/utils.ts",
        community=1,
    )

    g.add_node(
        "shared_types",
        label="CaféResponse",
        file_type="code",
        source_file="src/shared/types.ts",
        community=2,
    )
    g.add_node(
        "shared_config",
        label="Config",
        file_type="code",
        source_file="src/shared/config.ts",
        community=2,
    )
    g.add_node(
        "shared_logger",
        label="log()",
        file_type="code",
        source_file="src/shared/logger.ts",
        community=2,
    )

    g.add_edge(
        "server_app",
        "server_auth",
        relation="contains",
        confidence="EXTRACTED",
        source_file="src/server/app.py",
        weight=1.0,
    )
    g.add_edge(
        "server_app",
        "server_routes",
        relation="contains",
        confidence="EXTRACTED",
        source_file="src/server/app.py",
        weight=1.0,
    )
    g.add_edge(
        "server_routes",
        "server_db",
        relation="calls",
        confidence="EXTRACTED",
        source_file="src/server/routes.py",
        weight=1.0,
    )
    g.add_edge(
        "server_auth",
        "shared_config",
        relation="calls",
        confidence="INFERRED",
        source_file="src/server/auth.py",
        weight=0.8,
    )
    g.add_edge(
        "server_routes",
        "shared_logger",
        relation="calls",
        confidence="INFERRED",
        source_file="src/server/routes.py",
        weight=0.8,
    )
    g.add_edge(
        "ui_page",
        "ui_sidebar",
        relation="contains",
        confidence="EXTRACTED",
        source_file="src/ui/page.tsx",
        weight=1.0,
    )
    g.add_edge(
        "ui_page",
        "ui_hooks",
        relation="contains",
        confidence="EXTRACTED",
        source_file="src/ui/page.tsx",
        weight=1.0,
    )
    g.add_edge(
        "ui_hooks",
        "server_auth",
        relation="calls",
        confidence="INFERRED",
        source_file="src/ui/hooks.ts",
        weight=0.8,
    )
    g.add_edge(
        "ui_hooks",
        "shared_config",
        relation="calls",
        confidence="INFERRED",
        source_file="src/ui/hooks.ts",
        weight=0.8,
    )
    g.add_edge(
        "ui_utils",
        "shared_logger",
        relation="calls",
        confidence="INFERRED",
        source_file="src/ui/utils.ts",
        weight=0.8,
    )
    g.add_edge(
        "shared_config",
        "shared_types",
        relation="imports_from",
        confidence="EXTRACTED",
        source_file="src/shared/config.ts",
        weight=1.0,
    )

    return g


def test_sanitize_label_strips_control_chars():
    assert sanitize_label("hello\x00world\x1f!") == "helloworld!"


def test_sanitize_label_truncates():
    long_str = "a" * 300
    assert len(sanitize_label(long_str)) == 256


def test_sanitize_label_none():
    assert sanitize_label(None) == ""


def test_find_node_by_label(G):
    result = _find_node(G, "ChatApp")
    assert result == ["server_app"]


def test_find_node_case_insensitive(G):
    result = _find_node(G, "chatapp")
    assert result == ["server_app"]


def test_find_node_diacritic(G):
    result = _find_node(G, "cafe")
    assert "shared_types" in result


def test_find_node_by_id(G):
    result = _find_node(G, "server_app")
    assert "server_app" in result


def test_score_nodes_ranks_by_relevance(G):
    scored = _score_nodes(G, ["auth"])
    ids = [nid for _, nid in scored]
    assert ids[0] == "server_auth"


def test_bfs_depth_limit(G):
    visited, edges = _bfs(G, ["server_app"], depth=1)
    assert visited == {"server_app", "server_auth", "server_routes"}
    assert ("server_app", "server_auth") in edges or (
        "server_auth",
        "server_app",
    ) in edges


def test_bfs_full_traversal(G):
    visited, _ = _bfs(G, ["server_app"], depth=3)
    assert "shared_config" in visited
    assert "shared_types" in visited


def test_dfs_traversal(G):
    visited, _ = _dfs(G, ["server_app"], depth=5)
    bfs_visited, _ = _bfs(G, ["server_app"], depth=5)
    assert visited == bfs_visited


def test_communities_from_graph(G):
    communities = _communities_from_graph(G)
    assert set(communities.keys()) == {0, 1, 2}
    assert set(communities[0]) == {
        "server_app",
        "server_auth",
        "server_routes",
        "server_db",
    }
    assert set(communities[1]) == {
        "ui_page",
        "ui_sidebar",
        "ui_hooks",
        "ui_utils",
    }
    assert set(communities[2]) == {
        "shared_types",
        "shared_config",
        "shared_logger",
    }


def test_subgraph_to_text_renders_nodes_and_edges(G):
    nodes = {"server_app", "server_auth", "shared_config"}
    edges = [("server_app", "server_auth"), ("server_auth", "shared_config")]
    text = _subgraph_to_text(G, nodes, edges)
    assert "NODE ChatApp" in text
    assert "NODE AuthMiddleware" in text
    assert "NODE Config" in text
    assert "contains" in text
    assert "calls" in text


def test_strip_diacritics():
    assert _strip_diacritics("café") == "cafe"
    assert _strip_diacritics("naïve") == "naive"
    assert _strip_diacritics("hello") == "hello"
