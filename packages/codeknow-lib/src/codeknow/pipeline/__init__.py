"""Pipeline stage definitions and orchestration."""

from .config import PipelineConfig
from .io import communities_from_graph, load_graph, load_metadata, save_pipeline_result
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
    "communities_from_graph",
    "load_graph",
    "load_metadata",
    "resolve",
    "run_pipeline",
    "save_pipeline_result",
]
