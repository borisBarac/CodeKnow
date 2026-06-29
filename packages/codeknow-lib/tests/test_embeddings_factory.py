from __future__ import annotations

from unittest.mock import MagicMock, patch

from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings


def test_embedding_config_loads_num_ctx_from_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_NUM_CTX", "8192")

    config = EmbeddingConfig()

    assert config.num_ctx == 8192


@patch("codeknow.vector.embeddings.OpenAIEmbeddings")
def test_create_embeddings_does_not_forward_num_ctx(mock_openai_embeddings):
    mock_openai_embeddings.return_value = MagicMock()

    config = EmbeddingConfig(num_ctx=2048)

    create_embeddings(config)

    assert "num_ctx" not in mock_openai_embeddings.call_args.kwargs
    assert mock_openai_embeddings.call_args.kwargs["model"] == config.model
