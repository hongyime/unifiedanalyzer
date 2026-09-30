"""Download the CNNDetection ONNX weights used by src/face/engine/gan_detector.py.

Do Next #4 support script. Runs once, then the operator flips
GAN_DETECTOR_ENABLED=1 and gan_face_screening starts scoring faces.

The URL below is a placeholder for the operator's chosen mirror -
CNNDetection weights are BSD-licensed and mirrored in several places
(HuggingFace, university repos). The script:
  - Refuses to write if a valid file already exists.
  - Verifies SHA-256 against a hardcoded expected hash (fill in after
    downloading once).
  - Never falls back to alternative sources on hash mismatch.

Usage:
  python scripts/download_gan_model.py \
      --url <mirror-url> \
      --sha256 <expected-hash>
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ROOT = os.getenv("FACE_MODEL_ROOT", "Z:/unifiedanalyzer/media_derived/faces")
DEFAULT_FILENAME = "cnndetection_stylegan2.onnx"


def _sha256_file(path: Path, buf_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(buf_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Direct URL to the CNNDetection ONNX weights.")
    parser.add_argument("--sha256", required=True, help="Expected SHA-256 hex digest of the file.")
    parser.add_argument("--model-root", default=DEFAULT_MODEL_ROOT,
                        help=f"Model root (default: {DEFAULT_MODEL_ROOT})")
    parser.add_argument("--filename", default=DEFAULT_FILENAME,
                        help=f"Output filename (default: {DEFAULT_FILENAME})")
    parser.add_argument("--force", action="store_true", help="Overwrite existing file.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dest_dir = Path(args.model_root) / "gan"
    dest = dest_dir / args.filename

    if dest.exists() and not args.force:
        actual = _sha256_file(dest)
        if actual == args.sha256:
            logger.info("model already present and hash matches: %s", dest)
            return 0
        logger.error(
            "model exists but hash mismatch (got %s, want %s); pass --force to overwrite",
            actual, args.sha256,
        )
        return 2

    dest_dir.mkdir(parents=True, exist_ok=True)
    logger.info("downloading %s -> %s", args.url, dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(args.url) as resp, tmp.open("wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as exc:
        logger.error("download failed: %s", exc)
        if tmp.exists():
            tmp.unlink()
        return 3

    actual = _sha256_file(tmp)
    if actual != args.sha256:
        logger.error("hash mismatch after download: got %s, want %s", actual, args.sha256)
        tmp.unlink()
        return 4

    tmp.replace(dest)
    logger.info("SUCCESS: %s (%.1f MB, sha256 verified)", dest, dest.stat().st_size / 1e6)
    return 0


if __name__ == "__main__":
    sys.exit(main())
