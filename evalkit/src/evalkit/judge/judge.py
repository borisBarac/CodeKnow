"""LLMJudge — orchestrates the 3-stage reference-free pipeline.

Stage 0 + Stage 1 run per ``AgentRun``; Stage 3 (consistency) fills in per
task+tool group with >=2 seeds; Stage 2 (pairwise) runs across tools per task.
The LLM and embedding function are injected (dependency injection) so the
full pipeline is unit-testable with stubs; production binds
:func:`evalkit.llm.make_llm_callable`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from itertools import product
from typing import TYPE_CHECKING

from evalkit.judge.stage0 import Stage0Result, resolve_citation_path, stage0
from evalkit.judge.stage1 import stage1
from evalkit.judge.stage2 import stage2
from evalkit.judge.stage3 import stage3
from evalkit.llm import make_llm_callable
from evalkit.schemas import PairwiseJudgment, Winner

if TYPE_CHECKING:
    from pathlib import Path

    from evalkit.schemas import AgentRun, JudgeOutput, Task

EmbedFn = Callable[[str], list[float]]


def _default_embed_fn(text: str) -> list[float]:
    from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

    embeddings = create_embeddings(EmbeddingConfig())
    return embeddings.embed_query(text)


class LLMJudge:
    """Runs the 3-stage judge over tasks and agent runs."""

    def __init__(
        self,
        repo_root: Path,
        llm: Callable[[str], dict] | None = None,
        embed_fn: EmbedFn | None = None,
        snippet_context: int = 50,
    ) -> None:
        self.repo_root = repo_root
        self.llm = llm or make_llm_callable()
        self.embed_fn = embed_fn or _default_embed_fn
        self.snippet_context = snippet_context

    def judge_run(
        self,
        task: Task,
        run: AgentRun,
        s0_cache: dict[tuple[str, str, int], Stage0Result] | None = None,
    ) -> JudgeOutput:
        """Stage 0 (deterministic) + Stage 1 (grounding/faithfulness).

        Stage 1's ``hallucinated_paths`` is LLM-emitted and often conflates
        "cited range not shown in the snippet" with "file does not exist."
        We reconcile it against Stage 0's existence verdict: a flagged path
        whose file actually exists moves to ``unsupported_ranges``; only
        genuinely missing files stay as hallucinated.

        When ``s0_cache`` is supplied (as ``judge_all`` does), the Stage 0
        result is memoised into it keyed by ``(task_id, tool, seed)`` so the
        pairwise stage can reuse the snippets without recomputing Stage 0.
        """
        s0 = self._compute_stage0(run, s0_cache)
        out = stage1(task, run, s0.snippets, s0.existence_rate, self.llm)
        out.hallucinated_paths, out.unsupported_ranges = _reconcile_hallucinations(
            out.hallucinated_paths, s0.existence_map, self.repo_root
        )
        return out

    def _compute_stage0(
        self,
        run: AgentRun,
        s0_cache: dict[tuple[str, str, int], Stage0Result] | None,
    ) -> Stage0Result:
        """Run Stage 0 for ``run``, memoising into ``s0_cache`` when given.

        Avoids the redundant filesystem walk + snippet extraction that the
        pairwise stage would otherwise repeat per run.
        """
        key = (run.task_id, run.tool, run.seed)
        if s0_cache is not None and key in s0_cache:
            return s0_cache[key]
        result = stage0(
            run.cited_locations,
            self.repo_root,
            context=self.snippet_context,
        )
        if s0_cache is not None:
            s0_cache[key] = result
        return result

    def judge_all(
        self,
        tasks: list[Task],
        runs: list[AgentRun],
    ) -> tuple[list[JudgeOutput], list[PairwiseJudgment]]:
        """Judge every run, then consistency per group, then pairwise per task."""
        task_map = {t.task_id: t for t in tasks}
        s0_cache: dict[tuple[str, str, int], Stage0Result] = {}
        outputs = [self.judge_run(task_map[r.task_id], r, s0_cache) for r in runs]

        self._fill_consistency(tasks, runs, outputs)
        pairwise = self._pairwise(tasks, runs, s0_cache)
        return outputs, pairwise

    def _fill_consistency(
        self,
        tasks: list[Task],
        runs: list[AgentRun],
        outputs: list[JudgeOutput],
    ) -> None:
        task_map = {t.task_id: t for t in tasks}
        groups: dict[tuple[str, str], list[AgentRun]] = defaultdict(list)
        for r in runs:
            groups[(r.task_id, r.tool)].append(r)

        for (task_id, tool), group_runs in groups.items():
            if len(group_runs) < 2:
                continue
            consistency = stage3(task_map[task_id], group_runs, self.llm, self.embed_fn)
            if consistency is None:
                continue
            for out in outputs:
                if out.task_id == task_id and out.tool == tool:
                    out.consistency_vs_other_seeds = consistency

    def _pairwise(
        self,
        tasks: list[Task],
        runs: list[AgentRun],
        s0_cache: dict[tuple[str, str, int], Stage0Result],
    ) -> list[PairwiseJudgment]:
        snippets_cache = {
            (r.task_id, r.tool, r.seed): self._compute_stage0(r, s0_cache).snippets
            for r in runs
        }
        verdicts: list[PairwiseJudgment] = []
        for task in tasks:
            task_runs = [r for r in runs if r.task_id == task.task_id]
            tools = sorted({r.tool for r in task_runs})
            if len(tools) < 2:
                continue
            runs_a = [r for r in task_runs if r.tool == tools[0]]
            runs_b = [r for r in task_runs if r.tool == tools[1]]
            task_verdicts: list[PairwiseJudgment] = []
            for ra, rb in product(runs_a, runs_b):
                verdict = stage2(
                    task,
                    ra,
                    snippets_cache[(ra.task_id, ra.tool, ra.seed)],
                    rb,
                    snippets_cache[(rb.task_id, rb.tool, rb.seed)],
                    self.llm,
                )
                task_verdicts.append(verdict)
            verdicts.append(_majority(task.task_id, task_verdicts))
        return verdicts


def _reconcile_hallucinations(
    flagged: list[str],
    existence_map: dict[str, bool],
    repo_root: Path,
) -> tuple[list[str], list[str]]:
    """Split LLM-flagged paths into ``(hallucinated, unsupported)``.

    A flagged entry is hallucinated only if its file does not exist — checked
    against Stage 0's ``existence_map`` and, failing that, on disk at
    ``repo_root``. Entries whose file exists (the LLM objected to the range, not
    the path) move to ``unsupported_ranges``. Bare relative refs like ``:34``
    (no file) or ``34`` (not path-like) cannot be proven missing, so they are
    treated as unsupported rather than hallucinated.
    """
    existing_files = {
        _file_of(citation)
        for citation, exists in existence_map.items()
        if exists and _file_of(citation)
    }
    hallucinated: list[str] = []
    unsupported: list[str] = []
    for entry in flagged:
        file_path = _file_of(entry)
        if (
            _looks_like_path(file_path)
            and file_path not in existing_files
            and resolve_citation_path(file_path, repo_root) is None
        ):
            hallucinated.append(entry)
        else:
            unsupported.append(entry)
    return hallucinated, unsupported


def _file_of(entry: str) -> str:
    """Return the file path half of a citation-like string.

    ``"lib/a.js:42"`` -> ``"lib/a.js"``; ``"lib/a.js"`` -> ``"lib/a.js"``;
    ``":42"`` -> ``""``. A trailing ``:line`` or ``:N-M`` is stripped only when
    it parses as a line/range; otherwise the whole string is returned as-is.
    """
    path, sep, tail = entry.rpartition(":")
    if sep and tail.strip().replace("-", "").isdigit():
        return path.strip()
    return entry.strip()


def _looks_like_path(text: str) -> bool:
    """Heuristic: a path has an extension (``.``) or a directory separator."""
    return bool(text) and ("." in text or "/" in text)


def _majority(task_id: str, verdicts: list[PairwiseJudgment]) -> PairwiseJudgment:
    """Reduce cross-tool seed-pair verdicts to one verdict per task."""
    if len(verdicts) == 1:
        verdicts[0].task_id = task_id
        return verdicts[0]
    counts: dict[Winner, int] = defaultdict(int)
    for v in verdicts:
        counts[v.winner] += 1
    max_count = max(counts.values())
    leaders = [w for w, count in counts.items() if count == max_count]
    winner: Winner = leaders[0] if len(leaders) == 1 else "Tie"
    confidence = "medium" if counts[winner] > len(verdicts) / 2 else "low"
    return PairwiseJudgment(
        task_id=task_id, winner=winner, confidence=confidence, reasoning=""
    )
