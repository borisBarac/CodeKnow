"""Fastify agentic-search eval.

Compares two LangChain agents over the Fastify codebase:

- **hybrid** agent — uses CodeKnow hybrid search (vector + graph) as its tool.
- **grep** agent — uses ripgrep over the raw source tree as its tool.

Both agents share the same LLM (``JUDGE_LLM_MODEL``, default
deepseek-v4-pro via the configured OpenAI-compatible endpoint) and system
prompt; only the search tool differs. The 10 search items are natural-language
questions about the Fastify source tree (``./fastify-main``). All 20 runs
(10 items x 2 agents) are dispatched in parallel via a thread pool, and each
run is recorded as one JSONL row (``evalkit.schemas.AgentRun``).

After the runs finish, ``evalkit.judge.LLMJudge`` scores every run on
grounding / faithfulness (Stage 1), runs pairwise double-swap preference
(Stage 2), and aggregates a per-tool profile (no headline blend). Outputs:

- ``results/fastify/runs.jsonl`` — raw agent runs.
- ``results/fastify/profile.md`` — per-tool profile, pairwise
  winners, per-task detail (grounding/faithfulness/existence + ungrounded
  claims/hallucinated paths), and the length-bias check.
"""

from __future__ import annotations

# ruff: noqa: T201
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeknow.chunking.chunker import DEFAULT_CHUNK_SIZE
from codeknow.vector.search import GraphSearcher
from evalkit.citations import extract_citations
from evalkit.judge.aggregate import build_profile
from evalkit.judge.judge import LLMJudge
from evalkit.llm import JudgeLLMConfig
from evalkit.schemas import AgentRun, Cost, JudgeOutput, PairwiseJudgment, Task
from fastify_eval_support import (
    CHROMA_COLLECTION,
    EVALS_DIR,
    GRAPH_DIR,
    REPO_DIR,
    _env_flag,
    _load_eval_env,
    _make_store,
    assert_prebuilt_index_ready,
)
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.file_search import FilesystemFileSearchMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────
RESULTS_DIR = EVALS_DIR / "results" / "fastify"
RUNS_JSONL = RESULTS_DIR / "runs.jsonl"
PROFILE_MD = RESULTS_DIR / "profile.md"

HYBRID_TOP_K = 10
MAX_CONCURRENCY = 8
# Tool-call budget: the hard cap on how many times an agent may invoke its
# search tool. Enforced by ToolCallLimitMiddleware (exit_behavior="continue"),
# so the model answers from gathered context on the (N+1)th attempt instead of
# looping until GraphRecursionError. Env-overridable via AGENT_MAX_ITERATIONS.
AGENT_MAX_ITERATIONS = 6


# ── Part 1: the 10 verified search items ───────────────────────────────
SEARCH_ITEMS: list[Task] = [
    Task(
        task_id="fastify-01",
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt=(
            "Fastify answers HEAD requests for GET routes without sending the "
            "full GET body. Where is this HEAD-route behavior implemented, and "
            "how does it compute the Content-Length without serializing the payload?"
        ),
    ),
    Task(
        task_id="fastify-02",
        type="locate",
        stratum="single-hop",
        difficulty="easy",
        prompt=(
            "Where is the default request id generated, and how can it be "
            "customized or read from an incoming header?"
        ),
    ),
    Task(
        task_id="fastify-03",
        type="reasoning",
        stratum="multi-hop",
        difficulty="medium",
        prompt=(
            "How does a request with a JSON body move through content-type parsing, "
            "validation, hooks, and the user handler?"
        ),
    ),
    Task(
        task_id="fastify-04",
        type="reasoning",
        stratum="multi-hop",
        difficulty="medium",
        prompt=(
            "How is the Request object constructed from the raw Node req, and "
            "how does the trustProxy option change which headers are trusted "
            "for protocol, host, and ip detection?"
        ),
    ),
    Task(
        task_id="fastify-05",
        type="reasoning",
        stratum="multi-hop",
        difficulty="hard",
        prompt=(
            "How are route schemas normalized, compiled for validation and "
            "serialization, and attached to a route context?"
        ),
    ),
    Task(
        task_id="fastify-06",
        type="reasoning",
        stratum="multi-hop",
        difficulty="medium",
        prompt="How are errors from hooks or handlers converted into HTTP responses?",
    ),
    Task(
        task_id="fastify-07",
        type="reasoning",
        stratum="multi-hop",
        difficulty="hard",
        prompt=(
            "How does Fastify start listening on localhost, and why can it bind "
            "multiple local addresses?"
        ),
    ),
    Task(
        task_id="fastify-08",
        type="aggregation",
        stratum="multi-hop",
        difficulty="medium",
        prompt=(
            "Where are decorators implemented, and how does Fastify check decorator "
            "dependencies and prevent duplicate decorations?"
        ),
    ),
    Task(
        task_id="fastify-09",
        type="aggregation",
        stratum="multi-hop",
        difficulty="hard",
        prompt=(
            "How does Fastify handle 404 routes, including encapsulated not-found "
            "handlers and route prefixes?"
        ),
    ),
    Task(
        task_id="fastify-10",
        type="trap",
        stratum="single-hop",
        difficulty="medium",
        prompt=(
            "Which file implements Express middleware support in core Fastify? "
            "If core does not implement it, cite the code that says what to do instead."
        ),
    ),
]


def _effective_agent_max_iterations() -> int:
    """Return the tool-call budget for an agent run.

    This is the maximum number of search-tool invocations an agent may make,
    enforced by ``ToolCallLimitMiddleware``. ``AGENT_MAX_ITERATIONS`` env
    overrides the default. ``SMOKE`` affects only item selection
    (``_selected_search_items``), not the budget.
    """
    env_val = os.environ.get("AGENT_MAX_ITERATIONS")
    if env_val:
        try:
            return max(int(env_val), 1)
        except ValueError:
            logger.warning("Invalid AGENT_MAX_ITERATIONS=%r; ignoring", env_val)
    return AGENT_MAX_ITERATIONS


def _effective_eval_seeds() -> int:
    """Number of seeds to run per (item, tool).

    Env-overridable via ``EVAL_SEEDS``. Default ``1`` (deterministic, fast
    smoke). Set ``EVAL_SEEDS=3`` for the real eval so a single stochastic
    swing cannot flip the pairwise verdict — Stage 2 then double-swaps across
    seed pairs and Stage 3 can measure consistency.
    """
    env_val = os.environ.get("EVAL_SEEDS")
    if env_val:
        try:
            return max(int(env_val), 1)
        except ValueError:
            logger.warning("Invalid EVAL_SEEDS=%r; ignoring", env_val)
    return 1


def _selected_search_items() -> list[Task]:
    if _env_flag("SMOKE") or _env_flag("SMOKE_TEST"):
        logger.info("SMOKE/SMOKE_TEST enabled: running first search item only")
        return SEARCH_ITEMS[:1]
    return SEARCH_ITEMS


# ── Part 2a: search tools ──────────────────────────────────────────────
def _format_hybrid_results(response: Any) -> str:
    lines: list[str] = []
    for r in sorted(response.results, key=_hybrid_result_display_key):
        file_path = _display_file_path(r.file)
        span = (
            f"{r.start_line}-{r.end_line}"
            if r.end_line > r.start_line
            else f"{r.start_line}"
        )
        lines.append(f"=== {file_path}:{span} (provenance={r.provenance}) ===")
        lines.append(_numbered_chunk_content(r.start_line, r.content))
        lines.append("")
    return "\n".join(lines) if lines else "(no results)"


def _display_file_path(file_path: str) -> str:
    """Return repo-relative paths so model citations match Stage 0."""
    path = Path(file_path)
    try:
        return str(path.relative_to(REPO_DIR))
    except ValueError:
        return file_path


def _hybrid_result_display_key(result: Any) -> tuple[int, str, int]:
    """Prefer implementation files over tests/docs in the agent-facing output."""
    file_path = _display_file_path(result.file)
    parts = set(Path(file_path).parts)
    group = 1 if "test" in parts or "docs" in parts else 0
    return (group, file_path, result.start_line)


def _numbered_chunk_content(start_line: int, content: str) -> str:
    """Prefix chunk lines with exact line numbers for agent answers."""
    return "\n".join(
        f"{line_no} | {line}"
        for line_no, line in enumerate(content.splitlines(), start=start_line)
    )


def _build_tools() -> dict[str, BaseTool]:
    """Build the two search tools bound to this eval's repo + graph.

    The hybrid searcher is constructed ONCE here (with the Chroma store
    injected) and reused across every call — ``GraphSearcher.__init__``
    reloads ``graph.json`` and rebuilds the reverse index, so per-call
    construction would mean ~60 full graph reloads across the eval.
    """
    store = _make_store()
    logger.info(
        "building tools: graph=%s chroma=%s repo=%s",
        GRAPH_DIR,
        CHROMA_COLLECTION,
        REPO_DIR,
    )
    searcher = GraphSearcher(GRAPH_DIR, collection_name=CHROMA_COLLECTION, store=store)
    file_search = FilesystemFileSearchMiddleware(
        root_path=str(REPO_DIR),
        use_ripgrep=True,
    )
    logger.info("tools built: hybrid (top_k=%d) + grep", HYBRID_TOP_K)

    @tool
    def hybrid_search(query: str) -> str:
        """Search the indexed Fastify repo using hybrid vector + graph search.

        Use this to find code by concept, behavior, or relationship. Returns
        matching code chunks with file:line citations.
        """
        response = searcher.search(query, top_k=HYBRID_TOP_K)
        return _format_hybrid_results(response)

    return {"hybrid": hybrid_search, "grep": file_search.grep_search}


# ── Part 2b: cost-capturing callback ───────────────────────────────────
@dataclass
class RunCost:
    """Mutable cost counters, accumulated by the callback during one run."""

    search_calls: int = 0
    llm_turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tool_names: set[str] = field(default_factory=set)
    tool_outputs: list[str] = field(default_factory=list)


class CostCallback(BaseCallbackHandler):
    """Captures per-run token usage + search-tool invocation count."""

    def __init__(self, search_tool_name: str) -> None:
        self.search_tool_name = search_tool_name
        self.cost = RunCost()

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[Any]], **_: Any
    ) -> None:
        self.cost.llm_turns += 1

    def on_llm_end(self, response: Any, **_: Any) -> None:
        try:
            usage = response.llm_output or {}
            token_usage = usage.get("token_usage") or {}
            self.cost.tokens_in += int(token_usage.get("prompt_tokens", 0))
            self.cost.tokens_out += int(token_usage.get("completion_tokens", 0))
        except (AttributeError, TypeError):
            pass

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, **_: Any
    ) -> None:
        name = serialized.get("name", "")
        self.cost.tool_names.add(name)
        if name == self.search_tool_name:
            self.cost.search_calls += 1

    def on_tool_end(self, output: Any, **_: Any) -> None:
        """Stash each tool's output so an empty final answer can be recovered."""
        text = output if isinstance(output, str) else str(output)
        if text.strip():
            self.cost.tool_outputs.append(text)


# ── Part 2c: agents ────────────────────────────────────────────────────
AGENT_SYSTEM_PROMPT = """\
You are a senior Node.js engineer searching the Fastify codebase to \
answer a question. You have ONE search tool.

Strategy:
- Search up to 6 times, but STOP and answer as soon as you have enough to \
answer with real file:line citations. Two or three good queries usually suffice.
- Reformulate your query if the first result misses the point, but do NOT \
keep searching once you have enough.

Rules:
- Always cite real file:line locations you actually saw in the tool output.
- Cite each location as `path:line` (e.g. `lib/foo.js:42`).
- When your tool returns line numbers, cite the specific line that supports
  each claim. When it returns only a file path or a line range, cite that
  path or range honestly instead of inventing a precise line number.
- Do NOT cite an import line as evidence that behavior is implemented or a
  helper is used; find and cite the call site or implementation line.
- Do NOT infer hook ordering, serialization ordering, or pipeline timing
  unless evidence your tool returned directly shows it.
- Do NOT invent paths, class names, or line numbers.
- If you cannot find the answer, say so plainly — do not fabricate.
- You MUST end with a final answer (<=15 lines) grounded in the cited code.
"""


def _agent_temperature() -> float:
    """Sampling temperature for agent runs.

    With a single seed the agent runs deterministically at the judge config's
    temperature (0.0). With multiple seeds a small nonzero temperature gives
    genuine run-to-run diversity, so the seed axis measures variance instead
    of returning identical answers. The judge itself always uses its own
    config temperature (it never goes through this path).
    """
    if _effective_eval_seeds() > 1:
        return 0.3
    return JudgeLLMConfig().temperature


def _agent_model() -> str:
    """Model for agent runs (env-overridable; defaults to the judge model).

    Set ``EVAL_AGENT_MODEL`` to run agents on a faster/cheaper model while the
    judge keeps its configured (higher-quality) model. base_url/api_key still
    come from ``JudgeLLMConfig``, so the agent model must be reachable on the
    same endpoint as the judge.
    """
    return os.environ.get("EVAL_AGENT_MODEL") or JudgeLLMConfig().model


def _make_chat_model() -> ChatOpenAI:
    """Build the shared ChatOpenAI bound to the judge LLM config."""
    cfg = JudgeLLMConfig()
    return ChatOpenAI(
        model=_agent_model(),
        base_url=cfg.base_url,
        api_key=SecretStr(cfg.resolved_api_key()),
        temperature=_agent_temperature(),
        request_timeout=90,
        max_retries=2,
    )


def make_agent(tool_obj: Any, name: str) -> Any:
    """Build a LangChain agent (create_agent) bound to a single search tool.

    The agent is capped at ``_effective_agent_max_iterations()`` tool calls via
    ``ToolCallLimitMiddleware(exit_behavior="continue")``: on the (N+1)th
    attempt the middleware injects a "limit exceeded, stop calling tools"
    ToolMessage and the model answers from the context it already gathered, so
    ``invoke()`` returns normally instead of raising ``GraphRecursionError``.
    """
    llm = _make_chat_model()
    budget = _effective_agent_max_iterations()
    logger.debug(
        "building agent: tool=%s model=%s temp=%.2f budget=%d",
        getattr(tool_obj, "name", tool_obj),
        _agent_model(),
        _agent_temperature(),
        budget,
    )
    return create_agent(
        model=llm,
        tools=[tool_obj],
        system_prompt=AGENT_SYSTEM_PROMPT,
        middleware=[
            ToolCallLimitMiddleware(
                run_limit=budget,
                exit_behavior="continue",
            )
        ],
    )


def _synthesize_answer(item: Task, tool_outputs: list[str]) -> str:
    """Recover a grounded answer from gathered tool outputs when the agent
    produced no final answer of its own.

    Feeds the accumulated search results to the shared LLM and asks it to
    answer from ONLY those results, citing real file:line locations. This is
    the safety net so an agent run never yields an empty ``final_answer`` —
    the work the agent did is recovered instead of discarded.
    """
    if not tool_outputs:
        return "(no search results retrieved)"
    context = "\n\n".join(tool_outputs)[-8000:]
    prompt = (
        "You are answering a question about the Fastify codebase using ONLY "
        "the search results below. Cite real file:line locations that appear "
        "in the results. If the results do not answer it, say so plainly — "
        "do not fabricate paths or line numbers.\n\n"
        f"Question: {item.prompt}\n\n"
        f"Search results:\n{context}\n\n"
        "Answer (<=15 lines, grounded in the cited code):"
    )
    try:
        resp = _make_chat_model().invoke([HumanMessage(content=prompt)])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return content.strip() or "(synthesis produced no answer)"
    except Exception:
        logger.exception("answer synthesis failed: task=%s", item.task_id)
        return "(failed to synthesize answer)"


# ── Part 2d: parallel runner ───────────────────────────────────────────


def run_one(
    item: Task,
    tool_name: str,
    agent_factory: Callable[[], Any],
    tool_callable: Any,
    seed: int = 0,
) -> AgentRun:
    """Run one agent on one item; capture cost and return an AgentRun.

    The agent self-terminates when it has enough, with a hard tool-call cap
    enforced by the middleware. If for any reason it ends with a blank final
    answer (transient error, empty model response), the gathered tool outputs
    are fed back through ``_synthesize_answer`` so the run always yields a
    non-empty, grounded ``final_answer``.
    """
    callback = CostCallback(search_tool_name=tool_callable.name)
    agent = agent_factory()
    budget = _effective_agent_max_iterations()
    logger.info(
        "start task=%s tool=%s seed=%d budget=%d", item.task_id, tool_name, seed, budget
    )
    start = time.perf_counter()
    result: Any = None
    error: str | None = None
    try:
        result = agent.invoke(
            {
                "messages": [
                    SystemMessage(content=AGENT_SYSTEM_PROMPT),
                    HumanMessage(content=item.prompt),
                ]
            },
            config={
                "callbacks": [callback],
                # Backstop only; the middleware caps tool *calls* at `budget`
                # via run_limit. Faster/less-disciplined models (e.g. flash)
                # sometimes keep re-attempting a tool call after the limit
                # message, so the backstop must leave them ample supersteps to
                # settle into a final answer without tripping GraphRecursionError.
                # Tool usage stays bounded by run_limit regardless of this value.
                "recursion_limit": budget * 8 + 8,
            },
        )
    except Exception as exc:
        logger.exception("agent run failed: task=%s tool=%s", item.task_id, tool_name)
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - start

    if error:
        logger.error("run error task=%s tool=%s: %s", item.task_id, tool_name, error)

    final_answer = _extract_final_answer(result) if result is not None else ""
    if not final_answer.strip():
        if callback.cost.tool_outputs:
            logger.info(
                "empty answer; synthesizing from %d tool outputs: task=%s tool=%s",
                len(callback.cost.tool_outputs),
                item.task_id,
                tool_name,
            )
            final_answer = _synthesize_answer(item, callback.cost.tool_outputs)
        else:
            final_answer = "(no answer produced and no search results retrieved)"
    else:
        logger.debug(
            "answer extracted: task=%s tool=%s len=%d citations=%d",
            item.task_id,
            tool_name,
            len(final_answer),
            len(extract_citations(final_answer)),
        )

    return AgentRun(
        task_id=item.task_id,
        tool=tool_name,  # type: ignore[arg-type]
        seed=seed,
        final_answer=final_answer,
        cited_locations=extract_citations(final_answer),
        cost=Cost(
            search_calls=callback.cost.search_calls,
            llm_turns=callback.cost.llm_turns,
            tokens_in=callback.cost.tokens_in,
            tokens_out=callback.cost.tokens_out,
            wall_clock_s=round(elapsed, 2),
        ),
    )


def _extract_final_answer(result: Any) -> str:
    """Pull the agent's final assistant message text from the langgraph output."""
    messages = (
        result.get("messages", [])
        if isinstance(result, dict)
        else getattr(result, "messages", [])
    )
    for msg in reversed(messages):
        content = getattr(msg, "content", "")
        msg_type = getattr(msg, "type", "") or type(msg).__name__
        if (
            msg_type in {"ai", "AIMessage", "assistant"}
            and isinstance(content, str)
            and content.strip()
        ):
            return content
    return ""


def run_all(items: list[Task]) -> list[AgentRun]:
    """Run both agents on all items in parallel; stream JSONL rows as they finish.

    Each (item, tool) is run once per seed (``_effective_eval_seeds``); the
    judge uses the seed axis for consistency (Stage 3) and double-swap
    pairwise (Stage 2).
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tools = _build_tools()
    runs: list[AgentRun] = []
    jobs: list[tuple[Task, str, int, Callable[[], Any], Any]] = []

    def _factory(tn: str) -> Callable[[], Any]:
        return lambda: make_agent(tools[tn], tn)

    seeds = _effective_eval_seeds()
    for item in items:
        for tool_name, tool_obj in tools.items():
            factory = _factory(tool_name)
            for seed in range(seeds):
                jobs.append((item, tool_name, seed, factory, tool_obj))

    logger.info(
        "dispatching %d jobs: items=%d tools=%d seeds=%d concurrency=%d -> %s",
        len(jobs),
        len(items),
        len(tools),
        seeds,
        MAX_CONCURRENCY,
        RUNS_JSONL,
    )

    with (
        ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool,
        RUNS_JSONL.open("w", encoding="utf-8") as fh,
    ):
        futures = {
            pool.submit(run_one, item, tn, factory, tool_obj, seed): (item, tn)
            for item, tn, seed, factory, tool_obj in jobs
        }
        total = len(jobs)
        for i, future in enumerate(as_completed(futures), 1):
            item, tn = futures[future]
            try:
                run = future.result()
            except Exception:
                logger.exception("job crashed: task=%s tool=%s", item.task_id, tn)
                run = AgentRun(
                    task_id=item.task_id,
                    tool=tn,  # type: ignore[arg-type]
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
            runs.append(run)
            fh.write(run.model_dump_json() + "\n")
            fh.flush()
            logger.info(
                "[%d/%d] done task=%s tool=%s tokens=%d calls=%d %.1fs",
                i,
                total,
                run.task_id,
                run.tool,
                run.cost.tokens_in + run.cost.tokens_out,
                run.cost.search_calls,
                run.cost.wall_clock_s,
            )
    return runs


# ── Summary table ──────────────────────────────────────────────────────
def print_summary(runs: list[AgentRun]) -> None:
    """Print a per-tool aggregate cost table."""
    by_tool: dict[str, list[AgentRun]] = {"hybrid": [], "grep": []}
    for r in runs:
        by_tool.setdefault(r.tool, []).append(r)

    print("\n" + "=" * 72, file=sys.stderr)
    print(
        f"{'tool':<8} | {'runs':>4} | {'avg_tok_in':>10} | {'avg_tok_out':>11} | "
        f"{'avg_calls':>9} | {'avg_wall_s':>10}",
        file=sys.stderr,
    )
    print("-" * 72, file=sys.stderr)
    for tool_name, group in by_tool.items():
        if not group:
            continue
        n = len(group)
        avg_in = sum(r.cost.tokens_in for r in group) / n
        avg_out = sum(r.cost.tokens_out for r in group) / n
        avg_calls = sum(r.cost.search_calls for r in group) / n
        avg_wall = sum(r.cost.wall_clock_s for r in group) / n
        print(
            f"{tool_name:<8} | {n:>4} | {avg_in:>10.0f} | {avg_out:>11.0f} | "
            f"{avg_calls:>9.1f} | {avg_wall:>10.1f}",
            file=sys.stderr,
        )
    print("=" * 72, file=sys.stderr)
    print(f"runs written to: {RUNS_JSONL}", file=sys.stderr)


# ── Judge + report ─────────────────────────────────────────────────────
def _fmt_ci(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return "n too small"
    lo, hi = ci
    return f"[{lo:.2f}, {hi:.2f}]"


def _fmt_opt(value: float | None, suffix: str = "") -> str:
    return f"{value:.1f}{suffix}" if value is not None else "N/A"


def write_report(
    items: list[Task],
    outputs: list[JudgeOutput],
    pairwise: list[PairwiseJudgment],
    profile: dict[str, Any],
) -> None:
    """Write the per-tool profile + pairwise + per-task detail to PROFILE_MD."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out_by_key = {(o.task_id, o.tool): o for o in outputs}
    pw_by_task = {p.task_id: p for p in pairwise}

    md: list[str] = []
    md.append("# Fastify: hybrid vs grep agent eval\n\n")
    md.append(f"Generated: {stamp}  \n")
    md.append(f"Repo: `{REPO_DIR}`  \n")
    judge_line = (
        f"Judge: `{JudgeLLMConfig().model}` | seeds: {_effective_eval_seeds()} | "
        f"items: {len(items)}\n\n"
    )
    md.append(judge_line)

    # ── Per-tool profile ──
    md.append("## Per-tool profile\n\n")
    md.append(
        "| tool | grounding /5 | faithfulness /5 | consistency | "
        "preference win-rate | Wilson 95% CI | median tokens | "
        "median search calls | median wall (s) |\n"
    )
    md.append("|---|---|---|---|---|---|---|---|---|\n")
    for tool_name in ("hybrid", "grep"):
        if tool_name not in profile:
            continue
        p = profile[tool_name]
        cost = p["cost"]
        md.append(
            f"| {tool_name} | {_fmt_opt(p['grounding_mean'])} "
            f"| {_fmt_opt(p['faithfulness_mean'])} "
            f"| {_fmt_opt(p['consistency_pct'], '%')} "
            f"| {_fmt_opt(p['preference_win_rate_pct'], '%')} "
            f"| {_fmt_ci(p['preference_win_rate_ci'])} "  # type: ignore[arg-type]
            f"| {cost['median_tokens']:.0f} | {cost['median_search_calls']:.1f} "
            f"| {cost['median_wall_clock_s']:.1f} |\n"
        )
    md.append("\n")

    # ── Pairwise winners ──
    md.append("## Pairwise winners (Stage 2, double-swap)\n\n")
    md.append("| task | winner | confidence |\n|---|---|---|\n")
    for item in items:
        pw = pw_by_task.get(item.task_id)
        if pw is None:
            md.append(f"| {item.task_id} | (none) | — |\n")
        else:
            md.append(f"| {item.task_id} | {pw.winner} | {pw.confidence} |\n")
    md.append("\n")

    # ── Per-task detail ──
    md.append("## Per-task detail\n\n")
    for item in items:
        trap = " *(trap)*" if item.trap else ""
        md.append(f"### {item.task_id}{trap} — {item.prompt}\n\n")
        pw = pw_by_task.get(item.task_id)
        if pw is not None:
            md.append(f"**Pairwise winner:** {pw.winner} ({pw.confidence})\n\n")
        for tool_name in ("hybrid", "grep"):
            o = out_by_key.get((item.task_id, tool_name))
            if o is None:
                md.append(f"- **{tool_name}:** (no run)\n")
                continue
            existence = (
                f"{o.existence_rate:.0%}" if o.existence_rate is not None else "N/A"
            )
            md.append(
                f"- **{tool_name}:** grounding {o.grounding}/5, "
                f"faithfulness {o.faithfulness}/5, "
                f"existence {existence}\n"
            )
            if o.ungrounded_claims:
                md.append("  - ungrounded claims:\n")
                for c in o.ungrounded_claims:
                    md.append(f"    - {c}\n")
            if o.hallucinated_paths:
                md.append("  - hallucinated paths:\n")
                for path in o.hallucinated_paths:
                    md.append(f"    - {path}\n")
            if o.unsupported_ranges:
                md.append("  - unsupported ranges (cited, exist; range not shown):\n")
                for path in o.unsupported_ranges:
                    md.append(f"    - {path}\n")
        md.append("\n")

    # ── Bias + stats ──
    bias = profile.get("bias_check", {})
    corr = bias.get("length_winrate_correlation")
    corr_str = "n/a" if corr is None else f"{corr:.2f}"
    md.append("## Bias & significance\n\n")
    md.append(
        f"- length↔win correlation: {corr_str} "
        f"(flagged: {bias.get('bias_flagged', False)})\n"
    )
    bias_note = bias.get("note")
    if bias_note:
        md.append(f"- bias note: {bias_note}\n")
    stats = profile.get("stats", {})

    def _fmt_p(key: str) -> str:
        val = stats.get(key)
        if val is None:
            return "n/a"
        marker = " *" if val < 0.05 else ""
        return f"{val:.4f}{marker}"

    md.append(f"- McNemar preference p: {_fmt_p('mcnemar_preference_p')}\n")
    md.append(f"- Wilcoxon grounding p: {_fmt_p('wilcoxon_grounding_p')}\n")
    md.append(f"- Wilcoxon faithfulness p: {_fmt_p('wilcoxon_faithfulness_p')}\n")
    md.append("  (* p < 0.05)\n\n")

    PROFILE_MD.write_text("".join(md), encoding="utf-8")
    logger.info("profile written to %s", PROFILE_MD)
    print(f"profile written to: {PROFILE_MD}", file=sys.stderr)


def judge_and_report(items: list[Task], runs: list[AgentRun]) -> None:
    """Run the 3-stage judge over the captured runs and write the profile."""
    if not runs:
        logger.warning("no runs to judge; skipping judge + report")
        return
    # Align citation snippets with the indexed chunks agents reason over.
    logger.info(
        "judge stage starting: runs=%d items=%d judge=%s repo=%s",
        len(runs),
        len(items),
        JudgeLLMConfig().model,
        REPO_DIR,
    )
    judge_start = time.perf_counter()
    judge = LLMJudge(repo_root=REPO_DIR, snippet_context=DEFAULT_CHUNK_SIZE // 2)
    outputs, pairwise = judge.judge_all(items, runs)
    profile = build_profile(outputs, pairwise, runs)
    logger.info(
        "judge stage done: outputs=%d pairwise=%d %.1fs",
        len(outputs),
        len(pairwise),
        time.perf_counter() - judge_start,
    )
    write_report(items, outputs, pairwise, profile)


# ── Logging setup ──────────────────────────────────────────────────────
def _setup_logging() -> None:
    """Configure logging: unbuffered stderr + mirror to ``results/fastify/eval.log``.

    ``sys.stderr.reconfigure(line_buffering=True)`` forces a flush after every
    newline so logs appear in real-time even when piped through ``uv run`` or
    a terminal multiplexer (fixes the "no logs in terminal" bug where stderr
    was block-buffered until process exit).

    The ``httpx`` logger is silenced to ``WARNING`` — its per-request INFO
    lines (Chroma, embeddings, LLM API) drowned ~70% of the real eval signal.
    """
    sys.stderr.reconfigure(line_buffering=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(RESULTS_DIR / "eval.log")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)


def _log_banner(title: str) -> None:
    """Log a visual phase separator."""
    logger.info("=" * 60)
    logger.info("  %s", title)
    logger.info("=" * 60)


# ── main ───────────────────────────────────────────────────────────────
def main() -> None:
    _load_eval_env()
    _setup_logging()
    t0 = time.perf_counter()
    assert_prebuilt_index_ready()
    items = _selected_search_items()
    logger.info(
        "eval config: items=%d seeds=%d budget=%d model=%s temp=%.2f repo=%s",
        len(items),
        _effective_eval_seeds(),
        _effective_agent_max_iterations(),
        _agent_model(),
        _agent_temperature(),
        REPO_DIR,
    )
    _log_banner(f"phase 1/2: agent runs ({len(items)} items)")
    runs = run_all(items)
    print_summary(runs)
    _log_banner(f"phase 2/2: judge ({len(runs)} runs)")
    try:
        judge_and_report(items, runs)
    except Exception:
        logger.exception("judge + report failed (runs JSONL already saved)")

    elapsed = time.perf_counter() - t0
    mins, secs = divmod(int(elapsed), 60)
    logger.info(
        "eval complete: %dm %02ds | runs: %d | log: %s | profile: %s",
        mins,
        secs,
        len(runs),
        RESULTS_DIR / "eval.log",
        PROFILE_MD,
    )


if __name__ == "__main__":
    main()
