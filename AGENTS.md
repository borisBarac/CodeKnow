# AGENTS.md

## Tools

- **uv** — workspace-based package manager. Always use `uv run` to execute Python commands.
- **ruff** — linter and formatter. Config: `select = ["ALL"]` with curated ignores (see `ruff.toml`). Tests/e2e have relaxed rules.
- **pyrefly** — type checker (`implicit-any` enabled) on production code. Tests and e2e are excluded.
- **pytest** — test runner. Unit tests in `packages/codeknow-lib/tests/`, e2e tests in `e2e/`. Has `llm_judge` marker for LLM-judged tests.

## Scripts

- `codeknow-api` — starts the FastAPI server (`codeknow_api.app:main`). Run via `uv run codeknow-api`.
- `project-scripts.py` — project CLI with subcommands (run via `uv run project-scripts.py <command>`):
  - `dev-check` — runs all static checks in sequence: ruff check (with fix + unsafe-fixes), ruff format, pyrefly.
  - `pipeline` — runs the codeknow pipeline on a GitHub repo. Accepts `--repo-url`, `--input-dir`, `-o/--output-dir`, `-g/--graph-file`, `--chunk-map-file`.
  - `clean` — removes cached repos, graph output, and temp files. Flags: `-y` (skip confirmation).
  - `gen-client` — generate a Python HTTP client from the API OpenAPI spec (requires `--output-dir`).
- `codeknow` — user-facing CLI. Subcommands: `add`, `remove`, `search`, `info`, `clean`, and the `server` group (`mode`, `start`, `stop`, `status`). The CLI is config-file driven — it reads its `mode` (`docker` | `remote` | `daemon`) from `~/.codeknow/config.jsonl`, not from environment variables.
  - `clean` — in `daemon` mode, stops the server first; then removes cached repos, graph output, and temp files. Flags: `-y` (skip confirmation).
