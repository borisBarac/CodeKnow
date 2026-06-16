"""Tests for Stage 2 (pairwise double-swap) — logic with a stubbed LLM."""

from evalkit.judge.stage2 import resolve_pairwise, stage2
from evalkit.schemas import AgentRun, Cost, Task

TOOL_A = "hybrid"
TOOL_B = "grep"


def _task() -> Task:
    return Task(
        task_id="T-1",
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt="Find the login entry point.",
    )


def _run(tool: str) -> AgentRun:
    return AgentRun(
        task_id="T-1",
        tool=tool,
        seed=0,
        final_answer=f"{tool} answer",
        cited_locations=[],
        cost=Cost(
            search_calls=1,
            llm_turns=1,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=1.0,
        ),
    )


def test_resolve_both_point_at_same_tool_uses_lower_confidence():
    # AB: Candidate 1 => hybrid. BA: Candidate 2 => hybrid (swapped). Agree.
    j = resolve_pairwise("Candidate 1", "high", "Candidate 2", "medium", TOOL_A, TOOL_B)
    assert j.winner == "hybrid"
    assert j.confidence == "medium"


def test_resolve_disagreement_becomes_tie_low():
    # AB: Candidate 1 => hybrid. BA: Candidate 1 => grep (swapped). Disagree.
    j = resolve_pairwise("Candidate 1", "high", "Candidate 1", "high", TOOL_A, TOOL_B)
    assert j.winner == "Tie"
    assert j.confidence == "low"


def test_resolve_both_tie_is_tie():
    j = resolve_pairwise("Tie", "high", "Tie", "high", TOOL_A, TOOL_B)
    assert j.winner == "Tie"
    assert j.confidence == "high"


def test_resolve_one_tie_one_winner_is_tie_low():
    j = resolve_pairwise("Candidate 1", "high", "Tie", "high", TOOL_A, TOOL_B)
    assert j.winner == "Tie"
    assert j.confidence == "low"


def test_stage2_calls_llm_twice_swapped_and_resolves():
    calls: list[str] = []

    def fake_llm(prompt: str) -> dict:
        calls.append(prompt)
        # Candidate 1 always wins in whatever ordering we're shown.
        return {"reasoning": "c1 better", "winner": "Candidate 1", "confidence": "high"}

    run_a = _run(TOOL_A)
    run_b = _run(TOOL_B)
    j = stage2(_task(), run_a, {}, run_b, {}, fake_llm)

    # Two calls, and the two orderings differ (AB then BA).
    assert len(calls) == 2
    assert "hybrid answer" in calls[0]
    assert "grep answer" in calls[0]
    # In the BA call the candidates are swapped.
    assert calls[0].index("hybrid answer") < calls[0].index("grep answer")
    assert calls[1].index("grep answer") < calls[1].index("hybrid answer")
    # AB: Candidate 1 => hybrid; BA: Candidate 1 => grep => disagree => Tie.
    assert j.winner == "Tie"
    assert j.confidence == "low"


def test_stage2_propagates_reasoning():
    def fake_llm(prompt: str) -> dict:
        return {"reasoning": "both solid", "winner": "Tie", "confidence": "high"}

    j = stage2(_task(), _run(TOOL_A), {}, _run(TOOL_B), {}, fake_llm)
    assert j.reasoning == "both solid"


def test_stage2_empty_vs_non_empty_non_empty_wins():
    def fake_llm(_prompt: str) -> dict:
        msg = "LLM should not be called when one answer is empty"
        raise AssertionError(msg)

    empty_run = AgentRun(
        task_id="T-1",
        tool="grep",
        seed=0,
        final_answer="",
        cited_locations=[],
        cost=Cost(
            search_calls=0,
            llm_turns=0,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=0.0,
        ),
    )
    j = stage2(_task(), empty_run, {}, _run(TOOL_A), {}, fake_llm)
    assert j.winner == "hybrid"
    assert j.confidence == "high"


def test_stage2_both_empty_returns_tie():
    def fake_llm(_prompt: str) -> dict:
        msg = "LLM should not be called when both answers are empty"
        raise AssertionError(msg)

    empty_a = AgentRun(
        task_id="T-1",
        tool="hybrid",
        seed=0,
        final_answer="",
        cited_locations=[],
        cost=Cost(
            search_calls=0,
            llm_turns=0,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=0.0,
        ),
    )
    empty_b = AgentRun(
        task_id="T-1",
        tool="grep",
        seed=0,
        final_answer="",
        cited_locations=[],
        cost=Cost(
            search_calls=0,
            llm_turns=0,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=0.0,
        ),
    )
    j = stage2(_task(), empty_a, {}, empty_b, {}, fake_llm)
    assert j.winner == "Tie"
    assert j.confidence == "low"
