"""Dev REPL agent for testing search.

A throwaway-ish interactive agent with a single tool: `search`, which shells
out to the codeknow CLI itself (`python -m codeknow_cli.main search ...`).
Used to exercise the search pipeline end-to-end through a conversational
loop. Config comes from the existing ``JUDGE_LLM_*`` / ``OPENROUTER_API_KEY``
env vars (mirrors evalkit's judge config so no new env vars need to be set).
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import click
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_SYSTEM_PROMPT = """\
You are a dev-testing assistant with ONE tool: `search`. It runs the \
codeknow CLI's hybrid search (vector + graph) over indexed repos and \
returns matching code chunks with file:line citations.

Strategy:
- Call `search` with focused natural-language queries about behavior, \
concepts, or relationships in the codebase.
- Reformulate if the first result misses; stop as soon as you have enough.

Rules:
- Cite real file:line locations that appeared in the tool output.
- Do NOT invent paths, symbols, or line numbers.
- If results do not answer the question, say so plainly.
- Keep the final answer under ~15 lines.
"""

_BANNER = (
    "DEV TESTING ONLY\n\n"
    "This is a throwaway REPL for exercising codeknow search.\n"
    "The agent has access to ONE tool only: `search` (the codeknow CLI). "
    "It cannot run anything else, read files, or call other APIs.\n\n"
    "Commands:  Ctrl-D / Ctrl-C (or blank `exit`) to quit."
)


class ReplLLMConfig(BaseSettings):
    """REPL LLM connection — reads JUDGE_LLM_* / OPENROUTER_* env vars.

    Copy of evalkit's ``JudgeLLMConfig`` so the CLI does not depend on
    evalkit. Same env prefix and fallback, so existing config works.
    """

    model_config = SettingsConfigDict(
        env_prefix="JUDGE_LLM_", env_file=".env", extra="ignore", populate_by_name=True
    )

    model: str = "deepseek-v4-pro"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str | None = None
    temperature: float = 0.0

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        value = os.environ.get("OPENROUTER_API_KEY")
        if value:
            return value
        msg = "Set JUDGE_LLM_API_KEY or OPENROUTER_API_KEY for the REPL LLM."
        raise ValueError(msg)


def _build_chat(cfg: ReplLLMConfig) -> ChatOpenAI:
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=SecretStr(cfg.resolved_api_key()),
        temperature=0.0,
        request_timeout=90,
        max_retries=2,
    )


def _make_search_tool(slugs: tuple[str, ...]) -> Any:
    slug_args: list[str] = []
    for slug in slugs:
        slug_args.extend(["--slug", slug])

    @tool
    def search(query: str) -> str:
        """Search the indexed codebase by natural-language query.

        Returns matching code chunks with file:line citations. Use this to
        find code by concept, behavior, or relationship.
        """
        cmd = [
            sys.executable,
            "-m",
            "codeknow_cli.main",
            "search",
            query,
            "--full",
            *slug_args,
        ]
        proc = subprocess.run(  # noqa: S603 — trusted argv, same package
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return f"[search failed, exit {proc.returncode}]\n{proc.stderr.strip()}"
        return proc.stdout.strip() or "(no output)"

    return search


def run_repl(slugs: tuple[str, ...] = ()) -> None:
    """Run the interactive REPL agent."""
    cfg = ReplLLMConfig()
    try:
        chat = _build_chat(cfg)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc
    search_tool = _make_search_tool(slugs)
    from langchain.agents.middleware import ToolCallLimitMiddleware

    agent = create_agent(
        model=chat,
        tools=[search_tool],
        system_prompt=_SYSTEM_PROMPT,
        middleware=[
            ToolCallLimitMiddleware(run_limit=6, exit_behavior="continue"),
        ],
    )

    from rich.console import Console
    from rich.panel import Panel

    Console().print(
        Panel(
            _BANNER,
            title="codeknow repl",
            title_align="left",
            border_style="yellow",
            style="bold",
            expand=False,
        )
    )
    messages: list[Any] = []
    from langchain_core.callbacks import BaseCallbackHandler

    class _ToolEcho(BaseCallbackHandler):
        def on_tool_start(
            self, serialized: dict[str, Any], input_str: Any, **_: Any
        ) -> None:
            name = serialized.get("name", "tool")
            arg = input_str if isinstance(input_str, str) else str(input_str)
            click.secho(f"  ↳ {name}: {arg[:120]}", fg="cyan")

        def on_tool_end(self, output: Any, **_: Any) -> None:
            text = output if isinstance(output, str) else str(output)
            lines = text.count("\n") + 1
            preview = text.splitlines()[0][:100] if text.strip() else "(empty)"
            click.secho(f"  ↳ ← {lines} lines · {preview}", fg="cyan")

    while True:
        try:
            user_text = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # noqa: T201
            return
        if not user_text or user_text.lower() in {"exit", "quit"}:
            return

        from langchain_core.messages import HumanMessage

        messages.append(HumanMessage(content=user_text))
        try:
            result = agent.invoke(
                {"messages": messages}, config={"callbacks": [_ToolEcho()]}
            )
        except Exception as exc:
            click.secho(f"[agent error] {exc}", err=True, fg="red")
            continue
        messages = result["messages"]
        answer = messages[-1].content if messages else ""
        print(answer)  # noqa: T201
