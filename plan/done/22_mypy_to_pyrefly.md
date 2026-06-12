# Plan: Migrate from mypy to pyrefly

## Goal

Replace mypy with pyrefly as the type checker for `./packages`, preserving equivalent strictness (`disallow_untyped_defs` → `implicit-any`), missing-import handling, and error suppressions.

## Current state

- All mypy config in root `pyproject.toml`: `[tool.mypy]` + 5 `[[tool.mypy.overrides]]`
- `mypy>=2.0.0` in `[dependency-groups] dev`
- Invoked via `project-scripts.py dev-check` as `python -m mypy packages`
- Strict: `disallow_untyped_defs = true` on production code
- Tests/e2e excluded via regex
- 3 modules silenced for missing stubs: `redis`, `daemonocle`, `openapi_python_client`
- 1 module with error code suppressions: `codeknow.cache.redis` (arg-type, assignment, misc)
- AGENTS.md references mypy in 2 places
- No CI/CD pipeline

## Step 1 — Swap dev dependency

In `pyproject.toml` line 46:

```toml
# Before:
"mypy>=2.0.0",
# After:
"pyrefly",
```

Then `uv sync`.

## Step 2 — Replace mypy config with pyrefly config

Remove lines 110–141 (entire `[tool.mypy]` + all `[[tool.mypy.overrides]]`).

Add:

```toml
[tool.pyrefly]
python-version = "3.10"
project-includes = ["packages/**/*.py"]
project-excludes = [
    "packages/*/tests/**",
    "packages/codeknow-cli/generated/**",
]
search-path = [
    "packages/codeknow-lib/src/...",
    "packages/codeknow-api/src/...",
    "packages/codeknow-cli/src/...",
    "packages/codeknow-cli/generated/...",
]
ignore-missing-imports = ["redis", "redis.*", "daemonocle", "daemonocle.*", "openapi_python_client", "openapi_python_client.*"]

[tool.pyrefly.errors]
implicit-any = true

[[tool.pyrefly.sub-config]]
matches = "packages/codeknow-lib/src/codeknow/cache/redis.py"
[tool.pyrefly.sub-config.errors]
bad-argument-type = false
bad-assignment = false
```

### Translation mapping

| mypy | pyrefly | Mechanism |
|---|---|---|
| `disallow_untyped_defs = true` | `errors.implicit-any = true` | Config |
| `exclude` (tests, e2e, generated) | `project-excludes` globs | Config |
| tests/e2e override (`ignore_errors`) | Already excluded via `project-excludes` | N/A |
| `redis` `ignore_missing_imports` | `ignore-missing-imports` list | Config |
| `daemonocle` `ignore_missing_imports` | `ignore-missing-imports` list | Config |
| `openapi_python_client` `ignore_missing_imports` | `ignore-missing-imports` list | Config |
| `codeknow.cache.redis` error suppressions | `[[sub-config]]` with errors table | Sub-config |
| mypy `misc` error code | No direct equivalent — triage and suppress inline if needed | Inline |

## Step 3 — Update `project-scripts.py`

In `dev_check()` function, line 42:

```python
# Before:
("mypy", [sys.executable, "-m", "mypy", "packages"]),

# After:
("pyrefly", [sys.executable, "-m", "pyrefly", "check"]),
```

Also update docstring on line 16 from "mypy" → "pyrefly".

## Step 4 — Run pyrefly and triage errors

```bash
uv run python -m pyrefly check --summarize-errors
```

Expected error categories:
1. Missing stubs → handled by `ignore-missing-imports`
2. redis.py suppressions → handled by `sub-config`
3. Behavioral differences → fix inline or add to `errors` table
4. `misc` equivalent → may need `# pyrefly: ignore` comments

Fix/suppress each category until 0 errors.

## Step 5 — Update AGENTS.md

- Line 7: `mypy` → `pyrefly` in Tools section
- Line 14: `mypy` → `pyrefly` in dev-check description

## Step 6 — Minor cleanups

1. `e2e/judge/test_judge.py:2`: Remove or change `# mypy: disable-error-code="no-untyped-def"` (e2e is excluded from checking anyway)
2. `packages/codeknow-lib/src/codeknow/extract/detect.py:172`: Add `".pyrefly_cache"` to the cache directory list alongside `".mypy_cache"`
3. Remove any `.mypy_cache/` directories (gitignore or manual cleanup)

## Step 7 — Verify

```bash
uv run project-scripts.py dev-check
```

Confirm 0 errors from all 3 steps (ruff check, ruff format, pyrefly check).

## Execution order

1. `pyproject.toml` — swap dep + config (Steps 1–2)
2. `uv sync` — install pyrefly
3. `uv run python -m pyrefly check --summarize-errors` — triage (Step 4)
4. Fix errors (inline suppressions or config tweaks)
5. `project-scripts.py` — update invocation (Step 3)
6. `AGENTS.md` — update references (Step 5)
7. Minor cleanups (Step 6)
8. Full `dev-check` run (Step 7)
