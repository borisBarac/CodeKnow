from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import networkx as nx

from codeknow.pipeline import PipelineConfig, PipelineResult
from codeknow.schemas import Chunk
from codeknow.vector.metadata import build_chunk_metadata
from codeknow.vector.pipeline_stage import embed


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

    G.nodes["n1"]["chunks"] = [{"hash": chunk_a.hash}]
    G.nodes["n2"]["chunks"] = [{"hash": chunk_b.hash}]
    G.nodes["n3"]["chunks"] = [{"hash": chunk_c.hash}]

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

        assert meta["a" * 64]["node_labels"] == "Authenticate"
        assert meta["a" * 64]["community_ids"] == "1"

        assert meta["b" * 64]["node_labels"] == "ValidateToken"
        assert meta["b" * 64]["community_ids"] == "1"

        assert meta["c" * 64]["node_labels"] == "Helper"
        assert meta["c" * 64]["community_ids"] == "2"

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
        assert "d" * 64 not in meta


class TestEmbedStage:
    @patch("codeknow.vector.pipeline_stage.ChromaStore")
    @patch("codeknow.vector.pipeline_stage.create_embeddings")
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
        assert out.embed_stats["provider"] == "ollama"
        assert out.embed_stats["model"] == "qwen3-embedding:4b"
        mock_store.store_chunk_map.assert_called_once()
        mock_create_emb.assert_called_once()
        emb_config = mock_create_emb.call_args[0][0]
        assert emb_config.provider == "ollama"
        assert emb_config.model == "qwen3-embedding:4b"

    def test_embed_skipped_when_no_embed(self):
        config = _make_config(no_embed=True)
        result = _make_result(config)

        out = embed(result)

        assert out.embed_stats is None
        assert out is result

    @patch("codeknow.vector.pipeline_stage.ChromaStore")
    @patch("codeknow.vector.pipeline_stage.create_embeddings")
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

    @patch("codeknow.vector.pipeline_stage.ChromaStore")
    @patch("codeknow.vector.pipeline_stage.create_embeddings")
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

    @patch("codeknow.vector.pipeline_stage.ChromaStore")
    @patch("codeknow.vector.pipeline_stage.create_embeddings")
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

    @patch("codeknow.vector.pipeline_stage.ChromaStore")
    @patch("codeknow.vector.pipeline_stage.create_embeddings")
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


class TestChromaStoreExtraMetadata:
    def test_store_chunks_merges_extra_metadata(self):
        from codeknow.vector.chroma import ChromaStore

        mock_embeddings = MagicMock()
        mock_embeddings.embed_documents.return_value = [[0.1, 0.2, 0.3]]

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0

        store = ChromaStore(embeddings=mock_embeddings)
        store._collection = mock_collection  # noqa: SLF001

        chunk = Chunk(file="auth.py", start_line=1, end_line=10, hash="a" * 64)
        extra = {
            "a" * 64: {
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
                "codeknow.vector.chroma.read_chunk_content",
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

