# Setting up CodeKnow for development

## Prerequisites

- **Python 3.10+** (up to 3.13)
- **[uv](https://docs.astral.sh/uv/)** — package and project manager

Verify both are available:

```bash
python --version   # 3.10 or higher
uv --version
```

## From source (development)

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

## Development

```bash
uv run pytest                        # run tests
uv run project-scripts.py dev-check  # ruff + pyrefly
uv run project-scripts.py pipeline   # run the pipeline on a repo
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
