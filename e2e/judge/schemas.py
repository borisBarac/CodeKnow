"""Pydantic models for the LLM judge input/output contract.

JudgeInput — what goes *into* the judge (query, hits, analysis).
JudgeOutput — what comes *out* (scores, strengths, weaknesses, rationale).

The output shape matches the exact JSON contract from the judge prompt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JudgeHit(BaseModel):
    id: str
    file_path: str = ""
    symbol: str | None = None
    kind: str | None = None
    snippet: str | None = None
    score: float | None = None
    relation_to_seed: str | None = None
    why_retrieved: str | None = None


class JudgeInput(BaseModel):
    query: str
    repo_brief: str
    semantic_hits: list[JudgeHit]
    graph_hits: list[JudgeHit]
    merged_hits: list[JudgeHit]
    agent_analysis: str = ""


class JudgeSubscores(BaseModel):
    semantic_relevance: int = Field(ge=0, le=100)
    kg_expansion_value: int = Field(ge=0, le=100)
    coverage: int = Field(ge=0, le=100)
    groundedness: int = Field(ge=0, le=100)
    noise_control: int = Field(ge=0, le=100)


class UnsupportedClaim(BaseModel):
    claim: str
    reason: str


class EvidenceUsed(BaseModel):
    semantic_hit_ids: list[str] = Field(default_factory=list)
    graph_hit_ids: list[str] = Field(default_factory=list)
    merged_hit_ids: list[str] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    final_score: float = Field(ge=0.0, le=100.0)
    confidence: Literal["low", "medium", "high"]
    subscores: JudgeSubscores
    winner: Literal["semantic_only", "hybrid", "tie", "unknown"]
    strengths: list[str]
    weaknesses: list[str]
    unsupported_claims: list[UnsupportedClaim]
    missing_evidence: list[str]
    evidence_used: EvidenceUsed
    rationale: str
