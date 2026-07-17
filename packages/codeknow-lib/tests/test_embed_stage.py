from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest
from codeknow.pipeline import PipelineConfig, PipelineResult
from codeknow.pipeline.embed_stage import embed
from codeknow.pipeline.metadata import build_chunk_metadata
from codeknow.schemas import Chunk
from codeknow.vector.embeddings import (
    EmbeddingConfig,
    EmbeddingContextLengthError,
    _merge_split_embedding_vectors,
    embed_texts,
)


def _make_config(**overrides: Any) -> PipelineConfig:
    defaults = {"repo_url": "https://github.com/owner/repo.git"}
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def _make_result(config: PipelineConfig | None = None) -> PipelineResult:
    config = config or _make_config()

    G = nx.Graph()
    G.add_node(
        "n1",
        label="Authenticate",
        source_file="auth.py",
        source_location="L10",
        end_line=30,
        community=1,
    )
    G.add_node(
        "n2",
        label="ValidateToken",
        source_file="auth.py",
        source_location="L35",
        end_line=50,
        community=1,
    )
    G.add_node(
        "n3",
        label="Helper",
        source_file="util.py",
        source_location="L1",
        end_line=5,
        community=2,
    )

    chunk_a = Chunk(file="auth.py", start_line=1, end_line=30, hash="a" * 64)
    chunk_b = Chunk(file="auth.py", start_line=31, end_line=60, hash="b" * 64)
    chunk_c = Chunk(file="util.py", start_line=1, end_line=10, hash="c" * 64)

    G.nodes["n1"]["chunks"] = [{"hash": chunk_a.hash, "vector_id": chunk_a.vector_id}]
    G.nodes["n2"]["chunks"] = [{"hash": chunk_b.hash, "vector_id": chunk_b.vector_id}]
    G.nodes["n3"]["chunks"] = [{"hash": chunk_c.hash, "vector_id": chunk_c.vector_id}]

    communities = {1: ["n1", "n2"], 2: ["n3"]}
    chunk_map = {"auth.py": [chunk_a, chunk_b], "util.py": [chunk_c]}

    return PipelineResult(
        graph=G,
        communities=communities,
        chunk_map=chunk_map,
        discovery={},
        stats={},
        config=config,
    )


class TestBuildChunkMetadata:
    def test_produces_node_labels_and_community_ids(self):
        result = _make_result()
        meta = build_chunk_metadata(result)

        chunk_a = result.chunk_map["auth.py"][0]
        chunk_b = result.chunk_map["auth.py"][1]
        chunk_c = result.chunk_map["util.py"][0]
        assert meta[chunk_a.vector_id]["node_labels"] == "Authenticate"
        assert meta[chunk_a.vector_id]["community_ids"] == "1"

        assert meta[chunk_b.vector_id]["node_labels"] == "ValidateToken"
        assert meta[chunk_b.vector_id]["community_ids"] == "1"

        assert meta[chunk_c.vector_id]["node_labels"] == "Helper"
        assert meta[chunk_c.vector_id]["community_ids"] == "2"

    def test_omits_keys_for_chunks_with_no_nodes(self):
        config = _make_config()
        G = nx.Graph()
        chunk_x = Chunk(file="orphan.py", start_line=1, end_line=10, hash="d" * 64)
        chunk_map = {"orphan.py": [chunk_x]}
        result = PipelineResult(
            graph=G,
            communities={},
            chunk_map=chunk_map,
            discovery={},
            stats={},
            config=config,
        )

        meta = build_chunk_metadata(result)
        assert chunk_x.vector_id not in meta


class TestEmbedStage:
    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_embed_called_when_enabled(
        self,
        mock_create_emb,
        mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 5
        mock_store_cls.return_value = mock_store

        config = _make_config()
        result = _make_result(config)

        out = embed(result)

        assert out.embed_stats is not None
        assert out.embed_stats["chunks_embedded"] == 5
        assert out.embed_stats["provider"] == "docker"
        assert out.embed_stats["model"] == "ai/qwen3-embedding:4B"
        assert out.embed_stats["batch_size"] == 50
        mock_store.store_chunk_map.assert_called_once()
        mock_create_emb.assert_called_once()
        emb_config = mock_create_emb.call_args[0][0]
        assert emb_config.provider == "docker"
        assert emb_config.model == "ai/qwen3-embedding:4B"

    def test_embed_skipped_when_no_embed(self):
        config = _make_config(no_embed=True)
        result = _make_result(config)

        out = embed(result)

        assert out.embed_stats is None
        assert out is result

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_incremental_embed_copies_unchanged_and_embeds_changed(
        self,
        mock_create_emb,
        mock_store_cls,
        tmp_path,
    ):
        (tmp_path / "auth.py").write_text("\n" * 60, encoding="utf-8")
        (tmp_path / "util.py").write_text("\n" * 9 + "helper\n", encoding="utf-8")
        target = MagicMock()
        source = MagicMock()
        target.store_chunk_map.return_value = 2
        target.copy_from.return_value = 1
        mock_store_cls.side_effect = [target, source]
        result = replace(
            _make_result(),
            repo_root=tmp_path,
            collection_name="new-generation",
            prior_collection_name="old-generation",
            changed_paths=frozenset({"auth.py"}),
        )

        out = embed(result)

        target.copy_from.assert_called_once_with(
            source,
            [result.chunk_map["util.py"][0].vector_id],
        )
        changed_map = target.store_chunk_map.call_args.args[0]
        assert set(changed_map) == {"auth.py"}
        assert out.embed_stats["chunks_embedded"] == 2
        assert out.embed_stats["chunks_copied"] == 1
        target.update_metadata.assert_called_once()
        target.validate_records.assert_called_once()
        mock_create_emb.assert_called_once()

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_uses_custom_collection_name(
        self,
        mock_create_emb,
        mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 3
        mock_store_cls.return_value = mock_store

        config = _make_config(chroma_collection="my_custom_collection")
        result = _make_result(config)

        embed(result)

        call_kwargs = mock_store_cls.call_args
        chroma_config = call_kwargs[1]["config"]
        assert chroma_config.collection_name == "my_custom_collection"
        mock_create_emb.assert_called_once()

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_default_collection_name_uses_slug(
        self,
        mock_create_emb,
        mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 3
        mock_store_cls.return_value = mock_store

        config = _make_config()
        result = _make_result(config)

        embed(result)

        call_kwargs = mock_store_cls.call_args
        chroma_config = call_kwargs[1]["config"]
        assert chroma_config.collection_name == "codeknow_owner-repo"
        mock_create_emb.assert_called_once()

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_empty_chunk_map_reports_zero(
        self,
        _mock_create_emb,  # noqa: PT019
        mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 0
        mock_store_cls.return_value = mock_store

        config = _make_config()
        G = nx.Graph()
        result = PipelineResult(
            graph=G,
            communities={},
            chunk_map={},
            discovery={},
            stats={},
            config=config,
        )

        out = embed(result)

        assert out.embed_stats is not None
        assert out.embed_stats["chunks_embedded"] == 0
        mock_store.store_chunk_map.assert_called_once()

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_custom_provider_and_model(
        self,
        mock_create_emb,
        mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 1
        mock_store_cls.return_value = mock_store

        config = _make_config(
            embed_provider="openrouter",
            embed_model="text-embedding-3-small",
        )
        result = _make_result(config)

        out = embed(result)

        assert out.embed_stats["provider"] == "openrouter"
        assert out.embed_stats["model"] == "text-embedding-3-small"
        emb_config = mock_create_emb.call_args[0][0]
        assert emb_config.provider == "openrouter"
        assert emb_config.model == "text-embedding-3-small"

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_custom_batch_size(self, mock_create_emb, mock_store_cls):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 1
        mock_store_cls.return_value = mock_store

        config = _make_config(embed_batch_size=1)
        result = _make_result(config)

        out = embed(result)

        assert out.embed_stats["batch_size"] == 1
        mock_create_emb.assert_called_once()
        assert mock_store.store_chunk_map.call_args.kwargs["batch_size"] == 1

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_batch_size_from_env(self, mock_create_emb, mock_store_cls, monkeypatch):
        monkeypatch.setenv("CODEKNOW_EMBED_BATCH_SIZE", "1")
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 1
        mock_store_cls.return_value = mock_store

        result = _make_result(_make_config())

        out = embed(result)

        assert out.embed_stats["batch_size"] == 1
        mock_create_emb.assert_called_once()
        assert mock_store.store_chunk_map.call_args.kwargs["batch_size"] == 1

    @patch("codeknow.pipeline.embed_stage.ChromaStore")
    @patch("codeknow.pipeline.embed_stage.create_embeddings")
    def test_forwards_on_progress_to_store(
        self,
        _mock_create_emb,  # noqa: PT019
        mock_store_cls,
    ):
        mock_store = MagicMock()
        mock_store.store_chunk_map.return_value = 3
        mock_store_cls.return_value = mock_store

        config = _make_config()
        result = _make_result(config)

        def _on_progress(done: int, total: int) -> None: ...

        embed(result, on_progress=_on_progress)

        assert (
            mock_store.store_chunk_map.call_args.kwargs["on_progress"] is _on_progress
        )


class TestChromaStoreExtraMetadata:
    def test_store_chunks_merges_extra_metadata(self):
        from codeknow.vector.chroma import ChromaStore

        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0

        store = ChromaStore(embeddings=mock_embeddings)
        store._collection = mock_collection

        chunk = Chunk(file="auth.py", start_line=1, end_line=10, hash="a" * 64)
        extra = {
            chunk.vector_id: {
                "node_labels": "Auth|Login",
                "community_ids": "1,2",
            }
        }

        with (
            patch.object(
                store,
                "_get_or_create_collection",
                return_value=mock_collection,
            ),
            patch(
                "codeknow.vector.ingest._read_chunk_content",
                return_value="some code",
            ),
        ):
            store.store_chunks([chunk], slug="owner-repo", extra_metadata=extra)

        upsert_call = mock_collection.upsert.call_args
        metas = upsert_call[1]["metadatas"][0]
        assert metas["node_labels"] == "Auth|Login"
        assert metas["community_ids"] == "1,2"
        assert metas["slug"] == "owner-repo"
        assert metas["file"] == "auth.py"


class TestChromaStoreValidation:
    def test_validate_records_checks_complete_records_and_lookup(self):
        from codeknow.vector.chroma import ChromaStore

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["vector-1"],
            "documents": ["source"],
            "metadatas": [
                {
                    "file": "a.py",
                    "start_line": 1,
                    "end_line": 2,
                    "content_hash": "hash",
                    "slug": "owner-repo",
                }
            ],
            "embeddings": [[0.1, 0.2]],
        }
        collection.query.return_value = {"ids": [["vector-1"]]}
        store = ChromaStore(embeddings=MagicMock())
        store._collection = collection

        store.validate_records(
            {
                "vector-1": {
                    "file": "a.py",
                    "start_line": 1,
                    "end_line": 2,
                    "content_hash": "hash",
                    "slug": "owner-repo",
                }
            }
        )

        collection.query.assert_called_once()

    def test_validate_records_rejects_missing_embeddings(self):
        from codeknow.vector.chroma import ChromaStore

        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["vector-1"],
            "documents": ["source"],
            "metadatas": [{}],
            "embeddings": None,
        }
        store = ChromaStore(embeddings=MagicMock())
        store._collection = collection

        with pytest.raises(ValueError, match="incomplete records"):
            store.validate_records({"vector-1": {}})


class TestEmbedTexts:
    def test_embedding_config_defaults_split_depth_to_three(self):
        config = EmbeddingConfig()

        assert config.max_embedding_split_depth == 3

    def test_embedding_config_reads_split_depth_from_env(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MAX_SPLIT_DEPTH", "5")

        config = EmbeddingConfig()

        assert config.max_embedding_split_depth == 5

    def test_embed_texts_rejects_empty_contexts_for_non_empty_texts(self):
        with pytest.raises(ValueError, match="contexts"):
            embed_texts(
                ["alpha"],
                MagicMock(),  # type: ignore[arg-type]
                contexts=[],
            )

    def test_store_chunks_invokes_on_progress_per_batch(self):
        from codeknow.vector.chroma import ChromaStore

        class RecordingEmbeddings:
            def __init__(self) -> None:
                self.requests: list[list[str]] = []

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                self.requests.append(list(texts))
                return [[float(ord(text[0]))] for text in texts]

        embeddings = RecordingEmbeddings()
        mock_collection = MagicMock()

        store = ChromaStore(
            embeddings=embeddings,  # type: ignore[arg-type]
        )
        store._collection = mock_collection

        chunks = [
            Chunk(file="one.py", start_line=1, end_line=1, hash="a" * 64),
            Chunk(file="two.py", start_line=1, end_line=1, hash="b" * 64),
            Chunk(file="three.py", start_line=1, end_line=1, hash="c" * 64),
        ]

        calls: list[tuple[int, int]] = []

        def on_progress(done: int, total: int) -> None:
            calls.append((done, total))

        with (
            patch.object(
                store,
                "_get_or_create_collection",
                return_value=mock_collection,
            ),
            patch(
                "codeknow.vector.ingest._read_chunk_content",
                side_effect=["alpha", "beta", "gamma"],
            ),
        ):
            store.store_chunks(chunks, batch_size=2, on_progress=on_progress)

        # batch_size=2 over 3 chunks => two batches: [0,1] then [2].
        assert calls == [(2, 3), (3, 3)]

    def test_multi_text_context_error_splits_batch_and_preserves_order(self):
        class ContextLengthError(Exception):
            status_code = 400

        class SplitBatchEmbeddings:
            def __init__(self) -> None:
                self.requests: list[list[str]] = []

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                self.requests.append(list(texts))
                if len(texts) > 1:
                    msg = "context length exceeded"
                    raise ContextLengthError(msg)
                return [[float(ord(texts[0][0]))]]

        embeddings = SplitBatchEmbeddings()

        vectors = embed_texts(
            ["alpha", "beta"],
            embeddings,  # type: ignore[arg-type]
        )

        assert embeddings.requests == [["alpha", "beta"], ["alpha"], ["beta"]]
        assert vectors == [[97.0], [98.0]]

    def test_single_text_context_error_splits_and_averages_vector(self):
        class ContextLengthError(Exception):
            status_code = 400

        class LengthLimitedEmbeddings:
            def __init__(self) -> None:
                self.requests: list[list[str]] = []

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                self.requests.append(list(texts))
                if any(len(text) > 5 for text in texts):
                    msg = (
                        "Error code: 400\n"
                        "request (2189 tokens) exceeds the available context "
                        "size (2048 tokens)"
                    )
                    raise ContextLengthError(msg)
                return [[float(len(text))] for text in texts]

        embeddings = LengthLimitedEmbeddings()

        vectors = embed_texts(
            ["aa\nbbbb\n"],
            embeddings,  # type: ignore[arg-type]
        )

        assert embeddings.requests == [["aa\nbbbb\n"], ["aa\n"], ["bbbb\n"]]
        assert vectors == [[4.25]]

    def test_non_context_bad_request_reraises_even_when_skip_enabled(self):
        class BadRequestError(Exception):
            status_code = 400

        class MisconfiguredEmbeddings:
            def __init__(self) -> None:
                self.requests: list[list[str]] = []

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                self.requests.append(list(texts))
                msg = "model not found"
                raise BadRequestError(msg)

        embeddings = MisconfiguredEmbeddings()

        with pytest.raises(BadRequestError, match="model not found"):
            embed_texts(
                ["alpha", "beta"],
                embeddings,  # type: ignore[arg-type]
                skip_context_length_errors=True,
            )

        assert embeddings.requests == [["alpha", "beta"]]

    def test_rate_limit_error_sleeps_and_retries(self, monkeypatch):
        class RateLimitError(Exception):
            status_code = 429

        class RetryOnceEmbeddings:
            def __init__(self) -> None:
                self.requests: list[list[str]] = []
                self.calls = 0

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                self.requests.append(list(texts))
                self.calls += 1
                if self.calls == 1:
                    msg = "too many requests"
                    raise RateLimitError(msg)
                return [[float(ord(text[0]))] for text in texts]

        sleeps: list[float] = []
        monkeypatch.setattr(
            "codeknow.vector.embeddings.time.sleep",
            sleeps.append,
        )

        embeddings = RetryOnceEmbeddings()

        vectors = embed_texts(
            ["alpha", "beta"],
            embeddings,  # type: ignore[arg-type]
        )

        assert sleeps == [5]
        assert embeddings.requests == [["alpha", "beta"], ["alpha", "beta"]]
        assert vectors == [[97.0], [98.0]]

    def test_transient_embedding_error_sleeps_and_retries(self, monkeypatch):
        class TransientError(Exception):
            status_code = 500

        class RetryOnceEmbeddings:
            def __init__(self) -> None:
                self.requests: list[list[str]] = []
                self.calls = 0

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                self.requests.append(list(texts))
                self.calls += 1
                if self.calls == 1:
                    msg = "llama.cpp terminated unexpectedly"
                    raise TransientError(msg)
                return [[float(ord(text[0]))] for text in texts]

        sleeps: list[float] = []
        monkeypatch.setattr(
            "codeknow.vector.embeddings.time.sleep",
            sleeps.append,
        )

        embeddings = RetryOnceEmbeddings()

        vectors = embed_texts(
            ["alpha"],
            embeddings,  # type: ignore[arg-type]
        )

        assert sleeps == [2]
        assert embeddings.requests == [["alpha"], ["alpha"]]
        assert vectors == [[97.0]]

    def test_context_error_reports_chunk_when_split_depth_is_exceeded(self):
        class ContextLengthError(Exception):
            status_code = 400

        class AlwaysFailEmbeddings:
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                msg = "context length exceeded"
                raise ContextLengthError(msg)

        with pytest.raises(EmbeddingContextLengthError, match="chunk=abc"):
            embed_texts(
                ["too long"],
                AlwaysFailEmbeddings(),  # type: ignore[arg-type]
                contexts=["chunk=abc file=big.py:1-20 provider=docker"],
                max_embedding_split_depth=0,
                model="test-model",
            )

    def test_context_error_can_be_skipped_after_split_depth_is_exceeded(
        self,
        caplog,
    ):
        class ContextLengthError(Exception):
            status_code = 400

        class AlwaysFailEmbeddings:
            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                msg = "context length exceeded"
                raise ContextLengthError(msg)

        with caplog.at_level("WARNING", logger="codeknow.vector.embeddings"):
            vectors = embed_texts(
                ["too long"],
                AlwaysFailEmbeddings(),  # type: ignore[arg-type]
                contexts=["chunk=abc file=big.py:1-20 provider=docker"],
                max_embedding_split_depth=0,
                model="test-model",
                skip_context_length_errors=True,
            )

        assert vectors == [[]]
        assert "chunk=abc" in caplog.text

    def test_merge_split_embedding_vectors_uses_text_lengths(self):
        assert _merge_split_embedding_vectors([[1.0], [5.0]], [1, 3]) == [4.0]


class TestDeleteBySlug:
    def test_delete_by_slug_calls_get_and_delete(self):
        from codeknow.vector.chroma import ChromaStore

        mock_embeddings = MagicMock()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": ["hash1", "hash2"]}

        store = ChromaStore(embeddings=mock_embeddings)

        with patch.object(
            store,
            "_get_or_create_collection",
            return_value=mock_collection,
        ):
            count = store.delete_by_slug("owner-repo")

        assert count == 2
        mock_collection.get.assert_called_once_with(where={"slug": "owner-repo"})
        mock_collection.delete.assert_called_once_with(ids=["hash1", "hash2"])
