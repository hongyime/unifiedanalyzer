import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from src.face.utils.logging import get_logger

logger = get_logger(__name__)

from src.face.config import Settings
from src.face.utils.atomic import atomic_write_json


class FileManifestManager:
    """Manages file manifest for tracking processed files.

    Two backends:
      1. JSON file (default) — fast in-memory dict, persisted atomically.
         Used by the scanner for per-file dedup checks during a scan walk.
         JSON is loaded once at startup and written on each successful process.

      2. Postgres images table (preferred when db is wired) — authoritative
         across restarts. When a db engine is provided via wire_db(), the
         needs_processing() check queries the images table directly so a
         fresh container restart doesn't re-process everything that was
         already in the DB.

    The two backends run in parallel: DB results take priority; JSON acts
    as a read-through cache for paths not yet in the DB (e.g. during first
    scan before the pipeline has had a chance to write the image row).
    """

    def __init__(self, config: Settings):
        self.config = config
        self.manifest_path = Path(config.face_storage_root) / "state" / "file_manifest.json"
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory manifest cache and lock
        self._manifest: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()

        # Optional DB engine for authoritative checks.
        # Set via wire_db(engine) after the DB is connected.
        self._db_engine = None

    def wire_db(self, engine) -> None:
        """Wire a SQLAlchemy engine for authoritative needs_processing checks.

        Call this once after the DB is connected (e.g. in main.py lifespan).
        When wired, needs_processing() checks the images table first and only
        falls back to the JSON manifest for paths not yet in the DB.
        """
        self._db_engine = engine
        logger.info("FileManifestManager: DB engine wired — using images table for dedup")

    @property
    def manifest(self) -> Dict[str, Any]:
        """Load manifest from disk if not cached."""
        with self._lock:
            if self._manifest is None:
                self._manifest = self._load_manifest()
            return self._manifest

    def _load_manifest(self) -> Dict[str, Any]:
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading manifest: {e}")

        return {
            "files": {},
            "deleted": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def save_manifest(self) -> None:
        """Save manifest to disk atomically."""
        with self._lock:
            self.manifest["last_updated"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(self.manifest_path, self.manifest)

    def add_file(
        self,
        file_path: str,
        file_hash: str,
        file_size: int,
        file_mtime: float,
        is_processed: bool = False,
    ) -> None:
        with self._lock:
            self.manifest["files"][file_path] = {
                "path": file_path,
                "hash": file_hash,
                "size": file_size,
                "mtime": file_mtime,
                "is_processed": is_processed,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }

    def get_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.manifest["files"].get(file_path)

    def needs_processing(self, file_path: str, current_mtime: float, current_size: int) -> bool:
        """Check if a file needs processing.

        Priority:
          1. If a DB engine is wired, query the images table. A row with
             status='completed' and matching mtime+size means skip.
          2. Fall back to the JSON manifest for paths not yet in the DB.

        A file needs processing if:
          - Not found in DB (new file).
          - Found with status != 'completed' (partially processed).
          - mtime or size has changed (content changed).
        """
        # --- DB path (authoritative) ---
        if self._db_engine is not None:
            try:
                from sqlalchemy import text
                with self._db_engine.connect() as conn:
                    row = conn.execute(
                        text(
                            "SELECT status, file_mtime, file_size FROM images "
                            "WHERE file_path = :p LIMIT 1"
                        ),
                        {"p": file_path},
                    ).fetchone()
                if row is not None:
                    status, db_mtime, db_size = row
                    if status == "completed":
                        if abs((db_mtime or 0.0) - current_mtime) <= 1.0 and (db_size or 0) == current_size:
                            return False  # already done, unchanged
                    # Row exists but status != completed, or file changed
                    return True
                # Not in DB at all — needs processing
                return True
            except Exception as e:
                # DB check failed — fall through to JSON manifest
                logger.warning(f"DB needs_processing check failed, falling back to JSON: {e}")

        # --- JSON fallback ---
        with self._lock:
            record = self.get_file(file_path)

            if record is None:
                return True

            if record.get("is_processed", False):
                if abs(record.get("mtime", 0) - current_mtime) > 1:
                    return True
                if record.get("size", 0) != current_size:
                    return True

            return False

    def mark_processed(self, file_path: str) -> None:
        with self._lock:
            if file_path in self.manifest["files"]:
                self.manifest["files"][file_path]["is_processed"] = True
                self.manifest["files"][file_path]["processed_at"] = datetime.now(timezone.utc).isoformat()

    def mark_deleted(self, file_path: str) -> None:
        with self._lock:
            if file_path in self.manifest["files"]:
                record = self.manifest["files"].pop(file_path)
                record["deleted_at"] = datetime.now(timezone.utc).isoformat()
                self.manifest["deleted"].append(record)

    def get_unprocessed_files(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                record for record in self.manifest["files"].values()
                if not record.get("is_processed", False)
            ]

    def get_processed_count(self) -> int:
        with self._lock:
            return sum(
                1 for record in self.manifest["files"].values()
                if record.get("is_processed", False)
            )

    def get_total_count(self) -> int:
        with self._lock:
            return len(self.manifest["files"])

    def get_deleted_count(self) -> int:
        with self._lock:
            return len(self.manifest["deleted"])

    def cleanup_deleted(self, max_age_days: int = 30) -> None:
        with self._lock:
            cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 24 * 60 * 60)
            self.manifest["deleted"] = [
                record for record in self.manifest["deleted"]
                if datetime.fromisoformat(record.get("deleted_at", "1970-01-01")).timestamp() > cutoff
            ]

    def export_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_files": self.get_total_count(),
                "processed_files": self.get_processed_count(),
                "unprocessed_files": self.get_total_count() - self.get_processed_count(),
                "deleted_files": self.get_deleted_count(),
                "last_updated": self.manifest.get("last_updated"),
            }
