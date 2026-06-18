"""File watcher for real-time monitoring using watchdog."""

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent, FileDeletedEvent
from pathlib import Path
from typing import Callable, List, Optional, Set
from src.face.utils.logging import get_logger

logger = get_logger(__name__)

import time
import threading
from collections import defaultdict

from src.face.config import Settings


class FileEventHandler(FileSystemEventHandler):
    """
    Custom file system event handler with debouncing.
    
    Debounces rapid events to avoid processing the same file
    multiple times during writes.
    """
    
    def __init__(
        self, 
        callback: Callable[[str, str], None],
        supported_extensions: Set[str],
        debounce_seconds: float = 2.0,
    ):
        """
        Initialize the event handler.
        
        Args:
            callback: Function to call with (file_path, event_type)
            supported_extensions: Set of file extensions to monitor
            debounce_seconds: Seconds to wait before triggering callback
        """
        super().__init__()
        self.callback = callback
        self.supported_extensions = supported_extensions
        self.debounce_seconds = debounce_seconds
        
        # Per-file timer dict. Each file gets its own timer so a rapid burst
        # across multiple files doesn't cause all-but-last to be silently dropped.
        self._pending_files: dict = {}
        self._timers: dict = {}  # file_path -> threading.Timer
        self._lock = threading.Lock()
    
    def _should_process(self, path: str) -> bool:
        """Check if file should be processed based on extension."""
        ext = Path(path).suffix.lower()
        return ext in self.supported_extensions
    
    def _debounce_trigger(self, file_path: str, event_type: str) -> None:
        """Trigger callback after debounce period for a specific file."""
        with self._lock:
            if file_path in self._pending_files:
                self.callback(file_path, event_type)
                del self._pending_files[file_path]
            self._timers.pop(file_path, None)

    def _schedule_debounce(self, file_path: str, event_type: str) -> None:
        """Schedule a debounced callback for this specific file path."""
        with self._lock:
            # Cancel any existing timer for THIS file only — other files unaffected
            existing = self._timers.get(file_path)
            if existing:
                existing.cancel()

            # Mark as pending and start a new per-file timer
            self._pending_files[file_path] = event_type
            t = threading.Timer(
                self.debounce_seconds,
                self._debounce_trigger,
                args=[file_path, event_type],
            )
            self._timers[file_path] = t
            t.start()
    
    def on_created(self, event):
        """Handle file creation events."""
        if event.is_directory:
            return
        
        if self._should_process(event.src_path):
            self._schedule_debounce(event.src_path, "created")
    
    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory:
            return
        
        if self._should_process(event.src_path):
            self._schedule_debounce(event.src_path, "modified")
    
    def on_deleted(self, event):
        """Handle file deletion events."""
        if event.is_directory:
            return
        
        if self._should_process(event.src_path):
            # Don't debounce deletions
            self.callback(event.src_path, "deleted")


class FileWatcher:
    """
    File watcher using watchdog library.
    
    Monitors directories for file changes and triggers callbacks
    for supported file types.
    """
    
    def __init__(self, config: Settings):
        """
        Initialize the file watcher.
        
        Args:
            config: Application settings
        """
        self.config = config
        self.supported_extensions = set(config.all_supported_extensions)
        self.exclude_paths = set(config.exclude_paths)
        
        self.observer: Optional[Observer] = None
        self.watched_paths: List[str] = []
        self._callback: Optional[Callable[[str, str], None]] = None
        self._running = False
    
    def start(self, callback: Callable[[str, str], None]) -> None:
        """
        Start watching files.
        
        Args:
            callback: Function to call with (file_path, event_type)
        """
        if self._running:
            return
        
        self._callback = callback
        
        # Create event handler
        event_handler = FileEventHandler(
            callback=callback,
            supported_extensions=self.supported_extensions,
            debounce_seconds=2.0,
        )
        
        # Create observer
        self.observer = Observer()
        
        # Watch configured drive sources
        paths_to_watch = self._get_paths_to_watch()
        
        for path in paths_to_watch:
            try:
                p = Path(path)
                if not p.exists() or not p.is_dir():
                    logger.warning(f"Skipping non-existent or invalid watch directory: {path}")
                    continue
                    
                self.observer.schedule(event_handler, path, recursive=True)
                self.watched_paths.append(path)
                logger.info(f"Now watching: {path}")
            except Exception as e:
                logger.error(f"Failed to add watch path {path}: {e}")
        
        # Start observer
        self.observer.start()
        self._running = True
        logger.info(f"File watcher started, monitoring {len(self.watched_paths)} paths")
    
    def stop(self) -> None:
        """Stop watching files."""
        if not self._running:
            return
        
        self.observer.stop()
        self.observer.join(timeout=5)
        self._running = False
        self.watched_paths.clear()
        logger.info("File watcher stopped")
    
    def _get_paths_to_watch(self) -> List[str]:
        """
        Get list of paths to watch.
        
        Returns:
            List of directory paths
        """
        paths = []
        
        # Parse drive sources from config
        if self.config.drive_sources:
            for source in self.config.drive_sources:
                path = source.get("path") if isinstance(source, dict) else source.path
                
                # Skip excluded paths
                if path in self.exclude_paths:
                    continue
                
                paths.append(path)
        else:
            # Default paths
            default_paths = ["C:/Users", "D:/"]
            paths = [p for p in default_paths if p not in self.exclude_paths]
        
        return paths
    
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._running
    
    def add_watch_path(self, path: str) -> bool:
        """
        Add a path to watch.
        
        Args:
            path: Directory path to watch
            
        Returns:
            True if added successfully
        """
        if not self.observer or path in self.watched_paths:
            return False
        
        if path in self.exclude_paths:
            return False
        
        try:
            event_handler = FileEventHandler(
                callback=self._callback,
                supported_extensions=self.supported_extensions,
            )
            self.observer.schedule(event_handler, path, recursive=True)
            self.watched_paths.append(path)
            return True
        except Exception as e:
            logger.error(f"Failed to add watch path {path}: {e}")
            return False
    
    def remove_watch_path(self, path: str) -> bool:
        """
        Remove a path from watching.
        
        Args:
            path: Directory path to stop watching
            
        Returns:
            True if removed successfully
        """
        if path not in self.watched_paths:
            return False
        
        # Note: watchdog doesn't support removing individual watches
        # Need to restart the observer
        self.stop()
        self.watched_paths.remove(path)
        self.start(self._callback)
        
        return True
