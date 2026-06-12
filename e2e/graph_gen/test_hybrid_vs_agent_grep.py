"""E2E test: hybrid search (vector + graph) vs agent-grep baseline, judged by LLM.

The agent-grep baseline uses an LLM agent that calls ripgrep as its tool.
Two LLM rounds: (1) analyze query → generate grep commands, (2) rank results.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from judge import JudgeHit, JudgeInput, LLMJudge
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from test_hybrid_search import (
    _JUDGE_KEY,
    _REPO_BRIEF,
    _TRAVERSAL_DEPTH,
    CODE_TEST_SMALL,
    _enforce_semantic_saturation,
    _print_judge_report,
    _search,
    from_hybrid_response,
)

# ruff: noqa: T201

logger = logging.getLogger(__name__)

QUERY = "how does user authentication work"

RESULT_MD = Path(__file__).parent / "test_hybrid_vs_agent_grep_report.md"


def _write_md_report(text: str) -> None:
    RESULT_MD.write_text(RESULT_MD.read_text() + text, encoding="utf-8")


RESULT_MD.write_text(
    f"# Hybrid vs Agent-Grep E2E Results\n\n"
    f"Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n",
    encoding="utf-8",
)

# ── Agent LLM configuration (reuses judge env vars) ────────────────────


class _AgentConfig(BaseSettings):
    model: str = "openai/gpt-4o-mini"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.0

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        fallback = os.environ.get("OPENROUTER_API_KEY")
        if fallback:
            return fallback
        msg = "Agent requires an API key. Set JUDGE_LLM_API_KEY or OPENROUTER_API_KEY."
        raise ValueError(msg)

    model_config = {
        "env_prefix": "JUDGE_LLM_",
        "env_file": ".env",
        "extra": "ignore",
    }


_AGENT_CONFIG = _AgentConfig()

# ── Agent prompts ──────────────────────────────────────────────────────

_CMD_SYSTEM = """\
You are a precise code-search agent. Your only tool is ripgrep (rg).

Given a developer question and a brief description of the repository, generate
targeted rg commands to search the codebase. Think step by step internally,
then output only commands.

Rules:
- Use `rg -C 3 -n 'pattern' .` format (3 lines of context)
- Prefer focused patterns: combine related terms with |, use word boundaries
- Use -g or -t scoping only when the question points to specific file types
- Generate 2-5 commands total
- Output each command on its own line starting with "CMD: "
- The working directory is the repo root — use "." as the search path unless scoping

Output format (nothing else):
CMD: rg -C 3 -n 'pattern1|pattern2' .
CMD: rg -C 3 -n 'another_pattern' src/
"""

_RANK_SYSTEM = """\
You are a precise code-search agent evaluating grep results.

You will receive raw grep output from searching a codebase. Identify the 10 most
relevant hits for answering the original question and rank them.

Output format — exactly 10 hits separated by "---":

RANK: 1
FILE: src/server/auth.tsx
LINE: 8
RELEVANCE_REASON: one sentence why this matters
SNIPPET:
the exact snippet from grep output
---
RANK: 2
...

Rules:
- Exactly 10 hits (repeat the best if fewer unique results exist)
- Snippets must be exact copy-paste from the grep output — do NOT modify
- FILE must match the path as it appears in grep output
- LINE is the starting line number as shown in grep output
"""

# ── Grep execution ─────────────────────────────────────────────────────


def _run_grep_commands(commands: list[str], repo_dir: Path) -> str:
    """Execute a list of rg commands and return combined output."""
    combined: list[str] = []
    for cmd in commands:
        try:
            result = subprocess.run(  # noqa: S603
                shlex.split(cmd),
                capture_output=True,
                text=True,
                cwd=str(repo_dir),
                timeout=30,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                combined.append(result.stdout.strip())
            elif result.returncode == 1:
                continue
            else:
                logger.warning("rg command %r stderr: %s", cmd, result.stderr[:300])
        except subprocess.TimeoutExpired:
            logger.warning("rg command %r timed out", cmd)
        except FileNotFoundError:
            logger.warning("rg not found")
            return ""

    return "\n--\n".join(combined)


# ── Agent LLM calls ────────────────────────────────────────────────────


def _generate_grep_commands(query: str, repo_brief: str) -> list[str]:
    """LLM analyzes query and returns grep commands."""
    llm = ChatOpenAI(
        model=_AGENT_CONFIG.model,
        base_url=_AGENT_CONFIG.base_url,
        api_key=SecretStr(_AGENT_CONFIG.resolved_api_key()),
        temperature=_AGENT_CONFIG.temperature,
        request_timeout=60,
        max_retries=2,
    )
    user_prompt = f"QUERY: {query}\n\nREPO: {repo_brief}"
    messages = [SystemMessage(content=_CMD_SYSTEM), HumanMessage(content=user_prompt)]
    result = llm.invoke(messages)
    content = str(result.content)
    logger.info("Agent grep commands:\n%s", content)

    commands: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("CMD:"):
            cmd = stripped[4:].strip()
            if cmd:
                commands.append(cmd)
    return commands


def _rank_grep_results(
    query: str,
    repo_brief: str,
    grep_output: str,
) -> list[dict[str, object]]:
    """LLM ranks grep output and returns top 10 hits."""
    llm = ChatOpenAI(
        model=_AGENT_CONFIG.model,
        base_url=_AGENT_CONFIG.base_url,
        api_key=SecretStr(_AGENT_CONFIG.resolved_api_key()),
        temperature=_AGENT_CONFIG.temperature,
        request_timeout=60,
        max_retries=2,
    )
    truncated = grep_output[:12000]
    user_prompt = f"QUERY: {query}\n\nREPO: {repo_brief}\n\nGREP OUTPUT:\n{truncated}"
    messages = [SystemMessage(content=_RANK_SYSTEM), HumanMessage(content=user_prompt)]
    result = llm.invoke(messages)
    content = str(result.content)
    logger.info("Agent ranking:\n%s", content[:2000])

    return _parse_ranked_hits(content)


def _parse_ranked_hits(agent_output: str) -> list[dict[str, object]]:
    """Parse the agent's RANK / FILE / LINE / RELEVANCE_REASON / SNIPPET output."""
    hits: list[dict[str, object]] = []
    current: dict[str, object] = {}
    field: str | None = None
    buffer: list[str] = []

    for line in agent_output.splitlines():
        line = line.rstrip()
        if line.startswith("---"):
            if current and "FILE" in current:
                if buffer:
                    current["SNIPPET"] = "\n".join(buffer)
                    buffer = []
                hits.append(current)
            current = {}
            field = None
            continue

        m = re.match(r"^([A-Z_]+):\s*(.*)", line)
        if m:
            key, value = m.groups()
            if key == "RANK":
                continue
            if key == "LINE":
                try:
                    current[key] = int(value)
                except ValueError:
                    current[key] = 1
            elif key == "SNIPPET":
                field = "SNIPPET"
                if value:
                    buffer.append(value)
            else:
                current[key] = value
                field = key if key == "SNIPPET" else None
        elif field == "SNIPPET":
            buffer.append(line)

    if current and "FILE" in current:
        if buffer:
            current["SNIPPET"] = "\n".join(buffer)
        hits.append(current)

    return hits


# ── Conversion to JudgeInput ───────────────────────────────────────────


def _from_grep_results(
    ranked: list[dict[str, object]],
    query: str,
    repo_dir: Path,
    repo_brief: str,
    agent_analysis: str,
) -> JudgeInput:
    """Convert agent-ranked grep hits to JudgeInput."""
    hits: list[JudgeHit] = []
    for item in ranked:
        file_path = str(item.get("FILE", ""))
        line_num = int(item.get("LINE", 0))  # type: ignore[arg-type]
        snippet = str(item.get("SNIPPET", ""))
        reason = str(item.get("RELEVANCE_REASON", ""))
        full_path = str(repo_dir / file_path) if file_path else ""

        hit_id = hashlib.sha256(f"{full_path}:{line_num}".encode()).hexdigest()[:12]

        hits.append(
            JudgeHit(
                id=hit_id,
                file_path=full_path,
                symbol=None,
                kind="semantic",
                snippet=snippet[:2000] if snippet else None,
                score=None,
                relation_to_seed=None,
                why_retrieved=f"grep agent: {reason}" if reason else "grep match",
                relation_type=None,
                relation_weight=None,
                cumulative_weight=None,
            )
        )

    return JudgeInput(
        query=query,
        repo_brief=repo_brief,
        semantic_hits=hits,
        graph_hits=[],
        merged_hits=hits,
        agent_analysis=agent_analysis,
    )


def _synthesize_grep_analysis(ranked: list[dict[str, object]], query: str) -> str:
    """Build an agent analysis string from ranked grep hits."""
    parts = [f"Query: {query}", "Agent used ripgrep to search the codebase."]
    parts.append(f"Top {len(ranked)} ranked results:")
    for i, item in enumerate(ranked, 1):
        fname = item.get("FILE", "?")
        lnum = item.get("LINE", "?")
        reason = item.get("RELEVANCE_REASON", "")
        parts.append(f"  {i}. {fname}:{lnum} — {reason}")
    return "\n".join(parts)


# ── Orchestrator ───────────────────────────────────────────────────────


def _agent_grep_search(
    query: str,
    repo_dir: Path,
    repo_brief: str,
    n_results: int = 10,
) -> JudgeInput:
    """Intelligent agent-grep baseline: LLM generates grep commands, then ranks
    output.
    """
    commands = _generate_grep_commands(query, repo_brief)

    logger.info("Running %d grep commands", len(commands))
    grep_output = _run_grep_commands(commands, repo_dir)

    if not grep_output.strip():
        msg = "Agent-grep: no grep matches found for this query"
        raise RuntimeError(msg)

    ranked = _rank_grep_results(query, repo_brief, grep_output)
    if len(ranked) < n_results:
        logger.warning(
            "Agent only returned %d ranked hits (expected %d)", len(ranked), n_results
        )
    ranked = ranked[:n_results]
    analysis = _synthesize_grep_analysis(ranked, query)
    return _from_grep_results(ranked, query, repo_dir, repo_brief, analysis)


# ── Test ───────────────────────────────────────────────────────────────


@pytest.mark.llm_judge
@pytest.mark.skipif(not _JUDGE_KEY, reason="no JUDGE_LLM_API_KEY or OPENROUTER_API_KEY")
def test_judge_hybrid_vs_agent_grep_baseline():
    """Prove hybrid search (vector + graph) beats agent-grep baseline."""
    agent_input = _agent_grep_search(QUERY, CODE_TEST_SMALL, _REPO_BRIEF, n_results=10)
    baseline_output = LLMJudge().judge(agent_input)
    _enforce_semantic_saturation(baseline_output, graph_hit_count=0)

    resp = _search(QUERY, n_results=10, traversal_depth=_TRAVERSAL_DEPTH)
    hybrid_input = from_hybrid_response(
        resp,
        repo_brief=_REPO_BRIEF,
        agent_analysis=f"Hybrid search results for: {QUERY}",
    )
    hybrid_output = LLMJudge().judge(hybrid_input)
    _enforce_semantic_saturation(hybrid_output, graph_hit_count=resp.graph_expanded)

    sep = "=" * 70
    print(f"\n{sep}")
    _print_judge_report(baseline_output, f"AGENT-GREP BASELINE — {QUERY}")
    _print_judge_report(hybrid_output, f"HYBRID SEARCH — {QUERY}")
    print(f"\n{sep}")
    delta = hybrid_output.final_score - baseline_output.final_score
    print(
        f"AGENT-GREP: {baseline_output.final_score:.1f}  |  "
        f"HYBRID: {hybrid_output.final_score:.1f}  |  "
        f"DELTA: {delta:+.1f} points"
    )
    print(sep)

    md = f"## Agent-Grep Baseline — {QUERY}\n\n"
    md += (
        f"**Score:** {baseline_output.final_score:.1f}/100  "
        f"|  **Confidence:** {baseline_output.confidence}  "
        f"|  **Winner:** {baseline_output.winner}\n\n"
    )
    md += "| Subscore | Value |\n| --- | --- |\n"
    for field, val in baseline_output.subscores.model_dump().items():
        display = f"{val}" if val is not None else "N/A"
        md += f"| {field} | {display} |\n"
    md += "\n"

    md += f"## Hybrid Search — {QUERY}\n\n"
    md += (
        f"**Score:** {hybrid_output.final_score:.1f}/100  "
        f"|  **Confidence:** {hybrid_output.confidence}  "
        f"|  **Winner:** {hybrid_output.winner}\n\n"
    )
    md += "| Subscore | Value |\n| --- | --- |\n"
    for field, val in hybrid_output.subscores.model_dump().items():
        display = f"{val}" if val is not None else "N/A"
        md += f"| {field} | {display} |\n"
    md += "\n"

    md += "## Comparison\n\n"
    md += "| Method | Score | Delta |\n| --- | --- | --- |\n"
    md += f"| Agent-Grep | {baseline_output.final_score:.1f} | — |\n"
    md += f"| Hybrid Search | {hybrid_output.final_score:.1f} | {delta:+.1f} |\n\n"
    _write_md_report(md)

    assert hybrid_output.final_score >= baseline_output.final_score, (
        f"Hybrid ({hybrid_output.final_score:.1f}) scored below "
        f"agent-grep ({baseline_output.final_score:.1f})"
    )
    assert baseline_output.final_score >= 40, (
        f"Agent-grep scored {baseline_output.final_score:.1f} < 40"
    )
