"""Tests for the LLMJudge orchestrator — full pipeline with stubbed LLM."""

from pathlib import Path

from evalkit.judge.judge import LLMJudge, _majority
from evalkit.schemas import AgentRun, Cost, PairwiseJudgment, Task


def _task(task_id: str = "T-1") -> Task:
    return Task(
        task_id=task_id,
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt="Find the login entry point.",
    )


def _run(task_id: str, tool: str, seed: int, cite: str) -> AgentRun:
    return AgentRun(
        task_id=task_id,
        tool=tool,
        seed=seed,
        final_answer=f"{tool} answer citing {cite}",
        cited_locations=[cite],
        cost=Cost(
            search_calls=1,
            llm_turns=1,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=1.0,
        ),
    )


def _stub_llm(prompt: str) -> dict:
    # Stage 2 prompt mentions "Candidate"; stage 3 mentions "equivalent".
    if "Candidate 1" in prompt:
        return {"reasoning": "even", "winner": "Tie", "confidence": "high"}
    if "equivalent" in prompt:
        return {"equivalent": True, "agreement_score": 0.9}
    # Stage 1.
    return {
        "grounding": 4,
        "faithfulness": 4,
        "ungrounded_claims": [],
        "hallucinated_paths": [],
    }


def test_judge_run_runs_stage0_and_stage1(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def login():\n    pass\n", encoding="utf-8")
    judge = LLMJudge(repo_root=tmp_path, llm=_stub_llm)

    out = judge.judge_run(_task(), _run("T-1", "hybrid", 0, "src/a.py:1"))
    assert out.grounding == 4
    assert out.faithfulness == 4
    assert out.existence_rate == 1.0  # src/a.py exists
    assert out.consistency_vs_other_seeds is None  # not measured per-run


def test_judge_run_nonempty_answer_without_citations_is_judged(tmp_path: Path):
    # A substantively-correct prose answer that names no file:line must NOT be
    # hard-zeroed like a hallucination. Stage 1 still calls the LLM (which may
    # award partial faithfulness), existence is vacuous (None), and the missing
    # citation signal surfaces as an ungrounded claim.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def login():\n    pass\n", encoding="utf-8")

    def partial_llm(_prompt: str) -> dict:
        return {
            "grounding": 0,
            "faithfulness": 3,
            "ungrounded_claims": [],
            "hallucinated_paths": [],
        }

    judge = LLMJudge(repo_root=tmp_path, llm=partial_llm)
    run = AgentRun(
        task_id="T-1",
        tool="grep",
        seed=0,
        final_answer="The login entry point lives in the auth module.",
        cited_locations=[],
        cost=Cost(
            search_calls=2,
            llm_turns=3,
            tokens_in=0,
            tokens_out=0,
            wall_clock_s=1.0,
        ),
    )

    out = judge.judge_run(_task(), run)

    assert out.faithfulness == 3  # not hard-zeroed
    assert out.existence_rate is None  # vacuous, not 0%
    assert "answer contains no file:line citations" in out.ungrounded_claims


def test_judge_run_threads_snippet_context_to_stage0(tmp_path: Path):
    (tmp_path / "src").mkdir()
    lines = [f"line-{i:02d}" for i in range(1, 21)]
    (tmp_path / "src" / "a.py").write_text("\n".join(lines), encoding="utf-8")

    prompts: list[str] = []

    def capture_llm(prompt: str) -> dict:
        prompts.append(prompt)
        return _stub_llm(prompt)

    run = _run("T-1", "hybrid", 0, "src/a.py:10")

    LLMJudge(repo_root=tmp_path, llm=capture_llm, snippet_context=2).judge_run(
        _task(), run
    )
    narrow_prompt = prompts.pop()
    assert "line-08" in narrow_prompt
    assert "line-12" in narrow_prompt
    assert "line-07" not in narrow_prompt
    assert "line-13" not in narrow_prompt

    LLMJudge(repo_root=tmp_path, llm=capture_llm, snippet_context=5).judge_run(
        _task(), run
    )
    wide_prompt = prompts.pop()
    assert "line-05" in wide_prompt
    assert "line-15" in wide_prompt
    assert "line-04" not in wide_prompt
    assert "line-16" not in wide_prompt


def test_judge_all_single_seed_skips_consistency(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    judge = LLMJudge(repo_root=tmp_path, llm=_stub_llm)

    tasks = [_task("T-1")]
    runs = [
        _run("T-1", "hybrid", 0, "src/a.py:1"),
        _run("T-1", "grep", 0, "src/b.py:1"),
    ]
    outputs, pairwise = judge.judge_all(tasks, runs)

    assert len(outputs) == 2
    assert all(o.consistency_vs_other_seeds is None for o in outputs)
    assert len(pairwise) == 1
    assert pairwise[0].task_id == "T-1"
    assert pairwise[0].winner == "Tie"


def test_judge_all_fills_consistency_when_two_seeds(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    judge = LLMJudge(
        repo_root=tmp_path,
        llm=_stub_llm,
        embed_fn=lambda _text: [1.0, 0.0],
    )

    tasks = [_task("T-1")]
    runs = [
        _run("T-1", "hybrid", 0, "src/a.py:1"),
        _run("T-1", "hybrid", 1, "src/a.py:1"),
    ]
    outputs, pairwise = judge.judge_all(tasks, runs)

    # One consistency pair => LLM-judged (subset size >= 1) => 0.9.
    assert outputs[0].consistency_vs_other_seeds == 0.9
    assert pairwise == []  # only one tool => no cross-tool pair


def test_majority_returns_tie_for_evenly_split_verdicts():
    verdict = _majority(
        "T-1",
        [
            PairwiseJudgment(task_id="T-1", winner="hybrid", confidence="high"),
            PairwiseJudgment(task_id="T-1", winner="grep", confidence="high"),
        ],
    )

    assert verdict.winner == "Tie"
    assert verdict.confidence == "low"


def test_judge_run_reconciles_hallucinated_paths(tmp_path: Path):
    # The LLM over-flags: an existing file, a bare relative ref, and a
    # genuinely missing file are ALL reported as "hallucinated." Only the
    # truly missing one should survive; the rest become unsupported_ranges.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def login():\n    pass\n", encoding="utf-8")

    def flagging_llm(_prompt: str) -> dict:
        return {
            "grounding": 2,
            "faithfulness": 2,
            "ungrounded_claims": [],
            "hallucinated_paths": ["src/a.py:1", ":5", "missing.py:99"],
        }

    judge = LLMJudge(repo_root=tmp_path, llm=flagging_llm)
    run = _run("T-1", "hybrid", 0, "src/a.py:1")
    run.cited_locations = ["src/a.py:1", "missing.py:99"]

    out = judge.judge_run(_task(), run)

    assert out.hallucinated_paths == ["missing.py:99"]
    assert out.unsupported_ranges == ["src/a.py:1", ":5"]
