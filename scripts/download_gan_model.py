"""Download the deepfake-detector ONNX weights used by
src/face/engine/gan_detector.py.

Do Next #4 support script. Runs once, then the operator flips
GAN_DETECTOR_ENABLED=1 and gan_face_screening starts scoring faces.

Default source: HuggingFace `prithivMLmods/Deepfake-Detection-Exp-02-22-ONNX`
(Apache-2.0, ViT-based deepfake vs. real image classifier). The paper Griffin
references was CNNDetection (Wang et al. CVPR'20), and this is a
newer-generation replacement in the same class - offline classifier, no auth,
public checkpoint. We pick the ``model_quantized.onnx`` variant to keep the
file small (~90 MB, fits in the analyzer container's cache).

Usage:
  # First-run: no hash needed. The script records the observed SHA-256 in
  # a sidecar .sha256 file next to the model.
  python scripts/download_gan_model.py

  # Later runs: script verifies against the sidecar .sha256 file (or an
  # explicit --sha256 argument). Refuses to touch anything on mismatch.
  python scripts/download_gan_model.py

  # Point at a different mirror + expected hash:
  python scripts/download_gan_model.py --url <mirror-url> --sha256 <hash>
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
DEFAULT_FILENAME = "deepfake_detector.onnx"
DEFAULT_URL = (
    "https://huggingface.co/prithivMLmods/Deepfake-Detection-Exp-02-22-ONNX/"
    "resolve/main/onnx/model_quantized.onnx"
)


def _sha256_file(path: Path, buf_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(buf_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_sidecar(path: Path) -> str | None:
    """Read a recorded SHA-256 from ``<path>.sha256`` if it exists."""
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if sidecar.is_file():
        return sidecar.read_text(encoding="utf-8").strip().split()[0] or None
    return None


def _write_sidecar(path: Path, digest: str) -> None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(digest + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"Direct URL to the ONNX weights (default: HuggingFace)")
    parser.add_argument("--sha256", default=None,
                        help="Expected SHA-256; if omitted, falls back to sidecar "
                             ".sha256 file; if that's also absent, records the "
                             "observed hash on first successful download.")
    parser.add_argument("--model-root", default=DEFAULT_MODEL_ROOT,
                        help=f"Model root (default: {DEFAULT_MODEL_ROOT})")
    parser.add_argument("--filename", default=DEFAULT_FILENAME,
                        help=f"Output filename (default: {DEFAULT_FILENAME})")
    parser.add_argument("--force", action="store_true", help="Overwrite existing file.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dest_dir = Path(args.model_root) / "gan"
    dest = dest_dir / args.filename
    expected = args.sha256 or _read_sidecar(dest)

    if dest.exists() and not args.force:
        actual = _sha256_file(dest)
        if expected is None:
            logger.info(
                "model already present at %s (sha256=%s). No expected hash on record; "
                "writing sidecar so future runs can verify.",
                dest, actual,
            )
            _write_sidecar(dest, actual)
            return 0
        if actual == expected:
            logger.info("model already present and hash matches: %s", dest)
            return 0
        logger.error(
            "model exists but hash mismatch (got %s, want %s); pass --force to overwrite",
            actual, expected,
        )
        return 2

    dest_dir.mkdir(parents=True, exist_ok=True)
    logger.info("downloading %s -> %s", args.url, dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(args.url, headers={
            "User-Agent": "unifiedanalyzer-gan-model-fetch/1.0",
        })
        with urllib.request.urlopen(req) as resp, tmp.open("wb") as f:
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
    if expected is not None and actual != expected:
        logger.error("hash mismatch after download: got %s, want %s", actual, expected)
        tmp.unlink()
        return 4

    tmp.replace(dest)
    _write_sidecar(dest, actual)
    logger.info("SUCCESS: %s (%.1f MB, sha256=%s, sidecar written)",
                dest, dest.stat().st_size / 1e6, actual)
    return 0


if __name__ == "__main__":
    sys.exit(main())
