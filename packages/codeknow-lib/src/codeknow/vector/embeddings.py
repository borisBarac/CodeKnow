"""LangChain embeddings factory and chunk embedding utilities.

Provides a provider-agnostic factory that returns any LangChain ``Embeddings``
instance, plus helpers that convert the project's ``Chunk`` / ``ChunkMap``
types into ``{hash: embedding}`` dicts ready for a vector store.

Both supported providers use the OpenAI-compatible API via
``langchain_openai.OpenAIEmbeddings``:

- **ollama** — local inference (Ollama exposes an ``/v1`` OpenAI-compatible
  endpoint)
- **openrouter** — cloud inference (OpenRouter exposes an OpenAI-compatible API)

Configuration is read from a ``.env`` file in the working directory:

- ``EMBEDDING_PROVIDER`` — ``"ollama"`` (default) or ``"openrouter"``
- ``EMBEDDING_MODEL`` — model name (default ``"qwen3-embedding:4b"``)
- ``OLLAMA_BASE_URL``, ``OPENROUTER_BASE_URL``, ``OPENROUTER_API_KEY``
  — provider-specific connection details.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._utils import read_chunk_content

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

    from codeknow.schemas import Chunk, ChunkMap

try:
    from langchain_openai import OpenAIEmbeddings
except ImportError as exc:
    msg = (
        "langchain-openai is required for embeddings. "
        "Install with: pip install codeknow[embeddings]"
    )
    raise ImportError(msg) from exc


class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    provider: Literal["ollama", "openrouter"] = Field(
        default="ollama", alias="EMBEDDING_PROVIDER"
    )
    model: str = Field(default="qwen3-embedding:4b", alias="EMBEDDING_MODEL")
    base_url: str | None = None
    api_key: str | None = None
    ollama_base_url: str = Field(
        default="http://localhost:11434/v2", alias="OLLAMA_BASE_URL"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.provider == "ollama":
            return self.ollama_base_url
        return self.openrouter_base_url

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.provider == "ollama":
            return "ollama"
        if self.openrouter_api_key:
            return self.openrouter_api_key
        msg = (
            "OpenRouter requires an API key. "
            "Set OPENROUTER_API_KEY env var or pass api_key in config."
        )
        raise ValueError(msg)


def create_embeddings(config: EmbeddingConfig) -> Embeddings:
    kwargs: dict = {
        "model": config.model,
        "api_key": config.resolved_api_key(),
        "base_url": config.resolved_base_url(),
    }
    if config.provider == "ollama":
        kwargs["check_embedding_ctx_length"] = False
    return OpenAIEmbeddings(**kwargs)


def embed_texts(
    texts: list[str],
    embeddings: Embeddings,
) -> list[list[float]]:
    return embeddings.embed_documents(texts)


def embed_chunks(
    chunks: list[Chunk],
    embeddings: Embeddings,
) -> dict[str, list[float]]:
    if not chunks:
        return {}

    texts = [read_chunk_content(c) for c in chunks]
    vectors = embeddings.embed_documents(texts)

    return {
        chunk.hash: vector
        for chunk, vector in zip(chunks, vectors, strict=False)
        if vector
    }


def embed_chunk_map(
    chunk_map: ChunkMap,
    embeddings: Embeddings,
) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for file_chunks in chunk_map.values():
        result.update(embed_chunks(file_chunks, embeddings))
    return result
