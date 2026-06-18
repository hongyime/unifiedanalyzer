"""Pipeline module for face processing."""

from src.face.pipeline.processor import PipelineProcessor, ProcessingResult
from src.face.pipeline.thumbnail import ThumbnailGenerator

__all__ = [
    "PipelineProcessor",
    "ProcessingResult",
    "ThumbnailGenerator",
]