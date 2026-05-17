"""LLM judge for hybrid search evaluation.

Uses LangChain ChatOpenAI with structured output to evaluate search quality
across five dimensions: semantic relevance, KG expansion value, coverage,
groundedness, and noise control.

Configuration is read from environment variables (via pydantic-settings):
  JUDGE_LLM_MODEL    — model name (default: openai/gpt-4o-mini)
  JUDGE_LLM_BASE_URL — OpenAI-compatible API base URL (default: OpenRouter)
  JUDGE_LLM_API_KEY  — API key (falls back to OPENROUTER_API_KEY)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings

from .schemas import JudgeHit, JudgeInput, JudgeOutput

if TYPE_CHECKING:
    from codeknow.schemas import HybridSearchResponse, HybridSearchResult

logger = logging.getLogger(__name__)

# ruff: noqa: E501
_SYSTEM_PROMPT = """\
You are an expert evaluator of hybrid repository search.

Your job is to evaluate how good a repository search result is for answering a developer query.

The search system uses:
1. Semantic retrieval over a repository index.
2. Knowledge-graph expansion over code and repository entities.
3. A merged evidence set used by an agent to analyze the repo.

You are NOT grading whether the final answer is universally true.
You ARE grading whether the retrieved evidence is relevant, useful, sufficiently covering the problem, and whether the analysis is grounded in that evidence.

You must be strict, evidence-based, and conservative.
Do not reward confident language unless it is supported by retrieved evidence.
Do not assume missing facts.
Do not give high scores for partial matches that only look plausible.
Prefer precision over generosity.

You will receive:
- query: the user's repository question
- repo_brief: short summary of the repo structure
- semantic_hits: results returned directly from semantic search
- graph_hits: results added by knowledge-graph expansion
- merged_hits: final evidence set shown to the agent
- agent_analysis: the agent's explanation based on the evidence

Each hit may include fields like:
- id
- file_path
- symbol
- kind
- snippet
- score
- relation_to_seed
- why_retrieved

Evaluate across these dimensions:

A. Semantic relevance (0-100)
How well do the semantic hits match the query intent, target symbols, files, modules, or code region?

B. KG expansion value (0-100)
Did the graph expansion add meaningful connected evidence such as callers, callees, imports, subclasses, configuration links, ownership links, or related files?
Give a low score if graph expansion mostly adds tangential or obvious duplicates.

C. Coverage (0-100)
Does the combined evidence cover enough of the codebase area to support a solid answer?
A high score means the evidence likely contains the key files, symbols, and relationships needed for the task.

D. Groundedness (0-100)
Is the agent_analysis actually supported by the retrieved evidence?
Penalize any claims that are not directly supported by snippets, files, symbols, or relations in the input.

E. Noise control (0-100)
How well does the result avoid irrelevant, repetitive, or low-value context?
A high score means the context is focused and efficient.

Scoring weights:
- semantic_relevance: 35%
- kg_expansion_value: 20%
- coverage: 20%
- groundedness: 15%
- noise_control: 10%

Final score formula:
final_score =
  semantic_relevance * 0.35 +
  kg_expansion_value * 0.20 +
  coverage * 0.20 +
  groundedness * 0.15 +
  noise_control * 0.10

Scoring guidance:
- 90-100: highly relevant retrieval, graph adds clear value, analysis is strongly grounded
- 70-89: useful retrieval, but some missing context, noise, or weak support
- 50-69: mixed quality, partial support, noticeable gaps
- 0-49: poor retrieval, weak grounding, or mostly irrelevant evidence

Important judging rules:
- Judge semantic_hits and graph_hits separately before judging merged_hits.
- Reward graph expansion only for incremental value, not for volume.
- Penalize unsupported claims in agent_analysis.
- Penalize evidence packs that are broad but not targeted.
- Penalize duplicated hits dressed up as multiple sources.
- Do not guess repository facts that are not present in the inputs.
- If the query clearly requires a symbol, file, or dependency chain that is missing, reduce coverage.
- If graph expansion adds unrelated neighbors, reduce kg_expansion_value and noise_control.
- If the semantic hits are strong and graph expansion adds little, do not force a high graph score.
- If evidence is insufficient to judge confidently, lower confidence and explain why.

Additional rule for winner:
- "semantic_only" if semantic hits already solve the query and graph expansion adds little or adds noise
- "hybrid" if graph expansion clearly improves coverage, dependency tracing, or groundedness
- "tie" if both are similarly good
- "unknown" if evidence is too weak to judge
"""


class JudgeConfig(BaseSettings):
    model: str = "openai/gpt-4o-mini"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.0

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        import os

        fallback = os.environ.get("OPENROUTER_API_KEY")
        if fallback:
            return fallback
        msg = (
            "Judge requires an API key. Set JUDGE_LLM_API_KEY or "
            "OPENROUTER_API_KEY env var."
        )
        raise ValueError(msg)

    model_config = {
        "env_prefix": "JUDGE_LLM_",
        "env_file": ".env",
        "extra": "ignore",
    }


def _result_to_hit(r: HybridSearchResult) -> JudgeHit:
    relation = None
    why = None
    if r.graph_path:
        relation = " → ".join(r.graph_path)
        why = f"graph expansion via {' → '.join(r.graph_path)}"
    if r.provenance == "vector":
        why = "semantic match"

    return JudgeHit(
        id=r.chunk_hash,
        file_path=r.file,
        symbol=", ".join(r.node_labels) if r.node_labels else None,
        kind=r.provenance,
        snippet=r.content[:2000] if r.content else None,
        score=r.distance,
        relation_to_seed=relation,
        why_retrieved=why,
    )


def from_hybrid_response(
    response: HybridSearchResponse,
    repo_brief: str = "",
    agent_analysis: str = "",
) -> JudgeInput:
    semantic = [_result_to_hit(r) for r in response.results if r.provenance == "vector"]
    graph = [_result_to_hit(r) for r in response.results if r.provenance == "graph"]
    merged = [_result_to_hit(r) for r in response.results]

    return JudgeInput(
        query=response.query,
        repo_brief=repo_brief,
        semantic_hits=semantic,
        graph_hits=graph,
        merged_hits=merged,
        agent_analysis=agent_analysis,
    )


class LLMJudge:
    def __init__(self, config: JudgeConfig | None = None) -> None:
        self._config = config or JudgeConfig()
        self._llm = ChatOpenAI(
            model=self._config.model,
            base_url=self._config.base_url,
            api_key=SecretStr(self._config.resolved_api_key()),
            temperature=self._config.temperature,
        ).with_structured_output(JudgeOutput)

    def judge(self, judge_input: JudgeInput) -> JudgeOutput:
        payload = judge_input.model_dump()
        user_content = json.dumps(payload, indent=2, default=str)
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]
        logger.info("Judge evaluating query: %s", judge_input.query)
        result = self._llm.invoke(messages)
        if not isinstance(result, JudgeOutput):
            msg = f"Expected JudgeOutput, got {type(result)}"
            raise TypeError(msg)
        logger.info(
            "Judge result: final_score=%.1f confidence=%s winner=%s",
            result.final_score,
            result.confidence,
            result.winner,
        )
        return result
