"""Discovery module for file scanning and monitoring."""

from .scanner import DriveScanner, FileRecord
from .manifest import FileManifestManager
from .watcher import FileWatcher
from .onedrive import OneDriveHandler

__all__ = [
    "DriveScanner",
    "FileRecord",
    "FileManifestManager",
    "FileWatcher",
    "OneDriveHandler",
]
