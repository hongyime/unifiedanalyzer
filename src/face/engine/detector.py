"""Face detection module using InsightFace RetinaFace."""

import os
import threading
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import insightface
from insightface.app import FaceAnalysis

from src.face.utils.logging import get_logger
from src.face.engine.quality import QualityScorer

logger = get_logger(__name__)


class FaceDetectionResult:
    """Result of face detection for a single face."""

    def __init__(
        self,
        bbox: np.ndarray,
        landmark: np.ndarray,
        confidence: float,
        quality_score: float,
        area_ratio: float,
        laplacian_variance: float
    ):
        """
        Initialize face detection result.

        Args:
            bbox: Bounding box [x1, y1, x2, y2].
            landmark: 5 facial landmarks.
            confidence: Detection confidence score.
            quality_score: Combined quality score (0-1).
            area_ratio: Face area ratio to image size.
            laplacian_variance: Laplacian variance for blur detection.
        """
        self.bbox = bbox
        self.landmark = landmark
        self.confidence = confidence
        self.quality_score = quality_score
        self.area_ratio = area_ratio
        self.laplacian_variance = laplacian_variance

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "bbox": self.bbox.tolist(),
            "landmark": self.landmark.tolist(),
            "confidence": float(self.confidence),
            "quality_score": float(self.quality_score),
            "area_ratio": float(self.area_ratio),
            "laplacian_variance": float(self.laplacian_variance)
        }


class FaceDetector:
    """Detect faces in images using InsightFace RetinaFace."""

    # Configuration thresholds. Q3 (face-quality gate, 2026-07): these are now
    # ENV-TUNABLE so the ingest path can tighten what counts as a real face
    # without a code change (the user's "some aren't even faces" complaint).
    # Defaults are the historical hardcoded values so behaviour is unchanged
    # until an operator raises the floor. RetinaFace's det_score is the single
    # most discriminating signal for logo/pattern false-positives — bump
    # FACE_MIN_CONFIDENCE (e.g. 0.60) to drop the low-confidence junk tail while
    # keeping real (even stylised/drawn) faces, which spot-checking showed still
    # score well above the raw 0.50 floor.
    MIN_AREA_RATIO = float(os.getenv("FACE_MIN_AREA_RATIO", "0.05"))  # >=5% of image area
    MIN_LAPLACIAN_VARIANCE = float(os.getenv("FACE_MIN_LAPLACIAN_VARIANCE", "100"))  # sharpness
    MIN_CONFIDENCE = float(os.getenv("FACE_MIN_CONFIDENCE", "0.5"))  # detection confidence
    # Aspect-ratio sanity: a real face crop is roughly square (RetinaFace boxes
    # are near-1:1). A wildly elongated box (banner/logo strip mis-fired as a
    # face) has max(w/h, h/w) far above ~1. 0 disables the check.
    MAX_ASPECT_RATIO = float(os.getenv("FACE_MAX_ASPECT_RATIO", "0") or 0)

    def __init__(self, model_name: str = "buffalo_l", providers: List[str] = None, root: str = None):
        """
        Initialize face detector.

        Args:
            model_name: InsightFace model name (buffalo_l, buffalo_m, etc.).
            providers: ONNX runtime providers (CUDAExecutionProvider, CPUExecutionProvider).
            root: InsightFace model cache root. Defaults to FACE_MODEL_ROOT env
                (Z: drive) so the ~300MB model download stays off the
                space-constrained C: drive. InsightFace stores models under
                "{root}/models".
        """
        if providers is None:
            providers = ['CPUExecutionProvider']
        if root is None:
            root = os.getenv("FACE_MODEL_ROOT", "Z:/unifiedanalyzer/media_derived/faces")

        logger.info(f"Initializing FaceDetector with model: {model_name} (root={root})")

        # Cap ONNXRuntime intra-op threads so InsightFace's CPU sessions don't
        # fan out to ALL cores (the cause of face_worker's 250% CPU spikes). The
        # pip onnxruntime wheel ignores OMP_NUM_THREADS for its intra-op pool and
        # FaceAnalysis doesn't expose session options — so we patch
        # InferenceSession to inject a capped SessionOptions. FACE_ONNX_THREADS
        # sets the ceiling (0/unset = library default). Complements the
        # container's hard `cpus:` cgroup cap; this keeps it from thrashing
        # within that budget.
        _onnx_threads = int(os.getenv("FACE_ONNX_THREADS", "0") or 0)
        if _onnx_threads > 0:
            try:
                import onnxruntime as _ort
                if not getattr(_ort, "_intra_op_capped", False):
                    _orig_session = _ort.InferenceSession

                    def _capped_session(*args, **kwargs):
                        so = kwargs.get("sess_options") or _ort.SessionOptions()
                        so.intra_op_num_threads = _onnx_threads
                        so.inter_op_num_threads = 1
                        kwargs["sess_options"] = so
                        return _orig_session(*args, **kwargs)

                    _ort.InferenceSession = _capped_session
                    _ort._intra_op_capped = True
                    logger.info("Capped ONNXRuntime intra-op threads to %d", _onnx_threads)
            except Exception:
                logger.warning("Could not cap ONNXRuntime threads", exc_info=True)

        self.app = FaceAnalysis(
            name=model_name,
            root=root,
            providers=providers,
            allowed_modules=['detection', 'landmarks', 'recognition']
        )
        self.app.prepare(ctx_id=0, det_size=(640, 640))

        # Serialize calls to self.app.get(image). InsightFace's FaceAnalysis
        # wraps onnxruntime sessions but its Python-side state (det_size,
        # last-frame caches, etc.) is not documented as thread-safe. With
        # INDEX_WORKERS>1 multiple worker threads call detect() concurrently;
        # this lock makes inference serialized. The actual win from multiple
        # workers comes from overlapping NON-inference work (9p file reads,
        # image decode, thumbnail write, DB commit) - those happen OUTSIDE
        # this lock. Expected speedup ~1.3-1.6x over single-worker even with
        # serialized inference.
        self._detect_lock = threading.Lock()

        self.quality_scorer = QualityScorer()
        logger.info("FaceDetector initialized")

    def detect(self, image: np.ndarray, extract_embeddings: bool = True) -> List[FaceDetectionResult]:
        """
        Detect faces in an image with quality filtering.

        Args:
            image: RGB image as numpy array (H, W, 3).
            extract_embeddings: Whether to extract embeddings during detection.

        Returns:
            List of FaceDetectionResult objects passing quality filters.
        """
        try:
            # Get image dimensions
            height, width = image.shape[:2]
            image_area = height * width

            # Run detection. Serialized via _detect_lock so concurrent
            # worker threads can't race inside InsightFace's Python wrapper.
            # See _detect_lock initialization in __init__ for rationale.
            with self._detect_lock:
                faces = self.app.get(image)

            results = []
            for face in faces:
                # Extract bounding box
                bbox = face.bbox.astype(int)
                x1, y1, x2, y2 = bbox
                
                # Calculate area ratio
                face_area = (x2 - x1) * (y2 - y1)
                area_ratio = face_area / image_area

                # Filter by area
                if area_ratio < self.MIN_AREA_RATIO:
                    logger.debug(f"Face filtered: area_ratio {area_ratio:.3f} < {self.MIN_AREA_RATIO}")
                    continue

                # Q3: aspect-ratio sanity — reject wildly non-square boxes
                # (logo strips / banners mis-detected as faces). Only when the
                # operator opts in (MAX_ASPECT_RATIO > 0).
                if self.MAX_ASPECT_RATIO > 0:
                    bw = max(1, x2 - x1)
                    bh = max(1, y2 - y1)
                    aspect = max(bw / bh, bh / bw)
                    if aspect > self.MAX_ASPECT_RATIO:
                        logger.debug(f"Face filtered: aspect {aspect:.2f} > {self.MAX_ASPECT_RATIO}")
                        continue

                # Get confidence
                confidence = face.det_score if hasattr(face, 'det_score') else 1.0
                
                # Filter by confidence
                if confidence < self.MIN_CONFIDENCE:
                    logger.debug(f"Face filtered: confidence {confidence:.3f} < {self.MIN_CONFIDENCE}")
                    continue

                # Extract landmarks
                landmark = face.kps if hasattr(face, 'kps') else np.zeros((5, 2))

                # Compute quality metrics
                face_crop = image[max(0, y1):min(height, y2), 
                                  max(0, x1):min(width, x2)]
                
                if face_crop.size > 0:
                    laplacian_var = self.quality_scorer.compute_laplacian_variance(face_crop)
                    
                    # Filter by sharpness
                    if laplacian_var < self.MIN_LAPLACIAN_VARIANCE:
                        logger.debug(f"Face filtered: laplacian {laplacian_var:.1f} < {self.MIN_LAPLACIAN_VARIANCE}")
                        continue

                    # Compute combined quality score
                    normalized_area = min(area_ratio / 0.2, 1.0)  # Normalize to 0-1, cap at 20% area
                    quality_score = self.quality_scorer.compute_quality_score(
                        laplacian_var, 
                        normalized_area,
                        confidence
                    )
                else:
                    laplacian_var = 0
                    quality_score = 0

                result = FaceDetectionResult(
                    bbox=bbox,
                    landmark=landmark,
                    confidence=confidence,
                    quality_score=quality_score,
                    area_ratio=area_ratio,
                    laplacian_variance=laplacian_var
                )
                
                # Attach embedding if available
                if extract_embeddings and hasattr(face, 'embedding') and face.embedding is not None:
                    # Normalize embedding
                    embedding = face.embedding.astype(np.float32)
                    norm = np.linalg.norm(embedding)
                    if norm > 0:
                        embedding = embedding / norm
                    result.embedding = embedding
                else:
                    result.embedding = None

                results.append(result)

            logger.debug(f"Detected {len(results)} faces passing filters")
            return results

        except Exception as e:
            logger.error(f"Face detection failed: {e}")
            return []

    def detect_raw(self, image: np.ndarray) -> List[Dict]:
        """
        Detect faces without quality filtering (raw output).

        Args:
            image: RGB image as numpy array.

        Returns:
            List of raw face detection dictionaries.
        """
        try:
            faces = self.app.get(image)
            return [self._face_to_dict(f) for f in faces]
        except Exception as e:
            logger.error(f"Raw face detection failed: {e}")
            return []

    def _face_to_dict(self, face) -> Dict:
        """Convert InsightFace face object to dictionary."""
        return {
            "bbox": face.bbox.tolist() if hasattr(face, 'bbox') else [],
            "landmark": face.kps.tolist() if hasattr(face, 'kps') else [],
            "confidence": float(face.det_score) if hasattr(face, 'det_score') else 1.0,
            "embedding": face.embedding.tolist() if hasattr(face, 'embedding') else None
        }

    def set_thresholds(
        self,
        min_area_ratio: float = 0.05,
        min_laplacian_variance: float = 100,
        min_confidence: float = 0.5
    ):
        """Update detection thresholds."""
        self.MIN_AREA_RATIO = min_area_ratio
        self.MIN_LAPLACIAN_VARIANCE = min_laplacian_variance
        self.MIN_CONFIDENCE = min_confidence
        logger.info(
            f"Updated thresholds: area={min_area_ratio}, "
            f"laplacian={min_laplacian_variance}, confidence={min_confidence}"
        )
