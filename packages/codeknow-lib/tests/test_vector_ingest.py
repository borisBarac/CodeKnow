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
    config = EmbeddingConfig()

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
            EmbeddingConfig(),
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
        EmbeddingConfig(),
    )

    assert embeddings.requests == [["content\n"]]
    assert stats.chunks_seen == 2
    assert stats.chunks_embedded == 1
    assert stats.embedding_requests == 1


def test_embed_chunk_batches_skips_context_length_failures(tmp_path):
    class ContextLengthError(Exception):
        status_code = 400

    class FailingSecondEmbeddings:
        def __init__(self) -> None:
            self.requests: list[list[str]] = []

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.requests.append(list(texts))
            if "bad" in texts:
                msg = "context length exceeded"
                raise ContextLengthError(msg)
            return [[float(len(text))] for text in texts]

    good = tmp_path / "good.py"
    good.write_text("good", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("bad", encoding="utf-8")

    embeddings = FailingSecondEmbeddings()
    batches = list(
        embed_chunk_batches(
            [_chunk(str(good), "a"), _chunk(str(bad), "b")],
            embeddings,  # type: ignore[arg-type]
            EmbeddingConfig(
                max_embedding_split_depth=0,
            ),
        )
    )

    assert len(batches) == 1
    assert batches[0].ids == ["a" * 64]
    assert batches[0].texts == ["good"]
    assert batches[0].vectors == [[4.0]]


def test_embed_chunk_batches_reraises_non_context_bad_request(tmp_path):
    class BadRequestError(Exception):
        status_code = 400

    class MisconfiguredEmbeddings:
        def __init__(self) -> None:
            self.requests: list[list[str]] = []

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.requests.append(list(texts))
            msg = "model not found"
            raise BadRequestError(msg)

    source = tmp_path / "source.py"
    source.write_text("content", encoding="utf-8")
    embeddings = MisconfiguredEmbeddings()

    with pytest.raises(BadRequestError, match="model not found"):
        list(
            embed_chunk_batches(
                [_chunk(str(source), "a")],
                embeddings,  # type: ignore[arg-type]
                EmbeddingConfig(),
            )
        )

    assert embeddings.requests == [["content"]]


def test_embed_chunk_map_only_rejects_invalid_batch_size():
    with pytest.raises(ValueError, match="batch_size"):
        embed_chunk_map_only(
            {},
            RecordingEmbeddings(),  # type: ignore[arg-type]
            batch_size=0,
        )
