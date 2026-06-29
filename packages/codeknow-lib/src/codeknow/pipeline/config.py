from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_CODEKNOW_HOME = Path.home() / ".codeknow"

_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

_GITHUB_SSH_RE = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    return Path(raw) if raw else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration for the pipeline run."""

    repo_url: str
    input_dir: Path | None = None
    output_dir: Path | None = None
    graph_filename: str = "graph.json"
    chunk_map_filename: str = "chunk_map.json"
    no_embed: bool = False
    embed_provider: Literal["docker", "ollama", "openrouter"] = "docker"
    embed_model: str = "ai/qwen3-embedding:4B"
    embed_batch_size: int = field(
        default_factory=lambda: _env_int("CODEKNOW_EMBED_BATCH_SIZE", 50)
    )
    chroma_host: str | None = None
    chroma_port: int | None = None
    chroma_collection: str | None = None
    embed_base_url: str | None = None

    @property
    def slug(self) -> str:
        match = _GITHUB_RE.match(self.repo_url)
        if not match:
            match = _GITHUB_SSH_RE.match(self.repo_url)
        if not match:
            return self.repo_url.replace("/", "-").replace(":", "-").replace(".git", "")
        return f"{match.group('owner')}-{match.group('repo')}"

    def resolved_input_dir(self) -> Path:
        return self.input_dir or _env_path(
            "CODEKNOW_INPUT_DIR", _CODEKNOW_HOME / "repos"
        )

    def resolved_output_dir(self) -> Path:
        return self.output_dir or _env_path(
            "CODEKNOW_OUTPUT_DIR", _CODEKNOW_HOME / "graph"
        )
