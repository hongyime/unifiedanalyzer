"""Image reader module for loading various image formats."""

import numpy as np
from pathlib import Path
from PIL import Image
from typing import Optional, Tuple

from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class ImageReader:
    """Read images in various formats (JPEG, PNG, WebP, HEIC, etc.)."""

    SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.bmp', '.tiff', '.tif'}

    def __init__(self, max_size: int = 4096):
        """
        Initialize image reader.

        Args:
            max_size: Maximum dimension (width or height) to resize to.
        """
        self.max_size = max_size

    def can_read(self, path: Path) -> bool:
        """Check if this reader can handle the file extension."""
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def read(self, path: Path) -> Optional[np.ndarray]:
        """
        Read an image file and return as numpy array (RGB).

        Args:
            path: Path to image file.

        Returns:
            RGB image as numpy array (H, W, 3), or None on error.
        """
        try:
            logger.debug(f"Reading image: {path}")

            # Handle HEIC/HEIF separately
            if path.suffix.lower() in ('.heic', '.heif'):
                return self._read_heic(path)

            # Standard image formats — keep orig_img bound so the file handle
            # closes on __exit__ even after convert() rebinds img.
            with Image.open(path) as orig_img:
                orig_img.load()  # force full read before file closes
                img = orig_img.convert('RGB') if orig_img.mode != 'RGB' else orig_img.copy()

            img = self._maybe_resize(img)
            return np.array(img)

        except Exception as e:
            logger.error(f"Failed to read image {path}: {e}")
            return None

    def _read_heic(self, path: Path) -> Optional[np.ndarray]:
        """Read HEIC/HEIF format using pillow-heif."""
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()

            with Image.open(path) as orig_img:
                orig_img.load()
                img = orig_img.convert('RGB') if orig_img.mode != 'RGB' else orig_img.copy()

            img = self._maybe_resize(img)
            return np.array(img)

        except ImportError:
            logger.warning("pillow-heif not installed, cannot read HEIC files")
            return None
        except Exception as e:
            logger.error(f"Failed to read HEIC image {path}: {e}")
            return None

    def _maybe_resize(self, img: Image.Image) -> Image.Image:
        """Resize image if it exceeds max_size."""
        width, height = img.size
        if max(width, height) > self.max_size:
            scale = self.max_size / max(width, height)
            new_size = (int(width * scale), int(height * scale))
            logger.debug(f"Resizing image from {width}x{height} to {new_size[0]}x{new_size[1]}")
            return img.resize(new_size, Image.Resampling.LANCZOS)
        return img

    def get_dimensions(self, path: Path) -> Optional[Tuple[int, int]]:
        """Get image dimensions without fully loading."""
        try:
            with Image.open(path) as img:
                return img.size  # (width, height)
        except Exception as e:
            logger.error(f"Failed to get dimensions for {path}: {e}")
            return None
