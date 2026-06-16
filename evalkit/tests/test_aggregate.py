"""Tests for aggregation math (Wilson CI, Spearman length-bias, profile)."""

import pytest
from evalkit.judge.aggregate import (
    build_profile,
    spearman_corr,
    verbosity_guard,
    wilson_ci,
)
from evalkit.schemas import AgentRun, Cost, JudgeOutput, PairwiseJudgment


def test_wilson_ci_no_data_is_fully_uncertain():
    assert wilson_ci(0, 0) == pytest.approx((0.0, 1.0))


def test_wilson_ci_zero_wins_lower_bounded_at_zero():
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0
    assert 0.0 < hi < 0.5


def test_wilson_ci_all_wins_upper_bounded_at_one():
    lo, hi = wilson_ci(10, 10)
    assert hi == 1.0
    assert lo > 0.5


def test_wilson_ci_half_wins_symmetric_around_half():
    lo, hi = wilson_ci(5, 10)
    # Known Wilson 95% values for 5/10.
    assert lo == pytest.approx(0.237, abs=0.005)
    assert hi == pytest.approx(0.763, abs=0.005)


def test_spearman_perfect_monotonic_is_one():
    assert spearman_corr([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_perfect_inverse_is_minus_one():
    assert spearman_corr([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_constant_series_is_zero():
    assert spearman_corr([1, 1, 1, 1], [1, 2, 3, 4]) == 0.0


def test_spearman_handles_ties():
    # Two tied values at the start; still well-defined.
    assert spearman_corr([1, 1, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.949, abs=0.01)


def test_verbosity_guard_flags_when_longer_answers_win():
    # n>=5 so the correlation is computed (not guarded as too small).
    res = verbosity_guard([100, 200, 300, 400, 500], [False, False, False, True, True])
    assert res["length_winrate_correlation"] == pytest.approx(0.866, abs=0.01)
    assert res["bias_flagged"] is True


def test_verbosity_guard_clean_when_shorter_answers_win():
    # Longer answers losing is the *opposite* of a verbosity bias.
    res = verbosity_guard([100, 200, 300, 400, 500], [True, True, True, False, False])
    assert res["bias_flagged"] is False


def test_verbosity_guard_threshold_is_strict_positive():
    # Correlation exactly 0 -> not flagged.
    res = verbosity_guard([100, 200, 300, 400, 500], [True, False, True, False, True])
    assert res["bias_flagged"] is False


def test_verbosity_guard_suppresses_correlation_below_min_n():
    res = verbosity_guard([100, 200, 300, 400], [False, False, True, True])
    assert res["length_winrate_correlation"] is None
    assert res["bias_flagged"] is False
    assert "too small" in res["note"]


# ── build_profile ──────────────────────────────────────────────────────
def _run(task_id: str, tool: str, answer: str, cost: Cost) -> AgentRun:
    return AgentRun(
        task_id=task_id,
        tool=tool,
        seed=0,
        final_answer=answer,
        cited_locations=[],
        cost=cost,
    )


def _jout(task_id: str, tool: str, grounding: int, faith: int) -> JudgeOutput:
    return JudgeOutput(
        task_id=task_id,
        tool=tool,
        seed=0,
        grounding=grounding,
        existence_rate=1.0,
        faithfulness=faith,
        ungrounded_claims=[],
        hallucinated_paths=[],
        consistency_vs_other_seeds=0.0,
    )


def test_build_profile_reports_per_tool_means_and_win_rate():
    runs = [
        _run(
            "T1",
            "hybrid",
            "x" * 100,
            Cost(
                search_calls=2,
                llm_turns=4,
                tokens_in=1000,
                tokens_out=100,
                wall_clock_s=10.0,
            ),
        ),
        _run(
            "T1",
            "grep",
            "x" * 50,
            Cost(
                search_calls=1,
                llm_turns=2,
                tokens_in=500,
                tokens_out=50,
                wall_clock_s=5.0,
            ),
        ),
        _run(
            "T2",
            "hybrid",
            "x" * 80,
            Cost(
                search_calls=2,
                llm_turns=4,
                tokens_in=900,
                tokens_out=90,
                wall_clock_s=9.0,
            ),
        ),
        _run(
            "T2",
            "grep",
            "x" * 200,
            Cost(
                search_calls=1,
                llm_turns=2,
                tokens_in=400,
                tokens_out=40,
                wall_clock_s=4.0,
            ),
        ),
    ]
    outputs = [
        _jout("T1", "hybrid", 4, 4),
        _jout("T1", "grep", 2, 2),
        _jout("T2", "hybrid", 3, 3),
        _jout("T2", "grep", 5, 5),
    ]
    pairwise = [
        PairwiseJudgment(task_id="T1", winner="hybrid", confidence="high"),
        PairwiseJudgment(task_id="T2", winner="grep", confidence="high"),
    ]

    profile = build_profile(outputs, pairwise, runs)

    assert profile["hybrid"]["grounding_mean"] == pytest.approx(3.5)
    assert profile["hybrid"]["faithfulness_mean"] == pytest.approx(3.5)
    assert profile["grep"]["grounding_mean"] == pytest.approx(3.5)
    # Each tool won 1 of 2 => 50% win rate.
    assert profile["hybrid"]["preference_win_rate_pct"] == pytest.approx(50.0)
    assert profile["grep"]["preference_win_rate_pct"] == pytest.approx(50.0)
    # Median wall-clock across the two hybrid runs: median([10, 9]) = 9.5.
    assert profile["hybrid"]["cost"]["median_wall_clock_s"] == pytest.approx(9.5)


def test_build_profile_has_wilson_ci_and_bias_check():
    runs = [
        _run(
            "T1",
            "hybrid",
            "short",
            Cost(
                search_calls=1,
                llm_turns=1,
                tokens_in=0,
                tokens_out=0,
                wall_clock_s=1.0,
            ),
        )
    ]
    outputs = [_jout("T1", "hybrid", 4, 4)]
    pairwise = [PairwiseJudgment(task_id="T1", winner="hybrid", confidence="high")]

    profile = build_profile(outputs, pairwise, runs)

    assert "preference_win_rate_ci" in profile["hybrid"]
    assert "bias_check" in profile
    assert "length_winrate_correlation" in profile["bias_check"]


def test_build_profile_reports_median_tokens_from_per_run_totals():
    runs = [
        _run(
            "T1",
            "hybrid",
            "short",
            Cost(
                search_calls=1,
                llm_turns=1,
                tokens_in=0,
                tokens_out=0,
                wall_clock_s=1.0,
            ),
        ),
        _run(
            "T2",
            "hybrid",
            "medium",
            Cost(
                search_calls=1,
                llm_turns=1,
                tokens_in=0,
                tokens_out=100,
                wall_clock_s=1.0,
            ),
        ),
        _run(
            "T3",
            "hybrid",
            "long",
            Cost(
                search_calls=1,
                llm_turns=1,
                tokens_in=100,
                tokens_out=0,
                wall_clock_s=1.0,
            ),
        ),
    ]
    outputs = [
        _jout("T1", "hybrid", 4, 4),
        _jout("T2", "hybrid", 4, 4),
        _jout("T3", "hybrid", 4, 4),
    ]

    profile = build_profile(outputs, [], runs)

    assert profile["hybrid"]["cost"]["median_tokens"] == pytest.approx(100.0)
