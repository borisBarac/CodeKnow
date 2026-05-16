from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def _env_path(key: str, default: Path) -> Path:
    raw = os.environ.get(key)
    return Path(raw) if raw else default


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration for the pipeline run."""

    repo_url: str
    input_dir: Path | None = None
    output_dir: Path | None = None
    graph_filename: str = "graph.json"
    chunk_map_filename: str = "chunk_map.json"
    no_semantic: bool = False
    no_embed: bool = False
    embed_provider: Literal["ollama", "openrouter"] = "ollama"
    embed_model: str = "qwen3-embedding:4b"
    chroma_host: str | None = None
    chroma_port: int | None = None
    chroma_collection: str | None = None
    embed_base_url: str | None = None

    @property
    def slug(self) -> str:
        match = _GITHUB_RE.match(self.repo_url)
        if not match:
            return self.repo_url.replace("/", "-").replace(".git", "")
        return f"{match.group('owner')}-{match.group('repo')}"

    def resolved_input_dir(self) -> Path:
        return self.input_dir or _env_path(
            "CODEKNOW_INPUT_DIR", Path.cwd() / ".codeknow" / "repos"
        )

    def resolved_output_dir(self) -> Path:
        return self.output_dir or _env_path(
            "CODEKNOW_OUTPUT_DIR", Path.cwd() / "codeknow-out"
        )
