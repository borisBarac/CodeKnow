"""Stage 3 — consistency across seeds (reference-free).

For each pair of runs of the same task+tool (different seeds), measure
semantic agreement: an LLM judge on a subset of pairs (accuracy where it
matters) and embedding cosine similarity on the rest (cost where it doesn't).
Returns ``None`` when there are fewer than 2 runs (consistency is not
measurable on a single seed). Both the LLM and embedding function are
injected.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from itertools import combinations
from statistics import mean
from typing import TYPE_CHECKING

from evalkit.judge.prompts import CONSISTENCY_PROMPT

if TYPE_CHECKING:
    from evalkit.schemas import AgentRun, Task

LLM = Callable[[str], dict]
EmbedFn = Callable[[str], list[float]]


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity; ``0.0`` for a zero vector (no direction)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def stage3(
    task: Task,
    runs: list[AgentRun],
    llm: LLM,
    embed_fn: EmbedFn,
    llm_subset_size: int = 3,
) -> float | None:
    """Mean inter-seed agreement over all run pairs, or ``None`` if <2 runs."""
    pairs = list(combinations(runs, 2))
    if not pairs:
        return None
    agreements: list[float] = []
    for i, (r1, r2) in enumerate(pairs):
        if i < llm_subset_size:
            agreements.append(_llm_agreement(task, r1, r2, llm))
        else:
            agreements.append(
                cosine_sim(embed_fn(r1.final_answer), embed_fn(r2.final_answer))
            )
    return mean(agreements)


def _llm_agreement(task: Task, r1: AgentRun, r2: AgentRun, llm: LLM) -> float:
    prompt = CONSISTENCY_PROMPT.format(
        task_prompt=task.prompt, answer_1=r1.final_answer, answer_2=r2.final_answer
    )
    raw = llm(prompt)
    try:
        return float(raw.get("agreement_score", 0.0))
    except (TypeError, ValueError):
        return 0.0
