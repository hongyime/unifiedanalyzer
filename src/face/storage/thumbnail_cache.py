"""Thumbnail cache management for on-demand thumbnail generation."""

from pathlib import Path
from typing import Optional
from PIL import Image
import io

from src.face.config import Settings


class ThumbnailCache:
    """
    Manages thumbnail generation and caching.
    
    Thumbnails are generated on-demand from source images and cached
    to avoid repeated processing.
    """
    
    def __init__(self, config: Settings):
        """
        Initialize the thumbnail cache.
        
        Args:
            config: Application settings
        """
        self.config = config
        self.cache_path = Path(config.thumbnail_cache_path)
        self.thumbnail_size = config.thumbnail_size
        self.thumbnail_quality = config.thumbnail_quality
        
        # Ensure cache directory exists
        self.cache_path.mkdir(parents=True, exist_ok=True)
    
    def get_thumbnail_path(self, face_id: str) -> Path:
        """
        Get the path for a thumbnail by face ID.
        
        Args:
            face_id: Unique face identifier
            
        Returns:
            Path to thumbnail file
        """
        return self.cache_path / f"{face_id}_thumb.jpg"
    
    def has_thumbnail(self, face_id: str) -> bool:
        """
        Check if a thumbnail exists in the cache.
        
        Args:
            face_id: Unique face identifier
            
        Returns:
            True if thumbnail exists
        """
        return self.get_thumbnail_path(face_id).exists()
    
    def generate_thumbnail(
        self, 
        source_path: Path, 
        face_id: str,
        bbox: tuple = None
    ) -> Path:
        """
        Generate a thumbnail from a source image.
        
        Args:
            source_path: Path to source image
            face_id: Unique face identifier for the thumbnail
            bbox: Optional bounding box (x, y, width, height) to crop
            
        Returns:
            Path to generated thumbnail
        """
        thumbnail_path = self.get_thumbnail_path(face_id)
        
        try:
            # Open source image
            img = Image.open(source_path)
            
            # Convert to RGB if necessary
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            # Crop to face if bbox provided
            if bbox:
                x, y, w, h = bbox
                img_width, img_height = img.size
                left = int(x * img_width)
                top = int(y * img_height)
                right = int((x + w) * img_width)
                bottom = int((y + h) * img_height)
                
                # Apply margin
                margin_x = int(w * img_width * 0.15)
                margin_y = int(h * img_height * 0.15)
                left = max(0, left - margin_x)
                top = max(0, top - margin_y)
                right = min(img_width, right + margin_x)
                bottom = min(img_height, bottom + margin_y)
                
                img = img.crop((left, top, right, bottom))
            
            # Resize to thumbnail size
            img.thumbnail((self.thumbnail_size, self.thumbnail_size), Image.Resampling.LANCZOS)
            
            # Save as JPEG
            img.save(thumbnail_path, 'JPEG', quality=self.thumbnail_quality, optimize=True)
            
            return thumbnail_path
            
        except Exception as e:
            print(f"Error generating thumbnail for {face_id}: {e}")
            raise
    
    def get_or_generate(
        self,
        source_path: Path,
        face_id: str,
        bbox: tuple = None
    ) -> Path:
        """
        Get existing thumbnail or generate if not exists.
        
        Args:
            source_path: Path to source image
            face_id: Unique face identifier
            bbox: Optional bounding box to crop
            
        Returns:
            Path to thumbnail
        """
        if self.has_thumbnail(face_id):
            return self.get_thumbnail_path(face_id)
        
        return self.generate_thumbnail(source_path, face_id, bbox)
    
    def delete_thumbnail(self, face_id: str) -> bool:
        """
        Delete a thumbnail from the cache.
        
        Args:
            face_id: Unique face identifier
            
        Returns:
            True if deleted, False if didn't exist
        """
        thumbnail_path = self.get_thumbnail_path(face_id)
        
        if thumbnail_path.exists():
            thumbnail_path.unlink()
            return True
        
        return False
    
    def clear_cache(self) -> int:
        """
        Clear all thumbnails from the cache.
        
        Returns:
            Number of thumbnails deleted
        """
        count = 0
        for thumb_file in self.cache_path.glob("*_thumb.jpg"):
            thumb_file.unlink()
            count += 1
        
        return count
    
    def get_cache_size(self) -> int:
        """
        Get the number of thumbnails in the cache.
        
        Returns:
            Number of cached thumbnails
        """
        return len(list(self.cache_path.glob("*_thumb.jpg")))
    
    def get_cache_disk_usage(self) -> int:
        """
        Get total disk usage of thumbnail cache in bytes.
        
        Returns:
            Total bytes used by thumbnails
        """
        total = 0
        for thumb_file in self.cache_path.glob("*_thumb.jpg"):
            total += thumb_file.stat().st_size
        
        return total
