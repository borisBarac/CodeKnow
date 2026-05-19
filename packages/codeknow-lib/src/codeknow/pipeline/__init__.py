"""Pipeline stage definitions and orchestration."""

from .config import PipelineConfig
from .embed_stage import embed
from .io import communities_from_graph, load_graph, load_metadata, save_pipeline_result
from .metadata import build_chunk_metadata
from .runner import run_pipeline
from .stages import resolve
from .types import STAGE_IO, STAGES, EmbedFn, PipelineResult, ResolveFn

__all__ = [
    "STAGES",
    "STAGE_IO",
    "EmbedFn",
    "PipelineConfig",
    "PipelineResult",
    "ResolveFn",
    "build_chunk_metadata",
    "communities_from_graph",
    "embed",
    "load_graph",
    "load_metadata",
    "resolve",
    "run_pipeline",
    "save_pipeline_result",
]
