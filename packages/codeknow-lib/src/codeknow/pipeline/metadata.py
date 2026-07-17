from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codeknow.chunking.index import build_reverse_index

if TYPE_CHECKING:
    from codeknow.pipeline import PipelineResult


def build_vector_metadata(
    result: PipelineResult,
    *,
    check_content: bool = True,
) -> dict[str, dict[str, Any]]:
    """Return the complete expected metadata for every embedded chunk."""
    from codeknow.vector.embeddings import read_chunk_content

    extra_metadata = build_chunk_metadata(result)
    metadata: dict[str, dict[str, Any]] = {}
    for chunks in result.chunk_map.values():
        for chunk in chunks:
            if not chunk.embeddable:
                continue
            if (
                check_content
                and not read_chunk_content(chunk, result.repo_root).strip()
            ):
                continue
            record = {
                "file": chunk.file,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content_hash": chunk.hash,
                "slug": result.config.slug,
            }
            record.update(extra_metadata.get(chunk.vector_id, {}))
            metadata[chunk.vector_id] = record
    return metadata


def build_chunk_metadata(result: PipelineResult) -> dict[str, dict[str, Any]]:
    reverse = build_reverse_index(result.graph)

    node_id_to_community: dict[str, int] = {}
    for cid, node_ids in result.communities.items():
        for nid in node_ids:
            node_id_to_community[nid] = cid

    metadata: dict[str, dict[str, Any]] = {}
    for file_chunks in result.chunk_map.values():
        for chunk in file_chunks:
            vector_id = chunk.vector_id
            node_ids = reverse.get(vector_id, [])
            if not node_ids:
                continue

            labels: list[str] = []
            community_ids: set[int] = set()
            for nid in node_ids:
                data = result.graph.nodes.get(nid)
                if data is None:
                    continue
                label = data.get("label")
                if label:
                    labels.append(label)
                cid_found = node_id_to_community.get(nid)
                if cid_found is not None:
                    community_ids.add(cid_found)

            extra: dict[str, Any] = {}
            if labels:
                extra["node_labels"] = "|".join(labels)
            if community_ids:
                extra["community_ids"] = ",".join(str(c) for c in sorted(community_ids))
            if extra:
                metadata[vector_id] = extra

    return metadata
