"""Atomic operations utilities for Face Tracker application."""

import tempfile
import shutil
from pathlib import Path
from typing import Any, Callable
import json


def atomic_write_json(path: Path, data: Any) -> None:
    """
    Write JSON data atomically using a temporary file.
    
    Args:
        path: Target file path
        data: Data to serialize as JSON
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to temporary file first
    fd, temp_path = tempfile.mkstemp(suffix=".json", dir=path.parent)
    try:
        with open(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        # Atomic rename
        shutil.move(temp_path, path)
    except Exception:
        # Clean up temp file on failure
        try:
            Path(temp_path).unlink()
        except OSError:
            pass
        raise


def atomic_operation(operation: Callable[[], Any], rollback: Callable[[Any], None]) -> Any:
    """
    Execute `operation()`. If it raises, invoke `rollback(None)` and re-raise.

    The previous implementation ignored `rollback` entirely, so any caller
    that relied on rollback semantics would silently fail to clean up. This
    implementation:

      * runs `operation()` and returns its result on success;
      * on exception, calls `rollback(None)` for compensation (the contract
        passes None because there's no partial result to hand back);
      * rollback exceptions are logged but do not mask the original error.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        return operation()
    except Exception:
        try:
            rollback(None)
        except Exception as rb_exc:
            log.error("atomic_operation rollback failed: %s", rb_exc)
        raise
