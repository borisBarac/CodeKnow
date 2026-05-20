# HTTP Client Generator

Generates a typed Python HTTP client from the CodeKnow API OpenAPI spec using [openapi-python-client](https://github.com/openapi-generators/openapi-python-client).

## Prerequisites

```bash
uv sync
```

This installs `openapi-python-client` as part of the dev dependencies.

## Usage

```bash
uv run project-scripts.py gen-client --output-dir /path/to/output
```

The output directory **must already exist** — the script will exit with an error if it doesn't.

To regenerate an existing client in-place, just run the same command again (it passes `--overwrite` automatically).

## What happens under the hood

1. Dumps the OpenAPI schema from `create_app().openapi()` to a temporary file
2. Runs `openapi-python-client generate --path <spec> --output-path <dir> --overwrite`
3. Cleans up the temporary spec file
