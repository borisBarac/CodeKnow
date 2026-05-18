"""Shared relation weight taxonomy for weighted BFS graph traversal.

Single source of truth for relation weights used by:
- codeknow.vector.search._bfs_seeds (graph expansion in hybrid search)
- e2e.judge.judge (LLM judge evaluation of graph hit quality)

Edges with weight 0.0 are not traversed during BFS.
Only edges with positive weight are traversed — higher weight = higher semantic value.
"""

RELATION_WEIGHTS: dict[str, float] = {
    "imports": 0.3,
    "imports_from": 0.3,
    "contains": 0.15,
    "method": 0.3,
    "calls": 0.7,
    "uses": 0.7,
    "inherits": 0.8,
    "rationale_for": 0.9,
    "semantically_similar_to": 1.0,
}

DEFAULT_RELATION_WEIGHT: float = 0.0
