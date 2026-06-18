"""Efficient parallel file scanner with generator-based yields."""

import os
import threading
import queue as queue_module
from pathlib import Path
from typing import Generator, List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json

from src.face.config import Settings
from src.face.utils.logging import get_logger

logger = get_logger(__name__)


# Sentinel object used by the producer threads to tell the consumer
# (scan_drives) that they're done. Singleton: identity check only.
_SCAN_SENTINEL = object()


@dataclass
class FileRecord:
    """Record of a discovered file."""
    path: str
    size: int
    mtime: float
    extension: str
    drive_type: str = "local"
    source_root: str = ""  # The drive root this record came from (e.g. "/mnt/c").
                            # Lets the manager keep per-drive progress counters
                            # without re-deriving the root from each path.
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "extension": self.extension,
            "drive_type": self.drive_type,
            "source_root": self.source_root,
        }


class DriveScanner:
    """
    Efficient parallel file scanner.
    
    Uses os.scandir() for fast directory traversal and ThreadPoolExecutor
    for parallel scanning of multiple drives.
    """
    
    def __init__(self, config: Settings):
        """
        Initialize the drive scanner.
        
        Args:
            config: Application settings
        """
        self.config = config
        self.exclude_paths = set(config.exclude_paths)
        self.supported_extensions = set(config.all_supported_extensions)

        # Directory-name blacklist (case-insensitive). Lowercased once at
        # init - matched against entry.name in _scandir_recursive before
        # we descend, so excluded subtrees cost zero stat() calls.
        self.exclude_dir_names = {
            name.lower() for name in (getattr(config, "exclude_dir_names", []) or [])
        }

        # Smallest file size we'll yield as a FileRecord. Files below this
        # are almost always icons / thumbnails / sidecars and would just
        # add queue churn before being rejected by face detection.
        # 0 means "no minimum".
        self.min_file_size_bytes = int(getattr(config, "min_file_size_bytes", 0) or 0)
        
        # Parse drive sources
        self.drive_sources = []
        if config.drive_sources:
            for source in config.drive_sources:
                if not source.exclude:
                    self.drive_sources.append(source)
    
    def scan_drives(self, batch_size: int = 1000) -> Generator[List[FileRecord], None, None]:
        """
        Scan all configured drives in TRUE parallel, yielding batched records.

        Architecture:
            - One producer thread per drive, walking that drive's tree and
              pushing FileRecord items into a shared bounded queue.
            - This generator runs on the calling thread, draining the queue
              and yielding fixed-size batches as fast as records arrive.
            - Each producer pushes a sentinel when done; we exit when all
              producers have signaled.

        This replaces the previous ThreadPoolExecutor-based design which
        was inadvertently sequential: submitting a generator function to
        an executor returns the generator immediately, so all the actual
        directory walking happened serially on the calling thread.

        The bounded queue (maxsize=batch_size*4) provides backpressure:
        if downstream is slow, producers block instead of building an
        unbounded buffer. Important on a 17k-image drive where one
        unbounded producer could allocate hundreds of MB of FileRecord
        objects before the consumer catches up.
        """
        if not self.drive_sources:
            # SCOPE LOCK (D3): fail closed. The merged unifiedanalyzer system
            # indexes faces ONLY from collector media (via
            # face_worker.ingest_collector_media + resolve_media_path, confined
            # to Z:/unifiedcollector). A bare C:/ + D:/ default here would walk
            # the whole system and HYDRATE OneDrive placeholders onto the
            # space-constrained C: drive (see memory: onedrive-hydration-hazard).
            # If a full drive scan is ever wanted again, set DRIVE_SOURCES
            # explicitly in .env — never let it default to C:.
            logger.error(
                "DriveScanner.scan_drives called with no drive_sources configured; "
                "refusing to walk C:/ (OneDrive hydration risk). Set DRIVE_SOURCES explicitly."
            )
            return

        # Bounded queue for backpressure. 4x batch_size gives producers
        # room to keep batching without blocking on every put when the
        # consumer is busy yielding.
        record_queue: queue_module.Queue = queue_module.Queue(maxsize=batch_size * 4)
        producers: List[threading.Thread] = []

        def _producer(source) -> None:
            """Worker that walks one drive and pushes records to the queue."""
            path = source.get("path") if isinstance(source, dict) else source.path
            try:
                for record in self._scan_single_drive(path):
                    # Bounded put; blocks if queue is full so we never
                    # spike memory. No timeout - producer waits for
                    # consumer to drain.
                    record_queue.put(record)
            except Exception as e:
                # Match the previous swallow behavior so a single
                # drive failure doesn't take down the whole scan.
                logger.error(f"Error scanning {path}: {e}")
            finally:
                # Sentinel signals "this producer is done". Consumer
                # counts sentinels to know when all drives have finished.
                record_queue.put(_SCAN_SENTINEL)

        # Launch one thread per configured drive. Daemon=True so a
        # forced shutdown of the manager doesn't get blocked waiting
        # for these threads to finish their walk.
        for source in self.drive_sources:
            t = threading.Thread(target=_producer, args=(source,), daemon=True)
            t.start()
            producers.append(t)

        if not producers:
            return

        buffer: List[FileRecord] = []
        sentinels_received = 0
        total_producers = len(producers)

        # Drain loop. Exits only when every producer has emitted a sentinel.
        while sentinels_received < total_producers:
            item = record_queue.get()
            if item is _SCAN_SENTINEL:
                sentinels_received += 1
                continue
            buffer.append(item)
            if len(buffer) >= batch_size:
                yield buffer
                buffer = []

        # Flush trailing partial batch.
        if buffer:
            yield buffer

        # Producers are daemon threads and have already finished by the
        # time we got their sentinels, but join briefly to be tidy.
        for t in producers:
            t.join(timeout=0.1)
    
    def _scan_single_drive(self, drive_path: str) -> Generator[FileRecord, None, None]:
        """
        Scan a single drive recursively.
        
        Args:
            drive_path: Root path to scan
            
        Yields:
            FileRecord objects for matching files
        """
        # Preserve the original string form of the root - we tag every yielded
        # record with this so progress counters can group by source drive
        # without each consumer re-deriving it from the file path.
        source_root = str(drive_path)
        drive_path = Path(drive_path)
        
        if not drive_path.exists():
            return
        
        # Check if drive is in exclude paths
        if str(drive_path) in self.exclude_paths:
            return
        
        try:
            for root, dirs, files in self._scandir_recursive(str(drive_path)):
                for filename in files:
                    file_path = Path(root) / filename
                    
                    # Check extension
                    ext = file_path.suffix.lower()
                    if ext not in self.supported_extensions:
                        continue
                    
                    # Check exclusion paths
                    if self._is_excluded(file_path):
                        continue
                    
                    try:
                        stat = file_path.stat()
                        # Skip junk files below the size threshold. Almost
                        # always icons / thumbnails / sidecars; their
                        # contribution to face detection is zero and they
                        # multiply queue insert cost. min_file_size_bytes=0
                        # disables this filter.
                        if self.min_file_size_bytes and stat.st_size < self.min_file_size_bytes:
                            continue
                        yield FileRecord(
                            path=str(file_path),
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                            extension=ext,
                            drive_type="local",
                            source_root=source_root,
                        )
                    except (OSError, IOError):
                        # Skip inaccessible files
                        continue
                        
        except PermissionError:
            logger.warning(f"Permission denied scanning: {drive_path}")
        except Exception as e:
            logger.error(f"Error scanning {drive_path}: {e}")
    
    def _scandir_recursive(self, path: str) -> Generator[tuple, None, None]:
        """
        Recursively scan directories using os.scandir() generator.
        
        Args:
            path: Directory path to scan
            
        Yields:
            Tuples of (root, dirs, files) like os.walk()
        """
        dirs = []
        files = []
        
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            # Two-stage exclusion:
                            #   1. Directory NAME blacklist (cheap, in-memory
                            #      set) - catches scattered junk like
                            #      ".thumbnails" / "@eaDir" anywhere in tree
                            #   2. Full-path prefix exclusion (existing
                            #      EXCLUDE_PATHS) - catches root-level
                            #      directories like /mnt/c/Windows
                            if self.exclude_dir_names and entry.name.lower() in self.exclude_dir_names:
                                continue
                            if not self._is_excluded(Path(entry.path)):
                                dirs.append(entry.name)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(entry.name)
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            return
        
        if files:
            yield (path, dirs, files)
        
        # Recurse into subdirectories
        for dir_name in dirs:
            subdir = os.path.join(path, dir_name)
            yield from self._scandir_recursive(subdir)
    
    def _is_excluded(self, file_path: Path) -> bool:
        """
        Check if a file path should be excluded.
        
        Args:
            file_path: Path to check
            
        Returns:
            True if excluded
        """
        path_str = str(file_path)
        
        for exclude_path in self.exclude_paths:
            if path_str.startswith(exclude_path):
                return True
        
        return False
    
    def scan_directory(self, directory: str, batch_size: int = 1000) -> Generator[List[FileRecord], None, None]:
        """
        Scan a single directory.
        
        Args:
            directory: Directory path to scan
            batch_size: Number of files per batch
            
        Yields:
            Batches of FileRecord objects
        """
        buffer: List[FileRecord] = []
        
        for record in self._scan_single_drive(directory):
            buffer.append(record)
            
            if len(buffer) >= batch_size:
                yield buffer
                buffer = []
        
        if buffer:
            yield buffer
