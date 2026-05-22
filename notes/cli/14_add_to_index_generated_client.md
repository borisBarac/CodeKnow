# Plan 14: add_to_index — Generated Client Implementation

## What was done

Refactored `add_to_index()` from raw `httpx.post()` to the generated API client
(`code_know_api_client`). This was the first CLI function to use the generated
client and established the pattern for Plan 15 (search) and Plan 16 (remove).

## Generated client structure

The generated code (from `openapi-python-client`) has no facade class. Each
endpoint is a standalone module under `api/default/` with four functions:

- `sync()` → parsed model or None (no status code access)
- `sync_detailed()` → `Response[T]` with status_code + parsed
- `asyncio()` / `asyncio_detailed()` → async variants

We use `sync_detailed()` because we need the status code to branch between
202 (success), 422 (validation), and 409 (conflict, via UnexpectedStatus).

## Dependency wiring

```toml
# packages/codeknow-cli/pyproject.toml
[project]
dependencies = ["code-know-api-client", ...]

[tool.uv.sources]
code-know-api-client = { path = "./generated" }
```

Path resolves relative to the package directory (not workspace root) in uv
workspace setups.

## Key pattern: ClientError wrapper

`client.py` defines `ClientError(Exception)` so `main.py` never imports
`httpx` or generated-client internals. All API errors are caught and re-raised
as `ClientError`:

- `UnexpectedStatus(409)` → `ClientError("Repo is already being built")`
- `UnexpectedStatus(other)` → `ClientError("Unexpected API status ...")`
- `HTTPValidationError` → `ClientError("Validation error: ...")`

`main.py` error middleware catches only `ClientError`.

## Generated client initialization

```python
self._api_client = GeneratedClient(
    base_url=self.base_url,
    raise_on_unexpected_status=True,
    timeout=httpx.Timeout(300.0),
)
```

`raise_on_unexpected_status=True` is critical — it makes undocumented status
codes (like 409) raise `UnexpectedStatus` instead of silently returning None.

## UNSET sentinel

Generated models use `attrs` (not Pydantic). Optional fields default to `UNSET`,
not `None`. For type narrowing with mypy, use `isinstance(x, Unset)` instead of
`x is UNSET`:

```python
if not isinstance(detail, Unset) and detail:
    msgs = [str(d) for d in detail]
```

## BuildResponse fields

```python
class BuildResponse:
    status: str                    # always present
    slug: None | str | Unset       # optional
    commit_hash: None | str | Unset
    node_count: int | None | Unset
    edge_count: int | None | Unset
    community_count: int | None | Unset
```

`main.py` accesses attributes directly: `result.status`, `result.slug`, etc.
Truthiness works for slug (UNSET/None/"" are all falsy). For numeric fields,
`is not None` check is needed (UNSET is also falsy, so `if result.node_count`
would skip UNSET).

## What stays with raw httpx

`_wait_for_ready()` uses raw `httpx.get()` — it's a daemon health ping, not an
API call. No generated client endpoint for it.

## Files changed

- `packages/codeknow-cli/pyproject.toml` — added generated client dependency
- `packages/codeknow-cli/src/codeknow_cli/client.py` — full refactor
- `packages/codeknow-cli/src/codeknow_cli/main.py` — removed httpx, used attrs
- `packages/codeknow-cli/tests/daemon/test_client.py` — assertions → attrs
