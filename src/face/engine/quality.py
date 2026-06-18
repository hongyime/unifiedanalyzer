"""Face quality scoring module."""

import numpy as np
from typing import Optional
import cv2

from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class QualityScorer:
    """Compute quality scores for face images."""

    def __init__(
        self,
        laplacian_weight: float = 0.4,
        area_weight: float = 0.3,
        confidence_weight: float = 0.3
    ):
        """
        Initialize quality scorer.

        Args:
            laplacian_weight: Weight for sharpness score.
            area_weight: Weight for size score.
            confidence_weight: Weight for detection confidence.
        """
        self.laplacian_weight = laplacian_weight
        self.area_weight = area_weight
        self.confidence_weight = confidence_weight

    def compute_laplacian_variance(self, image: np.ndarray) -> float:
        """
        Compute Laplacian variance as a measure of image sharpness.

        Higher values indicate sharper images (less blur).
        Typical thresholds: >100 for acceptable, >200 for good.

        Args:
            image: RGB or grayscale image.

        Returns:
            Laplacian variance value.
        """
        try:
            # Convert to grayscale if needed
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            else:
                gray = image

            # Ensure proper dtype
            if gray.dtype != np.uint8:
                gray = (gray * 255).astype(np.uint8)

            # Compute Laplacian variance
            laplacian = cv2.Laplacian(gray, cv2.CV_64F)
            variance = laplacian.var()

            return float(variance)

        except Exception as e:
            logger.error(f"Laplacian variance computation failed: {e}")
            return 0.0

    def compute_normalized_area(
        self, 
        face_bbox: np.ndarray, 
        image_shape: tuple
    ) -> float:
        """
        Compute normalized face area relative to image size.

        Args:
            face_bbox: Bounding box [x1, y1, x2, y2].
            image_shape: Image shape (height, width) or (height, width, channels).

        Returns:
            Normalized area ratio (0-1), capped at 0.2 (20% of image).
        """
        try:
            height, width = image_shape[:2]
            image_area = height * width

            x1, y1, x2, y2 = face_bbox.astype(int)
            face_area = (x2 - x1) * (y2 - y1)

            area_ratio = face_area / image_area
            
            # Normalize to 0-1 range, cap at 20% area
            normalized = min(area_ratio / 0.2, 1.0)

            return normalized

        except Exception as e:
            logger.error(f"Normalized area computation failed: {e}")
            return 0.0

    def compute_quality_score(
        self,
        laplacian_variance: float,
        normalized_area: float,
        confidence: float,
        max_laplacian: float = 500.0
    ) -> float:
        """
        Compute combined quality score from multiple metrics.

        Args:
            laplacian_variance: Laplacian variance (sharpness).
            normalized_area: Normalized face area (0-1).
            confidence: Detection confidence (0-1).
            max_laplacian: Maximum expected laplacian variance for normalization.

        Returns:
            Combined quality score (0-1).
        """
        try:
            # Normalize laplacian variance to 0-1
            laplacian_score = min(laplacian_variance / max_laplacian, 1.0)

            # Area is already normalized
            area_score = normalized_area

            # Confidence is already 0-1
            confidence_score = confidence

            # Weighted combination
            quality = (
                self.laplacian_weight * laplacian_score +
                self.area_weight * area_score +
                self.confidence_weight * confidence_score
            )

            return min(max(quality, 0.0), 1.0)

        except Exception as e:
            logger.error(f"Quality score computation failed: {e}")
            return 0.0

    def score_face(
        self,
        image: np.ndarray,
        face_bbox: np.ndarray,
        confidence: float
    ) -> dict:
        """
        Compute all quality metrics for a face.

        Args:
            image: Full image containing the face.
            face_bbox: Bounding box [x1, y1, x2, y2].
            confidence: Detection confidence.

        Returns:
            Dictionary with all quality metrics.
        """
        try:
            # Extract face crop
            x1, y1, x2, y2 = face_bbox.astype(int)
            face_crop = image[y1:y2, x1:x2]

            if face_crop.size == 0:
                return {
                    "laplacian_variance": 0.0,
                    "normalized_area": 0.0,
                    "confidence": confidence,
                    "quality_score": 0.0
                }

            # Compute metrics
            laplacian_var = self.compute_laplacian_variance(face_crop)
            norm_area = self.compute_normalized_area(face_bbox, image.shape)
            quality = self.compute_quality_score(laplacian_var, norm_area, confidence)

            return {
                "laplacian_variance": laplacian_var,
                "normalized_area": norm_area,
                "confidence": confidence,
                "quality_score": quality
            }

        except Exception as e:
            logger.error(f"Face scoring failed: {e}")
            return {
                "laplacian_variance": 0.0,
                "normalized_area": 0.0,
                "confidence": confidence,
                "quality_score": 0.0
            }

    def is_acceptable(
        self,
        quality_score: float,
        min_score: float = 0.3,
        min_laplacian: float = 100.0
    ) -> bool:
        """
        Check if face quality is acceptable for processing.

        Args:
            quality_score: Combined quality score.
            min_score: Minimum acceptable quality score.
            min_laplacian: Minimum acceptable laplacian variance.

        Returns:
            True if face passes quality threshold.
        """
        return quality_score >= min_score
