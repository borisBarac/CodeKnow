"""Pydantic data contracts for evalkit.

Mirrors JUDGE_PRINCIPLES section 5. No ``gold`` field anywhere: this is a
reference-free evaluator. ``Task`` describes what an agent must do; ``AgentRun``
is one agent's attempt (task x tool x seed); ``JudgeOutput`` is the per-run
judgment; ``PairwiseJudgment`` is the cross-tool preference verdict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TaskType = Literal["locate", "reasoning", "aggregation", "trap"]
ToolName = Literal["hybrid", "grep"]
Winner = Literal["hybrid", "grep", "Tie"]
Confidence = Literal["high", "medium", "low"]


class Task(BaseModel):
    task_id: str
    type: TaskType
    stratum: str
    difficulty: str
    prompt: str
    trap: bool = False


class Cost(BaseModel):
    """Resource cost of one agent run.

    Fields align with JUDGE_PRINCIPLES section 4 (Stage 0 cost capture).
    """

    search_calls: int = Field(ge=0)
    llm_turns: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    wall_clock_s: float = Field(ge=0.0)


class AgentRun(BaseModel):
    task_id: str
    tool: ToolName
    seed: int
    final_answer: str
    cited_locations: list[str] = Field(default_factory=list)
    cost: Cost


class JudgeOutput(BaseModel):
    """Per-run judgment (Stage 0 + Stage 1 results)."""

    task_id: str
    tool: ToolName
    seed: int
    grounding: int = Field(ge=0, le=5)
    existence_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    faithfulness: int = Field(ge=0, le=5)
    ungrounded_claims: list[str] = Field(default_factory=list)
    hallucinated_paths: list[str] = Field(default_factory=list)
    unsupported_ranges: list[str] = Field(default_factory=list)
    consistency_vs_other_seeds: float | None = Field(default=None, ge=0.0, le=1.0)


class PairwiseJudgment(BaseModel):
    """Cross-tool preference verdict for one task (Stage 2)."""

    task_id: str
    winner: Winner
    confidence: Confidence
    reasoning: str = ""
