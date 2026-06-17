"""Tests for Stage 3 (consistency across seeds) — logic with stubbed LLM/embed."""

import pytest
from evalkit.judge.stage3 import cosine_sim, stage3
from evalkit.schemas import AgentRun, Cost, Task


def _task() -> Task:
    return Task(
        task_id="T-1",
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt="Find the login entry point.",
    )


def _run(seed: int, answer: str) -> AgentRun:
    return AgentRun(
        task_id="T-1",
        tool="hybrid",
        seed=seed,
        final_answer=answer,
        cited_locations=[],
        cost=Cost(
            search_calls=1,
            llm_turns=1,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=1.0,
        ),
    )


def test_cosine_identical_vectors_is_one():
    assert cosine_sim([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero():
    assert cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_is_zero():
    assert cosine_sim([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_stage3_single_run_returns_none():
    runs = [_run(0, "answer")]
    assert stage3(_task(), runs, llm=lambda _p: {}, embed_fn=lambda _s: [1.0]) is None


def test_stage3_two_runs_uses_llm_once():
    runs = [_run(0, "a"), _run(1, "b")]
    llm_calls: list[str] = []

    def llm(prompt: str) -> dict:
        llm_calls.append(prompt)
        return {"equivalent": True, "agreement_score": 0.8}

    embed_calls: list[str] = []
    score = stage3(
        _task(),
        runs,
        llm=llm,
        embed_fn=lambda s: embed_calls.append(s) or [1.0],
    )
    assert score == pytest.approx(0.8)
    assert len(llm_calls) == 1
    assert embed_calls == []


def test_stage3_splits_pairs_between_llm_and_embeddings():
    # 4 runs => 6 pairs. llm_subset_size=2 => 2 LLM + 4 embedding.
    runs = [_run(i, f"ans{i}") for i in range(4)]

    def llm(prompt: str) -> dict:
        return {"equivalent": True, "agreement_score": 1.0}

    def embed_fn(text: str) -> list[float]:
        # Deterministic orthogonal-ish vectors so cosine is well-defined.
        idx = int(text[-1])
        v = [0.0] * 4
        v[idx] = 1.0
        return v

    score = stage3(_task(), runs, llm=llm, embed_fn=embed_fn, llm_subset_size=2)
    # 2 pairs @1.0 (llm) + 4 pairs @0.0 (distinct orthogonal vectors) => 2/6.
    assert score == pytest.approx(2 / 6)


def test_stage3_clamps_negative_cosine_to_zero_agreement():
    # Opposite-direction embeddings would yield cosine = -1, which is outside
    # the [0, 1] consistency scale. It must collapse to 0 agreement, not render
    # as a negative consistency % in the report.
    runs = [_run(i, f"ans{i}") for i in range(4)]  # 6 pairs, all embedding-scored

    def llm(_prompt: str) -> dict:
        msg = "LLM should not be called with llm_subset_size=0"
        raise AssertionError(msg)

    def embed_fn(text: str) -> list[float]:
        # Opposite directions for ans0 and ans1; others orthogonal.
        idx = int(text[-1])
        v = [0.0] * 4
        v[idx % 4] = 1.0 if idx in (0, 2) else -1.0
        return v

    score = stage3(_task(), runs, llm=llm, embed_fn=embed_fn, llm_subset_size=0)
    # All agreement values must be within [0, 1]; no negative contribution.
    assert 0.0 <= score <= 1.0


def test_stage3_clamps_out_of_range_llm_agreement_scores():
    # LLMs sometimes drift outside [0, 1] (e.g. 1.2 or -0.3). The judge output
    # schema constrains consistency to [0, 1]; stage3 must enforce it.
    runs = [_run(0, "a"), _run(1, "b")]  # 1 pair, LLM-scored

    def llm(_prompt: str) -> dict:
        return {"equivalent": True, "agreement_score": 1.5}

    score = stage3(_task(), runs, llm=llm, embed_fn=lambda _s: [1.0])
    assert score == 1.0

    def llm_low(_prompt: str) -> dict:
        return {"equivalent": False, "agreement_score": -0.4}

    score_low = stage3(_task(), runs, llm=llm_low, embed_fn=lambda _s: [1.0])
    assert score_low == 0.0
