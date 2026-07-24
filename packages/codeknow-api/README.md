# CodeKnow API

FastAPI service for the CodeKnow knowledge graph. Builds can update an indexed repository incrementally or force a full rebuild.

## Running in Production Mode

```bash
uv run codeknow-api
```

Options:

| Flag | Default | Env var | Description |
|------|---------|---------|-------------|
| `--host` | `127.0.0.1` | `CODEKNOW_API_HOST` | Bind host |
| `--port` | `8080` | `CODEKNOW_API_PORT` | Bind port |
| `--debug` | off | — | Enable debug mode |

## Running in Debug Mode

```bash
uv run codeknow-api --debug
```

This starts uvicorn with auto-reload and debug-level logging — file changes restart the server automatically.

Alternatively, run uvicorn directly:

```bash
uv run uvicorn codeknow_api.app:create_app --factory --reload --host 127.0.0.1 --port 8080
```

## Build API

`POST /v1/build` starts an asynchronous build and returns `202 Accepted`. Poll `GET /v1/build/{slug}` for progress and the final commit and graph counts.

```json
{
  "github_ssh_url": "git@github.com:owner/repo.git",
  "force_rebuild": false,
  "fetch_remote": true
}
```

The default build updates an existing repository from its tracked remote and reuses unchanged file extractions and embeddings. Set `force_rebuild` to `true` for a full rebuild, or `fetch_remote` to `false` to use the cached checkout.

Builds use complete graph and vector generations. CodeKnow validates a new generation before publishing it, so a failed build does not replace the active index. The API recovers abandoned staging data at startup and periodically while it runs.

## Other endpoints

- `POST /v1/search` searches one or more indexed repositories.
- `GET /v1/repos` lists indexed repositories and build status.
- `DELETE /v1/repos` removes an indexed repository.
- `GET /health` checks API imports and service health.
