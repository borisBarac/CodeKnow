"""Backward-compatible re-export shim.

The implementation has been split into:
    config.py, types.py, stages.py, runner.py, io.py
"""

from .config import PipelineConfig
from .io import save_pipeline_result
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
    "resolve",
    "run_pipeline",
    "save_pipeline_result",
]
