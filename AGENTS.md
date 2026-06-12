# AGENTS.md

## Tools

- **uv** — workspace-based package manager. Always use `uv run` to execute Python commands.
- **ruff** — linter and formatter. Config: `select = ["ALL"]` with curated ignores (see root `pyproject.toml`). Tests/e2e have relaxed rules.
- **pyrefly** — type checker (`implicit-any` enabled) on production code. Tests and e2e are excluded.
- **pytest** — test runner. Unit tests in `packages/codeknow-lib/tests/`, e2e tests in `e2e/`. Has `llm_judge` marker for LLM-judged tests.

## Scripts

- `codeknow-api` — starts the FastAPI server (`codeknow_api.app:main`). Run via `uv run codeknow-api`.
- `project-scripts.py` — project CLI with subcommands (run via `uv run project-scripts.py <command>`):
  - `dev-check` — runs all static checks in sequence: ruff check (with fix + unsafe-fixes), ruff format, pyrefly.
  - `pipeline` — runs the codeknow pipeline on a GitHub repo. Accepts `--repo-url`, `--input-dir`, `-o/--output-dir`, `-g/--graph-file`, `--chunk-map-file`.
  - `clean` — removes cached repos, graph output, and temp files. Flags: `-y` (skip confirmation).
- `codeknow` — user-facing CLI. Subcommands: `add`, `remove`, `search`, `info`, `daemon`, `clean`.
  - `clean` — stops the daemon, then removes cached repos, graph output, and temp files. Flags: `-y` (skip confirmation).

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
