# Plan 14: `add_to_index(ssh_url: str) -> BuildResponse`

[implemented]

## Overview

Use the generated API client (`code_know_api_client`) instead of raw `httpx` calls. This gives us typed request/response models and keeps the CLI decoupled from `httpx`.

## Step 1: Wire up the generated client as a dependency

**`packages/codeknow-cli/pyproject.toml`:**
```toml
[project]
dependencies = [
    "click>=8.2",
    "daemonocle>=1.1",
    "code-know-api-client",
]

[tool.uv.sources]
code-know-api-client = { path = "../generated" }
```

Then run `uv sync`.

## Step 2: Refactor `client.py`

### Imports
- `code_know_api_client.Client` as the generated HTTP client
- `build_v1_build_post` (sync_detailed), `BuildRequest`, `BuildResponse`
- `code_know_api_client.errors.UnexpectedStatus`

### Custom exception
```python
class ClientError(Exception): ...
```
Used so `main.py` never needs to import `httpx` or generated-client internals.

### Generated client instance
Store a generated `Client` initialized with:
- `base_url=self.base_url`
- `raise_on_unexpected_status=True` (so unknown status codes raise `UnexpectedStatus`)
- `timeout=httpx.Timeout(300.0)` (builds take time)

### `add_to_index(ssh_url)` implementation
- Call `build_v1_build_post.sync_detailed(client=self._api_client, body=BuildRequest(github_ssh_url=ssh_url))`
- `status_code == 202` and parsed is `BuildResponse`: return it
- `status_code == 422` and parsed is `HTTPValidationError`: raise `ClientError` with validation details
- `UnexpectedStatus` with code `409`: raise `ClientError("Repo is already being built")`
- Other `UnexpectedStatus`: raise `ClientError` with status code and content
- Return type: `BuildResponse` (the generated model)

### `_wait_for_ready()`
- **No changes.** Stays with raw `httpx.get()` — it's daemon lifecycle, not an API call.

## Step 3: Fix `main.py`

- Remove `import httpx`
- Add `from codeknow_cli.client import ClientError`
- Error middleware in `main()` catches only `ClientError`:
  ```python
  try:
      cli()
  except ClientError as exc:
      click.echo(f"Error: {exc}", err=True)
      sys.exit(1)
  ```
- `add` command accesses `BuildResponse` attributes directly:
  - `result.status`, `result.slug`, `result.node_count`, `result.edge_count`

## Step 4: Fake Server (`fake_server.py`)

- No changes needed. Already handles `POST /v1/build` → 202.

## Step 5: Tests

- Integration tests still work against fake server — same HTTP endpoints
- Adjust assertions: `result["status"]` → `result.status`, `result["slug"]` → `result.slug`
- Add test for `ClientError` on unexpected status codes
