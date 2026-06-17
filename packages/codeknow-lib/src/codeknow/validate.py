"""Validate extraction JSON and graph data against the codeknow schema.

Extends graph's validation with checks for new fields:
- ``chunks[]`` on nodes (optional, hash format)
- ``community`` on nodes (optional, int)
- ``confidence_score`` on edges (optional, float 0.0–1.0)
"""

from __future__ import annotations

from .schemas import ConfidenceLabel

VALID_FILE_TYPES = {"code", "document", "paper", "image", "rationale", "video"}
VALID_CONFIDENCES = {c.value for c in ConfidenceLabel}
REQUIRED_NODE_FIELDS = {"id", "label", "file_type", "source_file"}
REQUIRED_EDGE_FIELDS = {"source", "target", "relation", "confidence", "source_file"}


def validate_extraction(data: dict) -> list[str]:
    """Validate an extraction JSON dict against the codeknow schema.

    Returns a list of error strings — empty list means valid.
    """
    if not isinstance(data, dict):
        return ["Extraction must be a JSON object"]

    errors: list[str] = []

    if "nodes" not in data:
        errors.append("Missing required key 'nodes'")
    elif not isinstance(data["nodes"], list):
        errors.append("'nodes' must be a list")
    else:
        for i, node in enumerate(data["nodes"]):
            if not isinstance(node, dict):
                errors.append(f"Node {i} must be an object")
                continue
            _validate_node(i, node, errors)

    edge_list = data.get("edges") if "edges" in data else data.get("links")
    if edge_list is None:
        errors.append("Missing required key 'edges'")
    elif not isinstance(edge_list, list):
        errors.append("'edges' must be a list")
    else:
        node_ids = {
            n["id"]
            for n in (data.get("nodes") or [])
            if isinstance(n, dict) and "id" in n
        }
        for i, edge in enumerate(edge_list):
            if not isinstance(edge, dict):
                errors.append(f"Edge {i} must be an object")
                continue
            _validate_edge(i, edge, node_ids, errors)

    return errors


def _validate_node(idx: int, node: dict, errors: list[str]) -> None:
    nid = node.get("id", "?")
    for field in REQUIRED_NODE_FIELDS:
        if field not in node:
            errors.append(f"Node {idx} (id={nid!r}) missing required field '{field}'")

    if "file_type" in node and node["file_type"] not in VALID_FILE_TYPES:
        errors.append(
            f"Node {idx} (id={nid!r}) has invalid file_type "
            f"'{node['file_type']}' — must be one of {sorted(VALID_FILE_TYPES)}"
        )

    if "chunks" in node:
        chunks = node["chunks"]
        if not isinstance(chunks, list):
            errors.append(f"Node {idx} (id={nid!r}) 'chunks' must be a list")
        else:
            for j, chunk in enumerate(chunks):
                if not isinstance(chunk, dict):
                    errors.append(f"Node {idx} chunk {j} must be an object")
                elif "hash" in chunk:
                    h = chunk["hash"]
                    if (
                        not isinstance(h, str)
                        or len(h) != 64
                        or not all(c in "0123456789abcdef" for c in h)
                    ):
                        errors.append(
                            f"Node {idx} chunk {j} has invalid hash "
                            f"(expected 64-char hex SHA-256)"
                        )

    if "community" in node:
        cid = node["community"]
        if isinstance(cid, bool) or not isinstance(cid, int) or cid < 0:
            errors.append(
                f"Node {idx} (id={nid!r}) 'community' must be "
                f"a non-negative int, got {cid!r}"
            )

    if "end_line" in node:
        el = node["end_line"]
        if isinstance(el, bool) or not isinstance(el, int) or el < 1:
            errors.append(
                f"Node {idx} (id={nid!r}) 'end_line' must be a positive int, got {el!r}"
            )


def _validate_edge(idx: int, edge: dict, node_ids: set[str], errors: list[str]) -> None:
    for field in REQUIRED_EDGE_FIELDS:
        if field not in edge:
            errors.append(f"Edge {idx} missing required field '{field}'")

    if "confidence" in edge and edge["confidence"] not in VALID_CONFIDENCES:
        errors.append(
            f"Edge {idx} has invalid confidence '{edge['confidence']}' "
            f"— must be one of {sorted(VALID_CONFIDENCES)}"
        )

    if "confidence_score" in edge:
        cs = edge["confidence_score"]
        if (
            isinstance(cs, bool)
            or not isinstance(cs, (int, float))
            or not (0.0 <= cs <= 1.0)
        ):
            errors.append(f"Edge {idx} 'confidence_score' must be 0.0–1.0, got {cs!r}")

    if "source" in edge and edge["source"] not in node_ids:
        errors.append(
            f"Edge {idx} source '{edge['source']}' does not match any node id"
        )

    if "target" in edge and edge["target"] not in node_ids:
        errors.append(
            f"Edge {idx} target '{edge['target']}' does not match any node id"
        )


def assert_valid(data: dict) -> None:
    """Raise ValueError with all errors if extraction is invalid."""
    errors = validate_extraction(data)
    if errors:
        msg = f"Extraction JSON has {len(errors)} error(s):\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        raise ValueError(msg)
