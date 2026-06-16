"""Stage 1 — LLM grounding + faithfulness (per run, reference-free).

One LLM call scores both dimensions on shared context (the cited code) and
emits ``ungrounded_claims[]`` and ``hallucinated_paths[]``. The LLM is injected
as a ``Callable[[str], dict]`` so the prompt assembly and JSON-to-output
mapping are unit-testable without a real model; the production binding lives
in :mod:`evalkit.llm`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from evalkit.judge.prompts import STAGE1_PROMPT
from evalkit.schemas import JudgeOutput

if TYPE_CHECKING:
    from evalkit.schemas import AgentRun, Task

LLM = Callable[[str], dict]


def format_cited_code(snippets: dict[str, str | None]) -> str:
    """Build the ``<CITED_CODE>`` block from Stage 0 snippets.

    Existing citations show their real code; missing files are marked
    ``[FILE NOT FOUND]`` so the judge can flag them as hallucinated.
    """
    parts: list[str] = []
    for citation, snippet in snippets.items():
        body = snippet if snippet is not None else "[FILE NOT FOUND]"
        parts.append(f"### {citation}\n{body}")
    return "\n\n".join(parts)


def stage1(
    task: Task,
    run: AgentRun,
    snippets: dict[str, str | None],
    existence_rate: float | None,
    llm: LLM,
) -> JudgeOutput:
    """Score one run on grounding + faithfulness via ``llm``.

    Short-circuits with zero scores only when the answer is empty. A
    non-empty answer with no citations is still judged by the LLM — it may
    earn partial faithfulness for substantively-correct prose — and the
    missing-citation signal is surfaced as an ungrounded claim rather than
    collapsing the run to the same score as a pure hallucination.
    """
    if not run.final_answer.strip():
        return JudgeOutput(
            task_id=run.task_id,
            tool=run.tool,
            seed=run.seed,
            grounding=0,
            existence_rate=None,
            faithfulness=0,
            ungrounded_claims=["empty answer"],
            hallucinated_paths=[],
            consistency_vs_other_seeds=None,
        )

    prompt = STAGE1_PROMPT.format(
        task_prompt=task.prompt,
        final_answer=run.final_answer,
        cited_code=format_cited_code(snippets),
    )
    raw = llm(prompt)
    ungrounded = list(raw.get("ungrounded_claims", []))
    if not run.cited_locations:
        ungrounded = ["answer contains no file:line citations", *ungrounded]
    return JudgeOutput(
        task_id=run.task_id,
        tool=run.tool,
        seed=run.seed,
        grounding=_clamp_int(raw.get("grounding", 0), 0, 5),
        existence_rate=existence_rate,
        faithfulness=_clamp_int(raw.get("faithfulness", 0), 0, 5),
        ungrounded_claims=ungrounded,
        hallucinated_paths=list(raw.get("hallucinated_paths", [])),
        consistency_vs_other_seeds=None,  # filled by Stage 3 when >=2 seeds
    )


def _clamp_int(value: Any, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, n))
