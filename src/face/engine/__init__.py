"""Engine module for face detection and embedding.

Collector-media-only scope (merge D3): the DeepSORT video tracker
(engine/tracker.py) is intentionally NOT imported/used — its deep-sort-realtime
(torch) dependency is omitted. Video faces come from the analyzer's
ffmpeg-extracted frames, not in-engine tracking.
"""

from src.face.engine.detector import FaceDetector, FaceDetectionResult
from src.face.engine.quality import QualityScorer

__all__ = [
    "FaceDetector",
    "FaceDetectionResult",
    "QualityScorer",
]