"""Discovery module for file scanning and monitoring."""

from .scanner import DriveScanner, FileRecord
from .manifest import FileManifestManager
from .onedrive import OneDriveHandler

__all__ = [
    "DriveScanner",
    "FileRecord",
    "FileManifestManager",
    "FileWatcher",
    "OneDriveHandler",
]


def __getattr__(name):
    """Lazily expose FileWatcher (PEP 562).

    FileWatcher pulls in `watchdog`, which is NOT installed in the unifiedanalyzer
    image — the analyzer never runs the live file-watcher (only the legacy
    facetracker manager did). Importing it eagerly here made even
    `from .scanner import DriveScanner` fail with ModuleNotFoundError('watchdog'),
    breaking face_worker's drive scan. Defer the import so the package (and
    DriveScanner) loads without watchdog; accessing FileWatcher when watchdog is
    absent still raises the normal ModuleNotFoundError at access time.
    """
    if name == "FileWatcher":
        from .watcher import FileWatcher
        return FileWatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
