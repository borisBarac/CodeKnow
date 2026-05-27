# CodeKnow API

FastAPI service for the CodeKnow knowledge graph.

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
