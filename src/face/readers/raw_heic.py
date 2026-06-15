"""Raw HEIC/HEIF format handler."""

import numpy as np
from pathlib import Path
from typing import Optional, List

from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class RawHEICReader:
    """Handle RAW and HEIC formats with special processing."""

    SUPPORTED_EXTENSIONS = {'.heic', '.heif', '.dng', '.cr2', '.nef', '.arw', '.raf'}

    def __init__(self, max_size: int = 4096):
        """
        Initialize RAW/HEIC reader.

        Args:
            max_size: Maximum dimension for output images.
        """
        self.max_size = max_size

    def can_read(self, path: Path) -> bool:
        """Check if this reader can handle the file extension."""
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def read(self, path: Path) -> Optional[np.ndarray]:
        """
        Read RAW/HEIC file and return as RGB numpy array.

        Args:
            path: Path to file.

        Returns:
            RGB image as numpy array, or None on error.
        """
        try:
            suffix = path.suffix.lower()

            if suffix in ('.heic', '.heif'):
                return self._read_heic(path)
            elif suffix == '.dng':
                return self._read_dng(path)
            else:
                # Other RAW formats - try generic approach
                return self._read_generic_raw(path)

        except Exception as e:
            logger.error(f"Failed to read RAW/HEIC file {path}: {e}")
            return None

    def _read_heic(self, path: Path) -> Optional[np.ndarray]:
        """Read HEIC/HEIF using pillow-heif."""
        try:
            from pillow_heif import register_heif_opener, HeifFile
            register_heif_opener()

            from PIL import Image

            heif_file = HeifFile(path)
            
            # Get primary image
            if len(heif_file) > 0:
                heif_image = heif_file[0]
                data = np.array(heif_image.data)
                
                # Handle different bit depths
                if data.dtype == np.uint16:
                    data = (data / 256).astype(np.uint8)
                
                # Convert to RGB if necessary
                if len(data.shape) == 2:
                    # Grayscale to RGB
                    data = np.stack([data] * 3, axis=-1)
                elif data.shape[2] == 4:
                    # RGBA to RGB
                    data = data[:, :, :3]

                # Resize if needed
                img = Image.fromarray(data)
                if max(img.size) > self.max_size:
                    scale = self.max_size / max(img.size)
                    new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)

                return np.array(img)

            return None

        except ImportError:
            logger.warning("pillow-heif not installed, cannot read HEIC files")
            return None
        except Exception as e:
            logger.error(f"HEIC read error for {path}: {e}")
            return None

    def _read_dng(self, path: Path) -> Optional[np.ndarray]:
        """Read DNG RAW file."""
        try:
            import rawpy
            import imageio

            with rawpy.imread(str(path)) as raw:
                # Post-process to get RGB image
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    half_size=False,
                    no_auto_bright=False,
                    output_bps=8
                )
                
                from PIL import Image
                img = Image.fromarray(rgb)
                
                # Resize if needed
                if max(img.size) > self.max_size:
                    scale = self.max_size / max(img.size)
                    new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                return np.array(img)

        except ImportError:
            logger.warning("rawpy not installed, cannot read DNG files")
            return None
        except Exception as e:
            logger.error(f"DNG read error for {path}: {e}")
            return None

    def _read_generic_raw(self, path: Path) -> Optional[np.ndarray]:
        """Try to read other RAW formats using available libraries."""
        try:
            # Try rawpy first (supports many RAW formats)
            import rawpy
            
            with rawpy.imread(str(path)) as raw:
                rgb = raw.postprocess()
                from PIL import Image
                img = Image.fromarray(rgb)
                
                if max(img.size) > self.max_size:
                    scale = self.max_size / max(img.size)
                    new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                return np.array(img)

        except ImportError:
            logger.warning(f"Cannot read RAW format {path.suffix}, rawpy not installed")
            return None
        except Exception as e:
            logger.error(f"Generic RAW read error for {path}: {e}")
            return None

    def get_thumbnail(self, path: Path, size: int = 256) -> Optional[np.ndarray]:
        """Extract embedded thumbnail from RAW/HEIC if available."""
        try:
            suffix = path.suffix.lower()
            
            if suffix in ('.heic', '.heif'):
                from pillow_heif import HeifFile
                heif_file = HeifFile(path)
                if len(heif_file) > 0:
                    data = np.array(heif_file[0].data)
                    img = Image.fromarray(data)
                    img.thumbnail((size, size), Image.Resampling.LANCZOS)
                    return np.array(img.convert('RGB'))
            
            elif suffix == '.dng':
                import rawpy
                with rawpy.imread(str(path)) as raw:
                    # Use quick preview if available
                    preview = raw.extract_preview()
                    if preview is not None:
                        img = Image.fromarray(preview)
                        img.thumbnail((size, size), Image.Resampling.LANCZOS)
                        return np.array(img.convert('RGB'))

        except Exception as e:
            logger.debug(f"Failed to extract thumbnail from {path}: {e}")
        
        return None
