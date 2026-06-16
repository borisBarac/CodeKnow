"""LLM client for the judge — OpenRouter/OpenAI-compatible chat + JSON parsing.

The judge calls the model directly (no LangSmith code) and parses JSON. Token
usage is captured for the cost axis. ``parse_json_block`` is pure and unit-
tested; ``call_llm_json`` is the production binding (exercised by the
``llm_judge``-marked integration test).
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_BARE_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


def parse_json_block(text: str) -> dict:
    """Extract the first JSON object from ``text`` (tolerant of fences/prose).

    Tries fenced `````json {...} ````` first, then any bare ``{...}``. Raises
    ``ValueError`` if nothing parses.
    """
    for pattern in (_FENCE_RE, _BARE_RE):
        m = pattern.search(text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"could not parse JSON from LLM output: {text[:200]!r}"
        raise ValueError(msg) from exc


class JudgeLLMConfig(BaseSettings):
    """Judge LLM connection — reads JUDGE_LLM_* / OPENROUTER_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="JUDGE_LLM_", extra="ignore", populate_by_name=True
    )

    model: str = "deepseek-v4-pro"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.0

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        for var in ("JUDGE_LLM_API_KEY", "OPENROUTER_API_KEY"):
            value = os.environ.get(var)
            if value:
                return value
        msg = "Set JUDGE_LLM_API_KEY or OPENROUTER_API_KEY for the judge LLM."
        raise ValueError(msg)


@lru_cache(maxsize=1)
def _default_config() -> JudgeLLMConfig:
    return JudgeLLMConfig()


def call_llm_json(prompt: str, config: JudgeLLMConfig | None = None) -> dict[str, Any]:
    """Call the judge model and return parsed JSON.

    Uses ``langchain_openai.ChatOpenAI`` (already a dependency for embeddings)
    against the OpenRouter/OpenAI-compatible endpoint. JSON mode is requested
    via ``response_format``; ``parse_json_block`` is the tolerant fallback.
    """
    cfg = config or _default_config()
    llm = _build_chat(cfg)
    response = llm.invoke(prompt)
    content = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    try:
        return parse_json_block(content)
    except ValueError:
        logger.warning("LLM JSON parse failed; retrying without JSON mode")
        retry = _build_chat(cfg, json_mode=False).invoke(prompt)
        text = retry.content if isinstance(retry.content, str) else str(retry.content)
        return parse_json_block(text)


def _build_chat(cfg: JudgeLLMConfig, json_mode: bool = True) -> Any:
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key": SecretStr(cfg.resolved_api_key()),
        "temperature": cfg.temperature,
        "request_timeout": 90,
        "max_retries": 2,
    }
    if json_mode:
        kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
    return ChatOpenAI(**kwargs)


def make_llm_callable(
    config: JudgeLLMConfig | None = None,
) -> Callable[[str], dict[str, Any]]:
    """Return a ``Callable[[str], dict]`` bound to ``call_llm_json``."""
    cfg = config or _default_config()
    return lambda prompt: call_llm_json(prompt, cfg)
