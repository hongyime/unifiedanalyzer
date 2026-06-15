"""OneDrive file handler with multi-faceted detection.

============================================================================
WARNING: this module is DEAD CODE on the production deployment.
============================================================================

The api process runs inside a Linux container (Docker Desktop on Windows).
Every detection method here calls a Windows-only binary:

  * `fsutil`   (line ~148)  -- Windows kernel utility, not in Linux
  * `powershell` (line ~185, 319) -- Windows shell, not in Linux
  * `attrib`   (line ~300)  -- Windows command, not in Linux

When called from the container all three raise `FileNotFoundError`,
caught silently, returning False. The pipeline reads the file straight
off the `/mnt/c` bind mount with no in-container download/revert flow.

CORRECTION (2026-05-27): an earlier version of this docstring claimed
reads through Docker Desktop's NTFS pass-through DON'T trigger Files-
On-Demand dehydration. THAT WAS WRONG — the original monitor script
was buggy (treated ReparsePoint=True as cloud-only when it's actually
set on cached files too). After fixing the monitor, we found 138/283
ingested OneDrive files were locally cached on C:. Reads through the
NTFS pass-through DO hydrate.

The current solution is OUT-OF-PROCESS:

  1. Pipeline marks `images.onedrive_revert_pending = True` on every
     OneDrive ingest (src/pipeline/processor.py:~520).
  2. Windows-host daemon `scripts/onedrive_evict.ps1` polls that flag
     hourly via Task Scheduler entry FacetrackerOneDriveEvict, runs
     `attrib +U -P` on each path, and flips the flag off on success.
  3. OneDrive's sync engine evicts the bytes on next sync.
  4. `scripts/onedrive_monitor.ps1` (menu option 20) provides
     observability — flags if ingested files stay locally cached too long.
  5. `/api/v1/stats/onedrive` reports daemon health to the dashboard.

This out-of-process design means this in-container module remains
dead code. We keep it (rather than delete) so the next reader doesn't
re-implement it from scratch and discover the Linux/Windows mismatch
the hard way.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import time

from src.face.config import Settings
from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class OneDriveHandler:
    """
    Handles OneDrive placeholder files with multi-faceted detection.
    
    Detection methods:
    1. Reparse point check (symlink analysis)
    2. Cloud attribute check (Windows API)
    3. Size mismatch check (reported vs actual)
    
    Requires 2+ indicators to confirm OneDrive status.
    """
    
    def __init__(self, config: Settings):
        """
        Initialize the OneDrive handler.
        
        Args:
            config: Application settings
        """
        self.config = config
        self.enabled = config.onedrive_enabled
        self.download_timeout = config.onedrive_download_timeout
        self.max_retries = config.onedrive_max_retries
        self.revert_verify = config.onedrive_revert_verify
        self.multi_detect = config.onedrive_multi_detect
        
        # Temp directory for downloads — only create if OneDrive is enabled.
        # When disabled, the dir stays absent so the handler is a cheap no-op.
        self.temp_dir = Path(config.face_storage_root) / "cache" / "onedrive_temp"
        if self.enabled:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
    
    def is_onedrive_path(self, file_path: str) -> bool:
        """
        Check if a file path is in a OneDrive directory.
        
        Args:
            file_path: File path to check
            
        Returns:
            True if path is in OneDrive
        """
        path_lower = file_path.lower()
        
        # Common OneDrive paths
        onedrive_indicators = [
            "/onedrive/",
            "\\onedrive\\",
            "/microsoft/onedrive/",
            "/skydrive/",
        ]
        
        return any(indicator in path_lower for indicator in onedrive_indicators)
    
    def detect_placeholder_status(self, file_path: str) -> Dict[str, Any]:
        """
        Detect OneDrive placeholder status using multiple methods.
        
        Args:
            file_path: Path to check
            
        Returns:
            Dictionary with detection results
        """
        result = {
            "is_placeholder": False,
            "is_online_only": False,
            "is_local": False,
            "has_reparse_point": False,
            "has_cloud_attribute": False,
            "size_mismatch": False,
            "confidence": 0,
        }
        
        path = Path(file_path)
        
        if not path.exists():
            return result
        
        try:
            # Method 1: Check reparse point (symlink)
            result["has_reparse_point"] = self._check_reparse_point(str(path))
            
            # Method 2: Check cloud attribute (via PowerShell)
            result["has_cloud_attribute"] = self._check_cloud_attribute(str(path))
            
            # Method 3: Check size mismatch
            result["size_mismatch"] = self._check_size_mismatch(str(path))
            
            # Count positive indicators
            indicators = sum([
                result["has_reparse_point"],
                result["has_cloud_attribute"],
                result["size_mismatch"],
            ])
            
            result["confidence"] = indicators / 3.0
            
            # Determine status based on indicators
            if self.multi_detect:
                # Require 2+ indicators for high confidence
                if indicators >= 2:
                    result["is_placeholder"] = True
                    result["is_online_only"] = True
                elif indicators == 1:
                    result["is_placeholder"] = True
                    result["is_local"] = False
            else:
                # Single indicator sufficient
                if indicators >= 1:
                    result["is_placeholder"] = True
                    result["is_online_only"] = True
            
            if not result["is_placeholder"]:
                result["is_local"] = True
                
        except Exception as e:
            logger.error(f"Error detecting OneDrive status for {file_path}: {e}")
        
        return result
    
    def _check_reparse_point(self, file_path: str) -> bool:
        """
        Check if file has a OneDrive-specific reparse point.

        Returns True only when fsutil's output mentions onedrive- or cloud-
        related tags. The previous implementation also returned True whenever
        `returncode == 0`, which is true for any reparse point including
        unrelated symlinks/junctions — that produced false positives that
        triggered spurious downloads. Now strictly content-based.
        """
        try:
            # Use Windows fsutil command. file_path is passed as a separate
            # argv element so the OS does no shell-string interpolation.
            result = subprocess.run(
                ["fsutil", "reparsepoint", "query", file_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode != 0:
                return False
            output = result.stdout.lower()
            return ("onedrive" in output) or ("cloud" in output)
            
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    def _check_cloud_attribute(self, file_path: str) -> bool:
        """
        Check if file has cloud / reparse / sparse attribute.

        Hardened against PowerShell command injection: `file_path` was
        previously interpolated into the script source. A filename containing
        a single quote would break out of the quoted argument and execute
        arbitrary PowerShell. We now read the path from $args[0] inside the
        script and pass it as a separate argv element.
        """
        try:
            ps_command = (
                "$p = $args[0]; "
                "$item = Get-Item -LiteralPath $p -ErrorAction SilentlyContinue; "
                "if ($item) { "
                "  $a = $item.Attributes; "
                "  $r = ($a -band [System.IO.FileAttributes]::ReparsePoint) -or "
                "       ($a -band [System.IO.FileAttributes]::SparseFile); "
                "  if ($r) { 'true' } else { 'false' } "
                "} else { 'false' }"
            )
            
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command, "--", file_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            return result.stdout.strip().lower() == "true"
            
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    def _check_size_mismatch(self, file_path: str) -> bool:
        """
        Check if reported file size differs from actual content.
        
        Placeholder files often report 0 bytes or incorrect size.
        
        Args:
            file_path: Path to check
            
        Returns:
            True if size mismatch detected
        """
        try:
            stat = os.stat(file_path)
            
            # Placeholder files often report 0 or very small size
            if stat.st_size == 0:
                return True
            
            # Check if size seems too small for file type
            ext = Path(file_path).suffix.lower()
            expected_min_sizes = {
                ".jpg": 1000,
                ".jpeg": 1000,
                ".png": 1000,
                ".mp4": 10000,
                ".mov": 10000,
            }
            
            min_size = expected_min_sizes.get(ext, 0)
            if min_size > 0 and stat.st_size < min_size:
                return True
            
            return False
            
        except OSError:
            return True  # Can't stat = likely placeholder
    
    def download_file(self, file_path: str) -> Optional[str]:
        """
        Download a OneDrive online-only file to local temp location.
        
        Args:
            file_path: Original file path
            
        Returns:
            Local path to downloaded file, or None if failed
        """
        if not self.enabled:
            return None
        
        for attempt in range(self.max_retries):
            try:
                # Create temp path
                temp_path = self.temp_dir / Path(file_path).name
                
                # Use PowerShell Copy-Item with -LiteralPath; paths come from
                # $args so a filename containing a quote can't break out of
                # the script.
                ps_command = (
                    "Copy-Item -LiteralPath $args[0] -Destination $args[1] -Force"
                )
                
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_command, "--",
                     file_path, str(temp_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.download_timeout,
                )
                
                if result.returncode == 0 and temp_path.exists():
                    logger.info(f"Downloaded OneDrive file: {file_path} -> {temp_path}")
                    return str(temp_path)
                
                logger.warning(f"OneDrive download attempt {attempt + 1} failed: {result.stderr}")
                
            except subprocess.TimeoutExpired:
                logger.error(f"OneDrive download timeout for {file_path}")
            except Exception as e:
                logger.error(f"OneDrive download error: {e}")
        
        return None
    
    def revert_to_online_only(self, file_path: str) -> bool:
        """
        Revert a local file back to OneDrive online-only.
        
        Uses PowerShell to set Files On-Demand status.
        
        Args:
            file_path: Path to file (original OneDrive path)
            
        Returns:
            True if successfully reverted
        """
        if not self.enabled:
            return False
        
        try:
            # `attrib +U <file>` marks the file as cloud-only on OneDrive.
            # Pass file_path as an argv element to avoid shell-string
            # interpolation; attrib accepts an unquoted path argument.
            result = subprocess.run(
                ["attrib", "+U", file_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            if result.returncode == 0:
                logger.info(f"Reverted to online-only: {file_path}")
                return True

            # PowerShell fallback: set the Offline attribute via $args[0].
            # Same hardening as elsewhere — path passed as argv, not interpolated.
            ps_command = (
                "$p = $args[0]; "
                "Get-Item -LiteralPath $p | "
                "ForEach-Object { $_.Attributes = $_.Attributes -bor "
                "[System.IO.FileAttributes]::Offline }"
            )
            fb = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command, "--", file_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if fb.returncode == 0:
                logger.info(f"Reverted to online-only via PS fallback: {file_path}")
                return True

            logger.warning(
                f"Failed to revert {file_path}: attrib stderr={result.stderr!r}, "
                f"ps stderr={fb.stderr!r}"
            )
            return False
            
        except Exception as e:
            logger.error(f"Error reverting OneDrive file: {e}")
            return False
    
    def verify_online_only(self, file_path: str) -> bool:
        """
        Verify that a file is now online-only.
        
        Args:
            file_path: Path to verify
            
        Returns:
            True if file is online-only
        """
        status = self.detect_placeholder_status(file_path)
        return status["is_online_only"]
    
    def cleanup_temp_files(self, max_age_hours: int = 24) -> int:
        """
        Clean up old temporary downloaded files.
        
        Args:
            max_age_hours: Maximum age of temp files to keep
            
        Returns:
            Number of files deleted
        """
        count = 0
        cutoff = time.time() - (max_age_hours * 3600)
        
        for temp_file in self.temp_dir.glob("*"):
            try:
                if temp_file.stat().st_mtime < cutoff:
                    temp_file.unlink()
                    count += 1
            except OSError:
                continue
        
        return count
    
    def process_file(self, file_path: str) -> Tuple[Optional[str], bool]:
        """
        Process a file, handling OneDrive download/revert.
        
        Args:
            file_path: Original file path
            
        Returns:
            Tuple of (local_path, should_revert)
        """
        if not self.is_onedrive_path(file_path):
            return file_path, False
        
        # Detect status
        status = self.detect_placeholder_status(file_path)
        
        if status["is_online_only"]:
            # Download to temp location
            local_path = self.download_file(file_path)
            return local_path, True if local_path else False
        
        return file_path, False
