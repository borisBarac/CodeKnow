# Installing CodeKnow

## Prerequisites

- **Python 3.10+** (up to 3.13)
- **[uv](https://docs.astral.sh/uv/)** — package and project manager

Verify both are available:

```bash
python --version   # 3.10 or higher
uv --version
```

## Install from source (development)

```bash
git clone <repo-url> CodeKnow
cd CodeKnow
uv sync
```

Run commands via `uv run`:

```bash
uv run codeknow --help
uv run codeknow-api --help
```

## Install as a global tool

This puts two standalone executables on your `PATH`:

```bash
cd CodeKnow
uv tool install .
```

- **`codeknow`** — the CLI
- **`codeknow-api`** — the API server

Uninstall:

```bash
uv tool uninstall codeknow
```

## Quick start

```bash
# 1. Start the background daemon
codeknow daemon start

# 2. Index a repo
codeknow add git@github.com:owner/repo.git

# 3. Search
codeknow search "how does auth work"

# 4. Stop the daemon
codeknow daemon stop
```

## Troubleshooting

### `uv: command not found`

Install uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your shell, then verify with `uv --version`.

### `python` not found or wrong version

On macOS:

```bash
brew install python@3.12
```

uv will automatically detect the right Python version from `pyproject.toml`. You can also set it explicitly:

```bash
uv python install 3.12
uv python pin 3.12
```

### Hatchling build errors on `uv tool install`

Make sure the working tree is clean (no uncommitted changes in `packages/`):

```bash
git status
git stash   # if needed
uv tool install .
git stash pop
```
