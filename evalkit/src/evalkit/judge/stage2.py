"""Stage 2 — pairwise preference, double-swapped (reference-free).

Run only on cross-tool pairs for the same task. The LLM is called twice with
the candidate order swapped (AB then BA); if the two verdicts disagree the
result is a ``Tie`` at ``low`` confidence (section 9 anti-bias rule). The LLM
is injected so the swap/normalisation logic is unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from evalkit.judge.prompts import STAGE2_PROMPT
from evalkit.judge.stage1 import format_cited_code
from evalkit.schemas import Confidence, PairwiseJudgment, Winner

if TYPE_CHECKING:
    from evalkit.schemas import AgentRun, Task

LLM = Callable[[str], dict]

_CANDIDATE_TO_ROLE = {"Candidate 1": "first", "Candidate 2": "second", "Tie": "tie"}


def resolve_pairwise(
    ab_winner: str,
    ab_conf: str,
    ba_winner: str,
    ba_conf: str,
    tool_a: str,
    tool_b: str,
) -> PairwiseJudgment:
    """Turn two swapped verdicts into one ``PairwiseJudgment``.

    In the AB call Candidate 1 = ``tool_a``; in the BA call Candidate 1 =
    ``tool_b`` (swapped). Agreement => that tool wins at the lower confidence;
    disagreement => ``Tie`` at ``low``.
    """
    norm_ab = _normalize(ab_winner, tool_a, tool_b)
    norm_ba = _normalize(ba_winner, tool_b, tool_a)

    if norm_ab == norm_ba:
        winner: Winner = norm_ab if norm_ab in ("hybrid", "grep") else "Tie"
        confidence = _lower_conf(ab_conf, ba_conf)
    else:
        winner = "Tie"
        confidence = "low"
    return PairwiseJudgment(
        task_id="", winner=winner, confidence=confidence, reasoning=""
    )


def stage2(
    task: Task,
    run_a: AgentRun,
    snippets_a: dict[str, str | None],
    run_b: AgentRun,
    snippets_b: dict[str, str | None],
    llm: LLM,
) -> PairwiseJudgment:
    """Run the double-swap pairwise comparison via ``llm``.

    Short-circuits deterministically when one or both answers are empty:
    a non-empty answer always beats an empty one; two empty answers tie.
    """
    a_empty = not run_a.final_answer.strip()
    b_empty = not run_b.final_answer.strip()

    if a_empty and b_empty:
        return PairwiseJudgment(
            task_id=task.task_id,
            winner="Tie",
            confidence="low",
            reasoning="both answers empty",
        )
    if a_empty:
        return PairwiseJudgment(
            task_id=task.task_id,
            winner=run_b.tool,
            confidence="high",
            reasoning="candidate A had an empty answer",
        )
    if b_empty:
        return PairwiseJudgment(
            task_id=task.task_id,
            winner=run_a.tool,
            confidence="high",
            reasoning="candidate B had an empty answer",
        )

    ab = _call_once(task, run_a, snippets_a, run_b, snippets_b, llm)
    ba = _call_once(task, run_b, snippets_b, run_a, snippets_a, llm)
    verdict = resolve_pairwise(
        ab["winner"],
        ab["confidence"],
        ba["winner"],
        ba["confidence"],
        run_a.tool,
        run_b.tool,
    )
    verdict.task_id = task.task_id
    verdict.reasoning = ab.get("reasoning", "")
    return verdict


def _call_once(
    task: Task,
    first_run: AgentRun,
    first_snippets: dict[str, str | None],
    second_run: AgentRun,
    second_snippets: dict[str, str | None],
    llm: LLM,
) -> dict:
    prompt = STAGE2_PROMPT.format(
        task_prompt=task.prompt,
        answer_1=first_run.final_answer,
        code_1=format_cited_code(first_snippets),
        answer_2=second_run.final_answer,
        code_2=format_cited_code(second_snippets),
    )
    return llm(prompt)


def _normalize(raw: str, tool_first: str, tool_second: str) -> str:
    role = _CANDIDATE_TO_ROLE.get(raw, "tie")
    if role == "first":
        return tool_first
    if role == "second":
        return tool_second
    return "Tie"


_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _lower_conf(a: str, b: str) -> Confidence:
    ranked = sorted([a, b], key=lambda c: _CONF_RANK.get(c, 0))
    return ranked[0] if ranked[0] in _CONF_RANK else "low"
