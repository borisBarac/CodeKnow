# syntax=docker/dockerfile:1.7
#
# Container image for the codeknow-api FastAPI service.
#
# Build from the repository root (it is a uv workspace, so the whole
# workspace must be available as the build context):
#
#   docker build -f packages/codeknow-api/Dockerfile -t codeknow-api .
#
# Or, with docker compose:
#
#   docker compose -f infra/docker-compose.yml up --build
#
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Python 3.12 is required: codeknow-lib depends on graspologic, which only
# supports Python < 3.13. bookworm-slim ships binary wheels for the heavy
# scientific/network deps (scipy, chromadb, langchain, networkx).

# Pin the project's venv location and make uv self-contained for the image.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# git is required at runtime: the pipeline clones repositories via gitpython.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# --- dependency layer (cached independently of source) ---------------------
# Only the root manifest and lockfile are needed: --no-install-workspace tells
# uv to install the third-party dependency closure of codeknow-api but skip the
# workspace members (codeknow-api/lib) themselves. --frozen avoids needing the
# per-member manifests to re-validate the lock (uv's documented workspace Docker
# pattern), so no package-level pyproject.toml is copied here.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package codeknow-api --no-install-workspace

# --- source layer -----------------------------------------------------------
COPY packages/ packages/

# Now build and install the local workspace packages (codeknow-api +
# codeknow-lib). Fast, since every third-party wheel is already in the venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package codeknow-api

# Runtime data directories for generated graphs and cloned-repo temp space.
# In docker compose these are backed by a named/host volume mounted at /data.
ENV CODEKNOW_API_HOST=0.0.0.0 \
    CODEKNOW_API_PORT=8080 \
    CODEKNOW_GRAPH_DIR=/data/graph \
    CODEKNOW_TEMP_DIR=/data/temp

RUN mkdir -p "${CODEKNOW_GRAPH_DIR}" "${CODEKNOW_TEMP_DIR}"

EXPOSE 8080

# The console script `codeknow-api` is installed into /app/.venv/bin by the
# [project.scripts] entry in packages/codeknow-api/pyproject.toml. Its
# argparse defaults read CODEKNOW_API_HOST / CODEKNOW_API_PORT, so the host /
# port remain overridable at runtime via environment variables.
CMD ["codeknow-api"]
