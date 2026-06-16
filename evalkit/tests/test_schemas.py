"""Tests for evalkit data contracts (Task, AgentRun, Cost, JudgeOutput, etc.)."""

import pytest
from evalkit.schemas import (
    AgentRun,
    Cost,
    JudgeOutput,
    PairwiseJudgment,
    Task,
)


def _cost() -> Cost:
    return Cost(
        search_calls=3,
        llm_turns=5,
        tokens_in=1000,
        tokens_out=200,
        wall_clock_s=12.5,
    )


def test_task_trap_defaults_false():
    t = Task(
        task_id="T-001",
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt="Find the login entry point.",
    )
    assert t.trap is False


def test_judge_output_enforces_grounding_range():
    with pytest.raises(ValueError, match="grounding"):
        JudgeOutput(
            task_id="T-001",
            tool="hybrid",
            seed=0,
            grounding=6,
            existence_rate=1.0,
            faithfulness=3,
            ungrounded_claims=[],
            hallucinated_paths=[],
            consistency_vs_other_seeds=0.0,
        )


def test_agent_run_carries_cost_and_citations():
    run = AgentRun(
        task_id="T-001",
        tool="grep",
        seed=1,
        final_answer="see src/a.py:10",
        cited_locations=["src/a.py:10"],
        cost=_cost(),
    )
    assert run.cost.search_calls == 3
    assert run.cited_locations == ["src/a.py:10"]


def test_pairwise_judgment_winner_is_constrained():
    PairwiseJudgment(
        task_id="T-001", winner="hybrid", confidence="high", reasoning="..."
    )
    with pytest.raises(ValueError, match="winner"):
        PairwiseJudgment(
            task_id="T-001", winner="bogus", confidence="high", reasoning="..."
        )
