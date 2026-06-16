"""Tests for Stage 1 (grounding + faithfulness) — logic with a stubbed LLM."""

from evalkit.judge.stage1 import format_cited_code, stage1
from evalkit.schemas import AgentRun, Cost, Task


def _task() -> Task:
    return Task(
        task_id="T-1",
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt="Find the login entry point.",
    )


def _run() -> AgentRun:
    return AgentRun(
        task_id="T-1",
        tool="hybrid",
        seed=0,
        final_answer="Login starts at src/auth/login.py:10.",
        cited_locations=["src/auth/login.py:10", "missing.py:99"],
        cost=Cost(
            search_calls=1,
            llm_turns=2,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=1.0,
        ),
    )


def test_format_cited_code_shows_snippet_and_marks_missing():
    snippets = {"src/auth/login.py:10": "def login():", "missing.py:99": None}
    block = format_cited_code(snippets)
    assert "### src/auth/login.py:10\ndef login():" in block
    assert "### missing.py:99\n[FILE NOT FOUND]" in block


def test_stage1_maps_llm_json_to_judge_output():
    stage0_snippets = {"src/auth/login.py:10": "def login():", "missing.py:99": None}

    def fake_llm(prompt: str) -> dict:
        assert "<TASK>Find the login entry point.</TASK>" in prompt
        assert "[FILE NOT FOUND]" in prompt
        return {
            "grounding": 4,
            "faithfulness": 3,
            "ungrounded_claims": ["claims rate limit; not in code"],
            "hallucinated_paths": ["missing.py:99"],
        }

    out = stage1(_task(), _run(), stage0_snippets, existence_rate=0.5, llm=fake_llm)

    assert out.task_id == "T-1"
    assert out.tool == "hybrid"
    assert out.grounding == 4
    assert out.faithfulness == 3
    assert out.existence_rate == 0.5
    assert out.ungrounded_claims == ["claims rate limit; not in code"]
    assert out.hallucinated_paths == ["missing.py:99"]


def test_stage1_clamps_out_of_range_llm_scores():
    def fake_llm(prompt: str) -> dict:
        return {"grounding": 99, "faithfulness": -3}

    out = stage1(_task(), _run(), {}, existence_rate=1.0, llm=fake_llm)
    assert out.grounding == 5
    assert out.faithfulness == 0


def test_stage1_empty_answer_scores_zero_without_llm():
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
            wall_clock_s=1.0,
        ),
    )

    def fake_llm(_prompt: str) -> dict:
        msg = "LLM should not be called for empty answer"
        raise AssertionError(msg)

    out = stage1(_task(), empty_run, {}, existence_rate=None, llm=fake_llm)
    assert out.grounding == 0
    assert out.faithfulness == 0
    assert out.existence_rate is None
    assert "empty answer" in out.ungrounded_claims


def test_stage1_no_citations_is_judged_and_flagged():
    # A non-empty answer with no citations is still judged by the LLM (it may
    # earn partial faithfulness) rather than hard-zeroed; the missing-citation
    # signal is prepended to ungrounded_claims.
    no_cite_run = AgentRun(
        task_id="T-1",
        tool="hybrid",
        seed=0,
        final_answer="The answer is somewhere in the codebase.",
        cited_locations=[],
        cost=Cost(
            search_calls=2,
            llm_turns=3,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=1.0,
        ),
    )

    def fake_llm(_prompt: str) -> dict:
        return {
            "grounding": 1,
            "faithfulness": 3,
            "ungrounded_claims": ["vague"],
            "hallucinated_paths": [],
        }

    out = stage1(_task(), no_cite_run, {}, existence_rate=None, llm=fake_llm)
    assert out.grounding == 1  # LLM score passes through (not hard-zeroed)
    assert out.faithfulness == 3
    assert out.existence_rate is None
    assert out.ungrounded_claims[0] == "answer contains no file:line citations"
    assert "vague" in out.ungrounded_claims
