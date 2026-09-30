"""GAN / synthetic-face detector (Do Next #4).

Offline binary classifier: is this avatar likely GAN-generated?

Model choice: CNNDetection (Wang et al. CVPR 2020) exported to ONNX.
BSD-licensed, ~200 MB, ~200 ms/face on CPU. Strong across StyleGAN2 which
is what Griffin's LinkedIn-fakes network (2021-11-16) actually used.

**Enable path**:
  1. Run scripts/download_gan_model.py once. Writes ~200 MB to
     ${FACE_MODEL_ROOT}/gan/cnndetection_stylegan2.onnx, verifies SHA-256.
  2. Set GAN_DETECTOR_ENABLED=1 in analyzer env.
  3. Pipeline picks it up on the next incremental cycle.

Default DISABLED. Fails cleanly (returns None + logs) when the model file
is missing so the pipeline can ship without the model artifact.

Ref: Z:\\...\\research\\spec-do-next-4-gan-detector.md
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

FACE_MODEL_ROOT_DEFAULT = "Z:/unifiedanalyzer/media_derived/faces"
GAN_MODEL_FILENAME_DEFAULT = "cnndetection_stylegan2.onnx"
GAN_MODEL_VERSION = "cnndetection_stylegan2_v1"


class GanDetectorUnavailable(RuntimeError):
    """Raised when the ONNX model file / runtime isn't available."""


class GanDetector:
    """Load-once, thread-safe wrapper around the CNNDetection ONNX model.

    Model input: 224x224 RGB, normalized to [-1, +1] (matches Wang et al.
    preprocessing; no ImageNet mean/std). Output: single sigmoid logit,
    where 1.0 = synthetic, 0.0 = real.

    On any load failure the score() method returns None so callers can
    skip cleanly. We NEVER auto-download a fallback model - operator
    runs scripts/download_gan_model.py once, or the detector stays off.
    """

    _session: object = None            # onnxruntime.InferenceSession; typed loosely
    _init_lock = threading.Lock()
    _load_error: Optional[str] = None

    def __init__(self, model_root: str | None = None) -> None:
        root = Path(model_root or os.getenv("FACE_MODEL_ROOT", FACE_MODEL_ROOT_DEFAULT))
        self.model_path = root / "gan" / os.getenv("GAN_MODEL_FILENAME", GAN_MODEL_FILENAME_DEFAULT)
        self.threads = int(os.getenv("GAN_ONNX_THREADS", "1"))

    def _lazy_load(self) -> None:
        if GanDetector._session is not None or GanDetector._load_error is not None:
            return
        with GanDetector._init_lock:
            if GanDetector._session is not None or GanDetector._load_error is not None:
                return
            if not self.model_path.is_file():
                GanDetector._load_error = f"model file missing: {self.model_path}"
                logger.warning("gan_detector: %s", GanDetector._load_error)
                return
            try:
                # Import onnxruntime lazily so an unavailable install
                # doesn't crash import-time.
                import onnxruntime as ort  # type: ignore
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = self.threads
                opts.inter_op_num_threads = 1
                GanDetector._session = ort.InferenceSession(
                    str(self.model_path),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                logger.info("gan_detector: loaded %s (threads=%d)", self.model_path.name, self.threads)
            except Exception as exc:
                GanDetector._load_error = f"onnxruntime load failed: {exc}"
                logger.warning("gan_detector: %s", GanDetector._load_error)

    def available(self) -> bool:
        self._lazy_load()
        return GanDetector._session is not None

    @staticmethod
    def _preprocess(image_bytes: bytes) -> Optional[np.ndarray]:
        """Decode -> center-crop 224x224 -> scale to [-1,+1]."""
        try:
            import cv2  # type: ignore
        except Exception:
            logger.debug("gan_detector: cv2 unavailable")
            return None
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        if h < 32 or w < 32:
            return None
        side = min(h, w)
        y0, x0 = (h - side) // 2, (w - side) // 2
        img = img[y0:y0 + side, x0:x0 + side]
        img = cv2.resize(img, (224, 224), interpolation=cv2.INTER_AREA)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 127.5 - 1.0
        return np.transpose(img, (2, 0, 1))[None, :, :, :]

    def score(self, image_bytes: bytes) -> Optional[float]:
        """Return probability in [0, 1] that the input is GAN-generated,
        or None if we can't classify (model missing / decode failure)."""
        self._lazy_load()
        if GanDetector._session is None:
            return None
        x = self._preprocess(image_bytes)
        if x is None:
            return None
        try:
            session = GanDetector._session
            input_name = session.get_inputs()[0].name
            output = session.run(None, {input_name: x})[0]
            raw = float(np.asarray(output).squeeze())
        except Exception as exc:
            logger.warning("gan_detector: inference failed: %s", exc)
            return None
        # Sigmoid if the model outputs a logit (any absolute value > 1).
        if abs(raw) > 1:
            return float(1.0 / (1.0 + np.exp(-raw)))
        return max(0.0, min(1.0, raw))


__all__ = ["GanDetector", "GanDetectorUnavailable", "GAN_MODEL_VERSION"]
