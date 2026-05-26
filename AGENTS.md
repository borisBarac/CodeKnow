# AGENTS.md

## Tools

- **uv** — workspace-based package manager. Always use `uv run` to execute Python commands.
- **ruff** — linter and formatter. Config: `select = ["ALL"]` with curated ignores (see root `pyproject.toml`). Tests/e2e have relaxed rules.
- **mypy** — strict type checking (`disallow_untyped_defs = true`) on production code. Tests and e2e are excluded.
- **pytest** — test runner. Unit tests in `packages/codeknow-lib/tests/`, e2e tests in `e2e/`. Has `llm_judge` marker for LLM-judged tests.

## Scripts

- `codeknow-api` — starts the FastAPI server (`codeknow_api.app:main`). Run via `uv run codeknow-api`.
- `project-scripts.py` — project CLI with subcommands (run via `uv run project-scripts.py <command>`):
  - `dev-check` — runs all static checks in sequence: ruff check (with fix + unsafe-fixes), ruff format, mypy.
  - `pipeline` — runs the codeknow pipeline on a GitHub repo. Accepts `--repo-url`, `--input-dir`, `-o/--output-dir`, `-g/--graph-file`, `--chunk-map-file`.
  - `clean` — removes cached repos, graph output, and temp files. Flags: `-y` (skip confirmation).
- `codeknow` — user-facing CLI. Subcommands: `add`, `remove`, `search`, `info`, `daemon`, `clean`.
  - `clean` — stops the daemon, then removes cached repos, graph output, and temp files. Flags: `-y` (skip confirmation).
