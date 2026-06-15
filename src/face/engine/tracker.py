"""Face tracking module using DeepSORT for video sequences."""

import numpy as np
from typing import Dict, List, Optional, Tuple
from deep_sort_realtime.deepsort_tracker import DeepSort

from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class TrackedFace:
    """Represents a tracked face in a video frame."""

    def __init__(
        self,
        track_id: int,
        bbox: np.ndarray,
        confidence: float,
        frame_number: int,
        timestamp: float
    ):
        """
        Initialize tracked face.

        Args:
            track_id: Unique identifier for this person across frames.
            bbox: Bounding box [x1, y1, x2, y2].
            confidence: Detection confidence.
            frame_number: Frame number in video.
            timestamp: Timestamp in seconds.
        """
        self.track_id = track_id
        self.bbox = bbox
        self.confidence = confidence
        self.frame_number = frame_number
        self.timestamp = timestamp

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "track_id": self.track_id,
            "bbox": self.bbox.tolist(),
            "confidence": float(self.confidence),
            "frame_number": self.frame_number,
            "timestamp": float(self.timestamp)
        }


class FaceTracker:
    """Track faces across video frames using DeepSORT."""

    def __init__(
        self,
        max_age: int = 30,
        n_init: int = 1,
        nn_budget: int = 100,
        embedding_model: str = "mobilenet"
    ):
        """
        Initialize face tracker.

        Args:
            max_age: Maximum frames to keep track without detection.
            n_init: Number of consecutive detections to confirm track.
            nn_budget: Maximum number of features to store per class.
            embedding_model: ReID embedding model name.
        """
        logger.info(
            f"Initializing FaceTracker: max_age={max_age}, n_init={n_init}"
        )

        self.max_age = max_age
        self.n_init = n_init
        self.nn_budget = nn_budget
        
        self.tracker = DeepSort(
            max_age=max_age,
            n_init=n_init,
            nms_max_overlap=1.0,
            max_cosine_distance=0.2,
            nn_budget=nn_budget,
            override_track_class=None,
            embedder=None,
            half=True
        )

        self.frame_count = 0
        self.active_tracks: Dict[int, List[TrackedFace]] = {}

        logger.info("FaceTracker initialized")

    def update(
        self,
        detections: List[Tuple[np.ndarray, float]],
        frame_number: int = None,
        timestamp: float = None
    ) -> List[TrackedFace]:
        """
        Update tracker with new detections.

        Args:
            detections: List of (bbox, confidence) tuples.
            frame_number: Current frame number.
            timestamp: Current timestamp in seconds.

        Returns:
            List of TrackedFace objects for current frame.
        """
        try:
            # Format detections for DeepSORT
            # Each detection: ([left, top, width, height], confidence, embedding)
            formatted_detections = []
            for bbox, confidence in detections:
                x1, y1, x2, y2 = bbox
                left, top = x1, y1
                width = x2 - x1
                height = y2 - y1
                formatted_detections.append(([left, top, width, height], confidence, None))

            # Update tracker
            tracks = self.tracker.update_tracks(formatted_detections)

            # Convert to TrackedFace objects
            tracked_faces = []
            current_frame = self.frame_count if frame_number is None else frame_number
            current_time = self.frame_count * 0.333 if timestamp is None else timestamp # Default ~3 FPS per PRD

            for track in tracks:
                if not track.is_confirmed():
                    continue

                track_id = int(track.track_id)
                
                # Get bounding box
                ltrb = track.to_ltrb()
                bbox = np.array([ltrb[0], ltrb[1], ltrb[2], ltrb[3]])

                tracked_face = TrackedFace(
                    track_id=track_id,
                    bbox=bbox,
                    confidence=track.get_confidence(),
                    frame_number=current_frame,
                    timestamp=current_time
                )
                tracked_faces.append(tracked_face)

                # Track history (limit to last 100 frames per track to avoid memory leak)
                if track_id not in self.active_tracks:
                    self.active_tracks[track_id] = []
                self.active_tracks[track_id].append(tracked_face)
                
                if len(self.active_tracks[track_id]) > 100:
                    self.active_tracks[track_id].pop(0)

            self.frame_count += 1

            logger.debug(f"Frame {self.frame_count}: {len(tracked_faces)} active tracks")
            return tracked_faces

        except Exception as e:
            logger.error(f"Tracker update failed: {e}")
            return []

    def get_track_history(self, track_id: int) -> List[TrackedFace]:
        """Get all detections for a specific track ID."""
        return self.active_tracks.get(track_id, [])

    def get_best_frame_for_track(
        self, 
        track_id: int,
        quality_scores: Optional[List[float]] = None
    ) -> Optional[TrackedFace]:
        """
        Get the best frame for a track based on quality or recency.

        Args:
            track_id: Track ID to find best frame for.
            quality_scores: Optional quality scores for EACH frame in history.

        Returns:
            TrackedFace with best quality, or most recent if no scores.
        """
        history = self.get_track_history(track_id)
        
        if not history:
            return None

        if quality_scores is not None and len(quality_scores) > 0:
            # Match scores to history (assuming they are for the same frames)
            # Use min length to avoid IndexError
            n = min(len(quality_scores), len(history))
            best_idx = int(np.argmax(quality_scores[:n]))
            return history[best_idx]
        else:
            # Return most recent frame
            return history[-1]

    def reset(self):
        """Reset tracker state for new video."""
        self.tracker = DeepSort(
            max_age=self.max_age,
            n_init=self.n_init,
            nms_max_overlap=1.0,
            max_cosine_distance=0.2,
            nn_budget=self.nn_budget,
            override_track_class=None,
            embedder=None,
            half=True
        )
        self.frame_count = 0
        self.active_tracks.clear()
        logger.info("FaceTracker reset")

    def get_active_track_ids(self) -> List[int]:
        """Get list of currently active track IDs."""
        return list(self.active_tracks.keys())

    def get_track_count(self) -> int:
        """Get total number of unique tracks."""
        return len(self.active_tracks)
