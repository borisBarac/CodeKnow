"""Aggregation — per-tool profile, Wilson CI, length-bias check, significance tests.

``calculate_final_score`` deliberately returns a *profile* dict rather than a
single headline number, per JUDGE_PRINCIPLES section 7: collapsing to one
score hides findings like "wins preference but costs 5x the tokens".
"""

from __future__ import annotations

import math
from statistics import mean, median
from typing import TYPE_CHECKING, Any

from scipy.stats import binomtest, wilcoxon

if TYPE_CHECKING:
    from collections.abc import Sequence

    from evalkit.schemas import AgentRun, JudgeOutput, PairwiseJudgment

# Below this many pairwise verdicts (or length/win pairs), Wilson CIs and the
# length-bias correlation are noise rather than signal — they are suppressed so
# the profile does not render misleadingly precise numbers at small n.
MIN_CI_N = 5
MIN_BIAS_N = 5


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial win-rate.

    Returns ``(0.0, 1.0)`` when ``n == 0`` (no trials => maximally uncertain),
    so callers can render a column without guarding against division by zero.
    """
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half_width = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, center - half_width), min(1.0, center + half_width)


def _rank(data: list[float]) -> list[float]:
    """Average ranks (1-indexed) handling ties."""
    order = sorted(range(len(data)), key=lambda i: data[i])
    ranks = [0.0] * len(data)
    i = 0
    while i < len(data):
        j = i
        while j + 1 < len(data) and data[order[j + 1]] == data[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n == 0:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=True))
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def spearman_corr(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation in ``[-1, 1]``.

    Returns ``0.0`` for a constant series (no monotone signal). Pure Python,
    no numpy dependency.
    """
    if len(x) != len(y) or not x:
        return 0.0
    return _pearson(_rank(x), _rank(y))


def verbosity_guard(
    lengths: list[int],
    won: list[bool],
    threshold: float = 0.3,
    min_n: int = MIN_BIAS_N,
) -> dict[str, Any]:
    """JUDGE_PRINCIPLES section 9 length-bias check.

    Computes the Spearman correlation between answer length and whether the
    answer won its pairwise comparison. A **positive** correlation above
    ``threshold`` means longer answers are winning on style rather than
    substance — the verbosity mitigation is failing. Negative correlation
    (longer answers losing) is not a bias.

    Below ``min_n`` pairs the correlation is noise, not signal, so it is
    suppressed (``None``) and the bias is not flagged; a ``note`` explains why.
    """
    if len(lengths) < min_n:
        return {
            "length_winrate_correlation": None,
            "bias_flagged": False,
            "note": f"n too small (<{min_n} pairs)",
        }
    corr = spearman_corr([float(n) for n in lengths], [1.0 if w else 0.0 for w in won])
    return {
        "length_winrate_correlation": corr,
        "bias_flagged": corr > threshold,
    }


def _mcnemar_preference(
    pairwise: list[PairwiseJudgment], tools: Sequence[str]
) -> float | None:
    """Exact McNemar test on discordant pairwise verdicts.

    Tests whether the two tools win equally often among discordant pairs
    (ties excluded). Returns the two-sided p-value, or ``None`` when there
    are no discordant pairs or the comparison is not exactly two-tool.
    """
    if len(tools) != 2:
        return None
    b = sum(1 for p in pairwise if p.winner == tools[0])
    c = sum(1 for p in pairwise if p.winner == tools[1])
    n_discordant = b + c
    if n_discordant == 0:
        return None
    return float(
        binomtest(min(b, c), n_discordant, p=0.5, alternative="two-sided").pvalue
    )


def _wilcoxon_score(
    outputs: list[JudgeOutput], tools: Sequence[str], field: str
) -> float | None:
    """Wilcoxon signed-rank test on paired per-task scores.

    Tests whether the median paired score difference between the two tools is
    significantly different from zero. When multiple seeds exist per task the
    scores are averaged first. Returns ``None`` when fewer than ``MIN_CI_N``
    tasks have scores for both tools, or when all differences are zero.
    """
    if len(tools) != 2:
        return None
    task_scores: dict[str, dict[str, list[float]]] = {}
    for o in outputs:
        if o.tool in tools:
            task_scores.setdefault(o.task_id, {}).setdefault(o.tool, []).append(
                float(getattr(o, field))
            )
    common = [t for t, d in task_scores.items() if tools[0] in d and tools[1] in d]
    if len(common) < MIN_CI_N:
        return None
    diffs = [
        mean(task_scores[t][tools[0]]) - mean(task_scores[t][tools[1]]) for t in common
    ]
    if all(d == 0 for d in diffs):
        return None
    try:
        return float(wilcoxon(diffs, alternative="two-sided").pvalue)
    except ValueError:
        return None


def _median_cost(costs: list[AgentRun], field: str) -> float:
    return float(median([getattr(r.cost, field) for r in costs]))


def _median_total_tokens(costs: list[AgentRun]) -> float:
    return float(median([r.cost.tokens_in + r.cost.tokens_out for r in costs]))


def build_profile(
    outputs: list[JudgeOutput],
    pairwise: list[PairwiseJudgment],
    runs: list[AgentRun],
) -> dict[str, Any]:
    """Per-tool evaluation profile (no headline blend, per section 7).

    Each tool gets grounding/faithfulness means, consistency %, preference
    win-rate with Wilson CI, and median cost. A global ``bias_check`` covers
    the section-9 verbosity guard. Significance tests (McNemar for preference,
    Wilcoxon for grounding/faithfulness) are computed via scipy.
    """
    tools = sorted({o.tool for o in outputs} | {r.tool for r in runs})
    wins_by_tool: dict[str, int] = dict.fromkeys(tools, 0)
    for p in pairwise:
        if p.winner != "Tie":
            wins_by_tool[p.winner] = wins_by_tool.get(p.winner, 0) + 1
    n_pairwise = len(pairwise)

    profile: dict[str, Any] = {}
    winner_by_task = {p.task_id: p.winner for p in pairwise}
    for tool in tools:
        tool_outputs = [o for o in outputs if o.tool == tool]
        tool_runs = [r for r in runs if r.tool == tool]

        wins = wins_by_tool.get(tool, 0)
        win_rate = wins / n_pairwise if n_pairwise else 0.0
        ci = wilson_ci(wins, n_pairwise) if n_pairwise >= MIN_CI_N else None

        consistencies = [
            c
            for c in (o.consistency_vs_other_seeds for o in tool_outputs)
            if c is not None
        ]
        consistency_pct = mean(consistencies) * 100 if consistencies else None

        profile[tool] = {
            "grounding_mean": mean([o.grounding for o in tool_outputs]),
            "faithfulness_mean": mean([o.faithfulness for o in tool_outputs]),
            "consistency_pct": consistency_pct,
            "preference_win_rate_pct": win_rate * 100,
            "preference_win_rate_ci": ci,
            "cost": {
                "median_tokens": _median_total_tokens(tool_runs),
                "median_search_calls": _median_cost(tool_runs, "search_calls"),
                "median_wall_clock_s": _median_cost(tool_runs, "wall_clock_s"),
            },
        }

    all_lengths = [len(r.final_answer) for r in runs]
    all_won = [winner_by_task.get(r.task_id) == r.tool for r in runs]
    profile["bias_check"] = verbosity_guard(all_lengths, all_won)
    profile["stats"] = {
        "mcnemar_preference_p": _mcnemar_preference(pairwise, tools),
        "wilcoxon_grounding_p": _wilcoxon_score(outputs, tools, "grounding"),
        "wilcoxon_faithfulness_p": _wilcoxon_score(outputs, tools, "faithfulness"),
    }
    return profile
