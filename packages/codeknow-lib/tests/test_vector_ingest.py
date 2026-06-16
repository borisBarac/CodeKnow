from __future__ import annotations

import pytest
from codeknow.schemas import Chunk
from codeknow.vector.embeddings import EmbeddingConfig
from codeknow.vector.ingest import embed_chunk_batches, embed_chunk_map_only


class RecordingEmbeddings:
    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.requests.append(list(texts))
        return [[float(len(text))] for text in texts]


def _chunk(file: str, hash_char: str, start_line: int = 1, end_line: int = 1) -> Chunk:
    return Chunk(
        file=file,
        start_line=start_line,
        end_line=end_line,
        hash=hash_char * 64,
    )


def test_embed_chunk_map_only_embeds_chunks_without_graph_or_chroma(tmp_path):
    first = tmp_path / "first.py"
    first.write_text("one\ntwo\n", encoding="utf-8")
    second = tmp_path / "second.py"
    second.write_text("three\n", encoding="utf-8")

    embeddings = RecordingEmbeddings()
    config = EmbeddingConfig(max_request_tokens=None)

    stats = embed_chunk_map_only(
        {
            str(first): [_chunk(str(first), "a", 1, 2)],
            str(second): [_chunk(str(second), "b")],
        },
        embeddings,  # type: ignore[arg-type]
        config,
        batch_size=10,
    )

    assert embeddings.requests == [["one\ntwo\n", "three\n"]]
    assert stats.chunks_seen == 2
    assert stats.chunks_embedded == 2
    assert stats.embedding_requests == 1
    assert stats.provider == "docker"
    assert stats.model == "ai/qwen3-embedding:4B"


def test_embed_chunk_batches_builds_metadata_and_vectors(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("line one\nline two\n", encoding="utf-8")
    chunk = _chunk(str(source), "a", 1, 2)

    embeddings = RecordingEmbeddings()
    batches = list(
        embed_chunk_batches(
            [chunk],
            embeddings,  # type: ignore[arg-type]
            EmbeddingConfig(max_request_tokens=None),
            slug="owner-repo",
            extra_metadata={chunk.hash: {"node_labels": "Node"}},
        )
    )

    assert len(batches) == 1
    batch = batches[0]
    assert batch.ids == [chunk.hash]
    assert batch.texts == ["line one\nline two\n"]
    assert batch.vectors == [[18.0]]
    assert batch.embedding_requests == 1
    assert batch.metadatas == [
        {
            "file": str(source),
            "start_line": 1,
            "end_line": 2,
            "slug": "owner-repo",
            "node_labels": "Node",
        }
    ]


def test_embed_chunk_map_only_skips_empty_content_and_deduplicates(tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("\n", encoding="utf-8")
    real = tmp_path / "real.py"
    real.write_text("content\n", encoding="utf-8")

    embeddings = RecordingEmbeddings()
    real_chunk = _chunk(str(real), "a")

    stats = embed_chunk_map_only(
        {
            str(empty): [_chunk(str(empty), "b")],
            str(real): [real_chunk, real_chunk],
        },
        embeddings,  # type: ignore[arg-type]
        EmbeddingConfig(max_request_tokens=None),
    )

    assert embeddings.requests == [["content\n"]]
    assert stats.chunks_seen == 2
    assert stats.chunks_embedded == 1
    assert stats.embedding_requests == 1


def test_embed_chunk_map_only_counts_token_budgeted_requests(tmp_path):
    first = tmp_path / "first.py"
    first.write_text("a" * 9, encoding="utf-8")
    second = tmp_path / "second.py"
    second.write_text("b" * 9, encoding="utf-8")
    third = tmp_path / "third.py"
    third.write_text("c" * 9, encoding="utf-8")

    embeddings = RecordingEmbeddings()
    stats = embed_chunk_map_only(
        {
            str(first): [_chunk(str(first), "a")],
            str(second): [_chunk(str(second), "b")],
            str(third): [_chunk(str(third), "c")],
        },
        embeddings,  # type: ignore[arg-type]
        EmbeddingConfig(max_request_tokens=6, token_safety_margin=0),
        batch_size=3,
    )

    assert embeddings.requests == [["a" * 9, "b" * 9], ["c" * 9]]
    assert stats.chunks_embedded == 3
    assert stats.embedding_requests == 2


def test_embed_chunk_batches_skips_oversized_single_chunk(tmp_path, caplog):
    source = tmp_path / "source.py"
    source.write_text("x" * 21, encoding="utf-8")
    chunk = _chunk(str(source), "a")

    embeddings = RecordingEmbeddings()
    with caplog.at_level("WARNING", logger="codeknow.vector.ingest"):
        batches = list(
            embed_chunk_batches(
                [chunk],
                embeddings,  # type: ignore[arg-type]
                EmbeddingConfig(max_request_tokens=6, token_safety_margin=0),
            )
        )

    assert batches == []
    assert embeddings.requests == []
    assert "Skipping chunk" in caplog.text
    assert "exceeds embedding request budget" in caplog.text


def test_embed_chunk_map_only_counts_only_embedded_oversized_chunks(tmp_path):
    valid = tmp_path / "valid.py"
    valid.write_text("a" * 9, encoding="utf-8")
    oversized = tmp_path / "oversized.py"
    oversized.write_text("x" * 21, encoding="utf-8")

    embeddings = RecordingEmbeddings()
    stats = embed_chunk_map_only(
        {
            str(valid): [_chunk(str(valid), "a")],
            str(oversized): [_chunk(str(oversized), "b")],
        },
        embeddings,  # type: ignore[arg-type]
        EmbeddingConfig(max_request_tokens=6, token_safety_margin=0),
        batch_size=2,
    )

    assert embeddings.requests == [["a" * 9]]
    assert stats.chunks_seen == 2
    assert stats.chunks_embedded == 1
    assert stats.embedding_requests == 1


def test_embed_chunk_map_only_rejects_invalid_batch_size():
    with pytest.raises(ValueError, match="batch_size"):
        embed_chunk_map_only({}, RecordingEmbeddings(), batch_size=0)  # type: ignore[arg-type]
