"""Video reader module for extracting frames using FFmpeg."""

import subprocess
import numpy as np
from pathlib import Path
from typing import Optional, Generator, Tuple
import tempfile
import os

from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class VideoReader:
    """Extract frames from video files using FFmpeg."""

    SUPPORTED_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'}

    def __init__(self, fps: float = 1.0, max_frame_size: int = 1920):
        """
        Initialize video reader.

        Args:
            fps: Frames per second to extract (default 1 FPS).
            max_frame_size: Maximum dimension for extracted frames.
        """
        self.fps = fps
        self.max_frame_size = max_frame_size

    def can_read(self, path: Path) -> bool:
        """Check if this reader can handle the file extension."""
        return path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def read_frames(self, path: Path) -> Generator[Tuple[np.ndarray, float], None, None]:
        """
        Extract frames from video at specified FPS.

        Args:
            path: Path to video file.

        Yields:
            Tuples of (frame_array, timestamp_seconds).
        """
        try:
            logger.debug(f"Reading video: {path} at {self.fps} FPS")

            # Get video duration and fps info
            info = self._get_video_info(path)
            if not info:
                return

            duration, video_fps = info
            if duration <= 0 or video_fps <= 0:
                logger.warning(f"Invalid video info for {path}")
                return

            # Calculate frame interval
            frame_interval = video_fps / self.fps

            # Use FFmpeg to extract frames
            cmd = [
                'ffmpeg',
                '-i', str(path),
                '-vf', f'fps={self.fps}',
                '-f', 'image2pipe',
                '-vcodec', 'png',
                '-'
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            frame_count = 0
            timestamp = 0.0
            frame_data = b''

            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break

                frame_data += chunk

                # Try to parse PNG frame
                if b'\x89PNG' in frame_data:
                    # Find frame boundaries
                    start = frame_data.find(b'\x89PNG')
                    if start > 0:
                        frame_data = frame_data[start:]

                    # Look for IEND marker
                    end = frame_data.find(b'IEND')
                    if end > 0:
                        end += 4  # Include IEND bytes
                        try:
                            # Decode frame
                            frame = self._decode_png(frame_data[:end])
                            if frame is not None:
                                yield frame, timestamp
                                frame_count += 1
                                timestamp = frame_count / self.fps
                        except Exception as e:
                            logger.debug(f"Failed to decode frame: {e}")

                        frame_data = frame_data[end:]

            process.stdout.close()
            stderr = process.stderr.read().decode()
            process.wait()

            logger.info(f"Extracted {frame_count} frames from {path}")

        except Exception as e:
            logger.error(f"Failed to read video {path}: {e}")

    def _get_video_info(self, path: Path) -> Optional[Tuple[float, float]]:
        """Get video duration and FPS using ffprobe."""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                str(path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None

            import json
            data = json.loads(result.stdout)

            # Find video stream
            video_stream = None
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break

            if not video_stream:
                return None

            duration = float(data.get('format', {}).get('duration', 0))
            fps_str = video_stream.get('r_frame_rate', '0/1')
            num, den = map(int, fps_str.split('/'))
            fps = num / den if den > 0 else 0

            return duration, fps

        except Exception as e:
            logger.error(f"Failed to get video info for {path}: {e}")
            return None

    def _decode_png(self, png_data: bytes) -> Optional[np.ndarray]:
        """Decode PNG bytes to numpy array."""
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(png_data))
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # Resize if needed
            width, height = img.size
            if max(width, height) > self.max_frame_size:
                scale = self.max_frame_size / max(width, height)
                new_size = (int(width * scale), int(height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            return np.array(img)

        except Exception as e:
            logger.debug(f"PNG decode error: {e}")
            return None

    def get_frame_count(self, path: Path) -> Optional[int]:
        """Estimate total frame count based on duration and FPS."""
        info = self._get_video_info(path)
        if info:
            duration, fps = info
            return int(duration * self.fps)
        return None
