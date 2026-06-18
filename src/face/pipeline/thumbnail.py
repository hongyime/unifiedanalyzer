"""Thumbnail generation module for face crops."""

import numpy as np
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
import io

from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class ThumbnailGenerator:
    """Generate thumbnails from face crops and full images."""

    def __init__(
        self,
        default_size: int = 256,
        face_size: int = 512,
        margin_ratio: float = 0.15,
        quality: int = 85
    ):
        """
        Initialize thumbnail generator.

        Args:
            default_size: Default thumbnail size for general images.
            face_size: Size for face crop thumbnails.
            margin_ratio: Margin ratio around face (15% per PRD).
            quality: JPEG quality (0-100).
        """
        self.default_size = default_size
        self.face_size = face_size
        self.margin_ratio = margin_ratio
        self.quality = quality

    def generate_face_thumbnail(
        self,
        image: np.ndarray,
        bbox: np.ndarray,
        output_path: Optional[Path] = None
    ) -> Tuple[Optional[np.ndarray], Optional[bytes]]:
        """
        Generate thumbnail from a face crop with margin.

        Args:
            image: Full RGB image (H, W, 3).
            bbox: Bounding box [x1, y1, x2, y2].
            output_path: Optional path to save thumbnail.

        Returns:
            Tuple of (thumbnail_array, thumbnail_bytes), either can be None on error.
        """
        try:
            height, width = image.shape[:2]
            
            # Extract bbox coordinates
            x1, y1, x2, y2 = bbox.astype(int)
            
            # Add margin
            face_width = x2 - x1
            face_height = y2 - y1
            margin_x = int(face_width * self.margin_ratio)
            margin_y = int(face_height * self.margin_ratio)
            
            # Apply margin with bounds checking
            x1_margin = max(0, x1 - margin_x)
            y1_margin = max(0, y1 - margin_y)
            x2_margin = min(width, x2 + margin_x)
            y2_margin = min(height, y2 + margin_y)
            
            # Crop face with margin
            face_crop = image[y1_margin:y2_margin, x1_margin:x2_margin]
            
            if face_crop.size == 0:
                logger.warning("Empty face crop after margin")
                return None, None
            
            # Resize to face_size
            img = Image.fromarray(face_crop)
            img = img.resize((self.face_size, self.face_size), Image.Resampling.LANCZOS)
            
            # Convert to bytes
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=self.quality, optimize=True)
            thumbnail_bytes = buffer.getvalue()
            
            # Save to file if path provided
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                img.save(output_path, format='JPEG', quality=self.quality, optimize=True)
                logger.debug(f"Saved thumbnail to {output_path}")
            
            return np.array(img), thumbnail_bytes
            
        except Exception as e:
            logger.error(f"Face thumbnail generation failed: {e}")
            return None, None

    def generate_image_thumbnail(
        self,
        image: np.ndarray,
        output_path: Optional[Path] = None,
        size: Optional[int] = None
    ) -> Tuple[Optional[np.ndarray], Optional[bytes]]:
        """
        Generate thumbnail from a full image.

        Args:
            image: Full RGB image (H, W, 3).
            output_path: Optional path to save thumbnail.
            size: Override default thumbnail size.

        Returns:
            Tuple of (thumbnail_array, thumbnail_bytes).
        """
        try:
            size = size or self.default_size
            
            img = Image.fromarray(image)
            
            # Create square thumbnail while maintaining aspect ratio
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            
            # Convert to bytes
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=self.quality, optimize=True)
            thumbnail_bytes = buffer.getvalue()
            
            # Save to file if path provided
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                img.save(output_path, format='JPEG', quality=self.quality, optimize=True)
            
            return np.array(img), thumbnail_bytes
            
        except Exception as e:
            logger.error(f"Image thumbnail generation failed: {e}")
            return None, None

    def generate_from_path(
        self,
        image_path: Path,
        bbox: Optional[np.ndarray] = None,
        output_path: Optional[Path] = None
    ) -> Tuple[Optional[np.ndarray], Optional[bytes]]:
        """
        Generate thumbnail directly from image file path.

        Args:
            image_path: Path to source image.
            bbox: Optional bounding box for face crop.
            output_path: Optional output path.

        Returns:
            Tuple of (thumbnail_array, thumbnail_bytes).
        """
        try:
            # Load image
            with Image.open(image_path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                image = np.array(img)
            
            if bbox is not None:
                return self.generate_face_thumbnail(image, bbox, output_path)
            else:
                return self.generate_image_thumbnail(image, output_path)
                
        except Exception as e:
            logger.error(f"Thumbnail generation from path failed: {e}")
            return None, None

    def ensure_thumbnail_exists(
        self,
        thumbnail_path: Path,
        source_path: Path,
        bbox: Optional[np.ndarray] = None
    ) -> bool:
        """
        Check if thumbnail exists, generate if not.

        Args:
            thumbnail_path: Expected thumbnail path.
            source_path: Source image path.
            bbox: Optional bounding box for face crop.

        Returns:
            True if thumbnail exists or was generated successfully.
        """
        if thumbnail_path.exists():
            return True
        
        _, thumb_bytes = self.generate_from_path(source_path, bbox, thumbnail_path)
        return thumb_bytes is not None

    def get_cached_or_generate(
        self,
        thumbnail_path: Path,
        source_path: Path,
        bbox: Optional[np.ndarray] = None
    ) -> Optional[bytes]:
        """
        Get cached thumbnail or generate new one.

        Args:
            thumbnail_path: Expected thumbnail cache path.
            source_path: Source image path.
            bbox: Optional bounding box.

        Returns:
            Thumbnail bytes if available.
        """
        # Try to load cached thumbnail
        if thumbnail_path.exists():
            try:
                with open(thumbnail_path, 'rb') as f:
                    return f.read()
            except Exception as e:
                logger.warning(f"Failed to load cached thumbnail: {e}")
        
        # Generate new thumbnail
        _, thumb_bytes = self.generate_from_path(source_path, bbox, thumbnail_path)
        return thumb_bytes
