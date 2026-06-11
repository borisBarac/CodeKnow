# Plan: Make `pip install .` work from repo root

## Current state

- Root `pyproject.toml` has **no `[build-system]` or `[project]`** — only tool configs and uv workspace settings
- 3 separate packages live under `packages/*/src/`:
  - `codeknow` (lib) — `packages/codeknow-lib/src/codeknow/`
  - `codeknow_api` (API) — `packages/codeknow-api/src/codeknow_api/`
  - `codeknow_cli` (CLI) — `packages/codeknow-cli/src/codeknow_cli/`
- A generated OpenAPI client lives at `packages/codeknow-cli/generated/code_know_api_client/`
- CLI imports directly from `codeknow.pipeline.config` (the lib) for the `clean` command

## Changes

### 1. Root `pyproject.toml` — add `[build-system]` and `[project]`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "codeknow"
version = "0.1.0"
description = "Knowledge graph pipeline for code"
requires-python = ">=3.10,<3.14"
dependencies = [
    "gitpython",
    "networkx",
    "pydantic>=2.12.5",
    "pydantic-settings",
    "tree-sitter>=0.23.0",
    "tree-sitter-python",
    "tree-sitter-javascript",
    "tree-sitter-typescript",
    "graspologic; python_version < '3.13'",
    "langchain-core",
    "langchain-openai",
    "chromadb",
    "redis[hiredis]>=7.1.0",
    "fastapi[standard]>=0.115",
    "click>=8.2",
    "daemonocle>=1.1",
    "rich>=13.0",
    "httpx>=0.23.0,<0.29.0",
    "attrs>=22.2.0",
    "python-dateutil>=2.8.0",
]

[project.scripts]
codeknow = "codeknow_cli.main:main"
codeknow-api = "codeknow_api.app:main"
```

Notes:
- `uvicorn` omitted because `fastapi[standard]` already pulls it in
- `graspologic; python_version < '3.13'` kept as-is (matches existing lib setup)

### 2. Configure hatchling to find all packages

```toml
[tool.hatch.build.targets.wheel]
packages = [
    "packages/codeknow-lib/src/codeknow",
    "packages/codeknow-api/src/codeknow_api",
    "packages/codeknow-cli/src/codeknow_cli",
    "packages/codeknow-cli/generated/code_know_api_client",
]

[tool.hatch.build.targets.editable]
dev-mode-dirs = [
    "packages/codeknow-lib/src",
    "packages/codeknow-api/src",
    "packages/codeknow-cli/src",
    "packages/codeknow-cli/generated",
]
```

- `wheel` config tells hatchling which directories to package into the wheel
- `editable` config adds each `src/` dir to `sys.path` so `pip install -e .` works in dev mode

### 3. Sub-package `pyproject.toml` files — no changes

The 3 sub-package `pyproject.toml` files stay as-is for `uv` workspace development. They're ignored when doing `pip install .` from root.

### 4. Update `.gitignore`

Add `dist/` and `*.egg-info/` if not already present.

### 5. Verify, then clean up

```bash
pip install .                                                      # regular install
codeknow --help                                                    # CLI works
codeknow-api --help                                                # API server works
python -c "import codeknow; import codeknow_api; import codeknow_cli"  # imports work
pip uninstall codeknow                                             # remove after verification
```

## Files modified

| File | Change |
|---|---|
| `pyproject.toml` (root) | Add `[build-system]`, `[project]`, `[project.scripts]`, hatch build targets |
| `.gitignore` | Add `dist/`, `*.egg-info/` if missing |

## What stays the same

- All source code — zero code changes
- Sub-package `pyproject.toml` files — untouched
- `uv` workspace — still works as before for development
- All tool configs (ruff, mypy, pytest) — untouched
