"""Indexing manager to coordinate scanning and processing."""

import threading
import queue
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from src.face.utils.logging import get_logger
from src.face.utils.connectivity import ConnectivityGuard
from src.face.discovery.scanner import DriveScanner, FileRecord
from src.face.discovery.manifest import FileManifestManager
from src.face.discovery.watcher import FileWatcher
from src.face.pipeline.processor import PipelineProcessor
from src.face.storage.database import Database
from src.face.config import Settings

logger = get_logger(__name__)


class IndexingManager:
    """
    Coordinates the background indexing process.
    
    Responsibilities:
    - Scanning drives for new/changed files
    - Queuing files for processing
    - Managing processing threads
    - Real-time file watching
    - Progress tracking
    """
    
    def __init__(
        self,
        config: Settings,
        processor: PipelineProcessor,
        manifest: FileManifestManager,
        watcher: FileWatcher,
        db: Database,
    ):
        self.config = config
        self.processor = processor
        self.manifest = manifest
        self.watcher = watcher
        self.db = db
        self.scanner = DriveScanner(config)
        
        # Queues
        self.processing_queue = queue.Queue(maxsize=config.index_queue_size)
        
        # Threading
        self._scan_thread: Optional[threading.Thread] = None
        self._worker_threads: List[threading.Thread] = []
        self._stop_event = threading.Event()
        
        # Progress Tracking
        self.is_scanning = False
        self.current_file: Optional[str] = None
        # Three independent counters (3A). Each measures a distinct stage
        # of the pipeline — they SHOULD diverge during normal operation
        # and the divergence itself is the diagnostic signal:
        #
        #   files_discovered  - producer side: yielded by the scanner
        #                       walker after extension/size/dir-name
        #                       filters. This is "what we saw on disk".
        #
        #   files_queued      - passed the manifest dedup check (mtime/size
        #                       unchanged since last run) and was put on
        #                       the processing queue. discovered - queued
        #                       == "skipped because already processed".
        #
        #   files_processed   - worker pulled from queue, processor
        #                       returned status='success'.
        #
        #   files_failed      - worker pulled from queue, processor
        #                       returned a non-success status (file
        #                       unreadable, no faces, model error, etc.).
        #
        # Invariants the dashboard can rely on:
        #   files_processed + files_failed <= files_queued <= files_discovered
        #   queue_depth == files_queued - (files_processed + files_failed)
        #     (modulo in-flight worker, off-by-one is fine)
        #
        # files_scanned / files_total are kept as legacy aliases for the
        # dashboard JS that already reads them; both equal files_discovered.
        self.files_discovered = 0
        self.files_queued = 0
        self.files_processed = 0
        self.files_failed = 0
        self.scan_start_time: Optional[datetime] = None

        # Per-drive progress. Keyed by FileRecord.source_root (e.g. "/mnt/c").
        # Each value: {"discovered": int, "queued": int, "processed": int, "failed": int}.
        # Reset at the start of every scan loop iteration. Read-only from
        # the API thread; writes only happen on the scanner thread, so
        # the dict-of-dicts is safe to publish without a lock (atomic
        # in CPython for the single-key updates we do).
        self.per_drive: Dict[str, Dict[str, int]] = {}

        # Queue + processing observability state. Updated lazily by
        # get_progress(); kept on the instance so high-water-mark
        # survives across calls. _processing_anchor_time is set when
        # the first worker pulls a record - that's when "processing"
        # actually started, distinct from when the scan thread woke up.
        self._queue_high_water: int = 0
        self._processing_anchor_time: Optional[datetime] = None
        
        # Connectivity guard — gates scan + worker loops on DB health.
        # Logs once on drop, once on recovery, quiet in between.
        self._scan_guard = ConnectivityGuard(db.engine, poll_interval=15, label="ScanLoop")
        self._worker_guard = ConnectivityGuard(db.engine, poll_interval=10, label="Worker")

        # Stats
        self.last_scan_completed: Optional[datetime] = None
        
    def start(self) -> None:
        """Start the indexing process."""
        if self._scan_thread and self._scan_thread.is_alive():
            logger.warning("Indexing already in progress")
            return
            
        logger.info("Starting Indexing Manager")
        self._stop_event.clear()
        
        # Start worker threads
        num_workers = self.config.index_workers
        for i in range(num_workers):
            t = threading.Thread(target=self._worker_loop, name=f"IndexerWorker-{i}")
            t.daemon = True
            t.start()
            self._worker_threads.append(t)

        # Recovery: re-queue any orphaned pending rows from prior incomplete runs.
        # Runs in its own thread so app startup is not blocked by a long DB
        # query against millions of rows. Workers will start draining the queue
        # immediately as recovery enqueues find them.
        recovery_thread = threading.Thread(
            target=self._recover_pending_images,
            name="PendingRecoveryThread",
        )
        recovery_thread.daemon = True
        recovery_thread.start()

        # Start scan thread
        self._scan_thread = threading.Thread(target=self._scan_loop, name="ScannerThread")
        self._scan_thread.daemon = True
        self._scan_thread.start()
        
        # Start watcher if enabled
        if self.config.watch_mode:
            watcher_thread = threading.Thread(
                target=self.watcher.start, 
                kwargs={"callback": self._on_file_event},
                name="WatcherStartupThread"
            )
            watcher_thread.daemon = True
            watcher_thread.start()
            
        logger.info(f"Indexing Manager started with {num_workers} workers")
        
    def stop(self) -> None:
        """Stop the indexing process."""
        logger.info("Stopping Indexing Manager...")
        self._stop_event.set()
        
        if self.watcher.is_running():
            self.watcher.stop()
            
        # Wait for threads to finish
        if self._scan_thread:
            self._scan_thread.join(timeout=5)
            if self._scan_thread.is_alive():
                logger.warning("ScannerThread did not exit within 5s — may still be running")
            
        for t in self._worker_threads:
            t.join(timeout=5)
            if t.is_alive():
                logger.warning(f"Worker thread {t.name} did not exit within 5s")
            
        self._worker_threads.clear()
        logger.info("Indexing Manager stopped")
        
    def get_progress(self) -> Dict[str, Any]:
        """Get current indexing progress.

        Returns a dict with both legacy and new fields:

        Legacy (keep for backwards-compat with existing dashboard JS):
            is_scanning, current_file, files_scanned, files_total,
            files_processed, files_failed, progress_percent,
            eta_seconds, last_scan_completed

        New (richer signal — additive, won't break consumers):
            queue_depth: how many records are queued in front of workers
                         right now. The real "what's left" number for the
                         processing phase. files_total grows as we scan
                         and so progress_percent reads ~100% in steady
                         state - this is the honest backlog.
            queue_high_water_mark: peak observed queue_depth this run.
                                   Useful for sizing index_queue_size.
            processing_rate_per_sec: rolling rate of file completions.
                                     None until enough samples gather.
            processing_eta_seconds: queue_depth / rate. The "minutes left
                                    until backlog drains" the user
                                    actually wants. None if rate unknown.
            per_drive: per-drive scanned + queued counts (unchanged from
                       previous implementation).
        """
        progress = 0
        if self.files_discovered > 0:
            # Honest "what % of discovered files have been resolved one way
            # or another (processed or failed)". When there's a big backlog
            # this stays low and tracks real progress, unlike the old
            # files_scanned/files_total which were identical and always 100.
            resolved = self.files_processed + self.files_failed
            progress = (resolved / self.files_discovered) * 100
            
        eta = None
        if self.is_scanning and self.scan_start_time and self.files_discovered > 0:
            elapsed = (datetime.now() - self.scan_start_time).total_seconds()
            if elapsed > 0:
                files_per_sec = self.files_discovered / elapsed
                # Legacy ETA = "scan walk time remaining". Best-effort
                # since we don't know files_total ahead of time without a
                # pre-scan; use queue_depth as a stand-in for "left to do".
                if files_per_sec > 0:
                    eta = self.files_queued / files_per_sec if files_per_sec > 0 else None

        # Snapshot per-drive counters. We copy the dict so the API thread
        # never observes a half-updated entry (writes happen on scanner
        # thread). For each drive we also derive a friendly label from
        # the root path.
        per_drive_view: Dict[str, Dict[str, Any]] = {}
        for root, counts in dict(self.per_drive).items():
            discovered = int(counts.get("discovered", counts.get("scanned", 0)))
            queued = int(counts.get("queued", 0))
            processed = int(counts.get("processed", 0))
            failed = int(counts.get("failed", 0))
            # Friendly label: "/mnt/c" -> "C:", "/mnt/y" -> "Y:".
            label = root
            if root.startswith("/mnt/") and len(root) >= 6:
                label = root[5].upper() + ":"
            per_drive_view[root] = {
                "label": label,
                # New three-counter view. "scanned" kept as legacy alias.
                "discovered": discovered,
                "queued": queued,
                "queued_for_processing": queued,  # legacy alias
                "processed": processed,
                "failed": failed,
                "scanned": discovered,  # legacy alias
            }

        # Live queue depth. Most honest "what's left" number we have.
        # qsize() is approximate (CPython doesn't lock when reading) but
        # off-by-a-few in either direction is fine for a progress display.
        queue_depth = 0
        try:
            queue_depth = int(self.processing_queue.qsize())
        except Exception:
            queue_depth = 0

        # Update high-water mark. Only ever grows, reset at process start.
        if queue_depth > getattr(self, "_queue_high_water", 0):
            self._queue_high_water = queue_depth

        # Processing rate over the lifetime of this scanner instance.
        # Use processing_start_time if we have it, otherwise fall back to
        # scan_start_time which is close enough.
        rate = None
        eta_processing = None
        anchor_time = getattr(self, "_processing_anchor_time", None) or self.scan_start_time
        if anchor_time and self.files_processed > 0:
            elapsed = (datetime.now() - anchor_time).total_seconds()
            if elapsed > 1.0:  # avoid divide-by-tiny-number explosions
                rate = self.files_processed / elapsed
                if rate > 0 and queue_depth > 0:
                    eta_processing = queue_depth / rate

        return {
            # --- legacy fields (do not remove; dashboard JS reads these) ---
            "is_scanning": self.is_scanning,
            "current_file": self.current_file,
            "files_scanned": self.files_discovered,  # legacy alias
            "files_total": self.files_discovered,    # legacy alias
            "files_processed": self.files_processed,
            "files_failed": self.files_failed,
            "progress_percent": round(progress, 2),
            "eta_seconds": round(eta, 2) if eta is not None else None,
            "last_scan_completed": self.last_scan_completed.isoformat() if self.last_scan_completed else None,
            "per_drive": per_drive_view,
            # --- new three-counter view (3A) ---
            "files_discovered": self.files_discovered,
            "files_queued": self.files_queued,
            "files_skipped": max(0, self.files_discovered - self.files_queued),
            # --- new richer fields (additive) ---
            "queue_depth": queue_depth,
            "queue_high_water_mark": int(getattr(self, "_queue_high_water", 0)),
            "processing_rate_per_sec": round(rate, 3) if rate is not None else None,
            "processing_eta_seconds": round(eta_processing, 1) if eta_processing is not None else None,
        }
        
    def _scan_loop(self) -> None:
        """Main loop for periodic drive scanning."""
        while not self._stop_event.is_set():
            # Block quietly if DB is unreachable (internet loss, postgres restart).
            if not self._scan_guard.wait_for_db(self._stop_event):
                break  # stop_event fired
            try:
                self.is_scanning = True
                self.scan_start_time = datetime.now()
                # Reset all counters at start of each scan iteration.
                self.files_discovered = 0
                self.files_queued = 0
                # files_processed/files_failed are NOT reset - they're
                # cumulative since process start so the rate calculation
                # has stable history. Resetting them on every scan loop
                # iteration would make rate spike/drop per cycle.
                # Reset per-drive counters at start of each scan iteration.
                # Pre-seed with configured drive sources so API consumers see
                # all drives from t=0 (with discovered=0) instead of one-by-one
                self.per_drive = {}
                try:
                    for src in (self.config.drive_sources or []):
                        root = src.get("path") if isinstance(src, dict) else getattr(src, "path", None)
                        if root and not (src.get("exclude") if isinstance(src, dict) else getattr(src, "exclude", False)):
                            self.per_drive[str(root)] = {
                                "discovered": 0, "queued": 0,
                                "processed": 0, "failed": 0,
                            }
                except Exception:
                    # Defensive: if config shape ever drifts we still want
                    # the scan loop to run; per-drive view will populate
                    # lazily as records arrive.
                    pass
                
                logger.info("Starting drive scan...")
                
                # First pass: Count files for progress (optional but helpful)
                # For now, we'll just increment as we go
                
                for batch in self.scanner.scan_drives():
                    if self._stop_event.is_set():
                        break
                        
                    for record in batch:
                        self.files_discovered += 1

                        # Per-drive discovered counter. Lazy-init the bucket if
                        # we somehow see a record from an unexpected root
                        # (shouldn't happen but cheap safety).
                        root = record.source_root or ""
                        if root:
                            bucket = self.per_drive.get(root)
                            if bucket is None:
                                bucket = {"discovered": 0, "queued": 0, "processed": 0, "failed": 0}
                                self.per_drive[root] = bucket
                            bucket["discovered"] += 1
                        
                        # Check if file needs processing
                        if self.manifest.needs_processing(record.path, record.mtime, record.size):
                            # Add to queue (blocks if full)
                            try:
                                self.processing_queue.put(record, timeout=1)
                                self.files_queued += 1
                                if root:
                                    self.per_drive[root]["queued"] += 1
                            except queue.Full:
                                # Queue saturated — file will be retried on next scan cycle.
                                # Log at warning level so it's visible; do NOT increment
                                # files_queued so the invariant queued <= discovered holds.
                                logger.warning(
                                    "Processing queue full, deferring file to next scan cycle",
                                    path=record.path,
                                )
                                
                logger.info(
                    f"Drive scan completed. Discovered {self.files_discovered} files, "
                    f"queued {self.files_queued} for processing "
                    f"(skipped {self.files_discovered - self.files_queued} unchanged)."
                )
                self.last_scan_completed = datetime.now()
                self._scan_error_backoff = 60  # reset on successful scan

                # Incremental identity clustering: assign any faces added during
                # this scan cycle to existing or new identity clusters.
                # Runs in-process after each successful full scan so the
                # Identities page in the dashboard stays current without a
                # manual cluster_faces.py run.
                self._run_incremental_clustering()

                self.is_scanning = False
                
                # Wait for next scan
                wait_time = self.config.watch_poll_interval * 60  # config unit is minutes
                for _ in range(int(wait_time)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)
                    
            except Exception as e:
                self.is_scanning = False
                # If DB is unreachable, skip the error log — the
                # connectivity guard at the top of the next iteration
                # will handle the quiet wait. Only log real application
                # errors.
                if self._scan_guard.is_healthy():
                    logger.error(f"Error in scan loop: {e}")
                    _backoff = getattr(self, "_scan_error_backoff", 60)
                    logger.info(f"Scan loop retry in {_backoff}s")
                    for _ in range(_backoff):
                        if self._stop_event.is_set():
                            break
                        time.sleep(1)
                    self._scan_error_backoff = min(_backoff * 2, 600)
                else:
                    # DB is down — next iteration's wait_for_db handles it
                    pass
                
    def _run_incremental_clustering(self) -> None:
        """Assign any unmapped faces to existing or new identity clusters.

        Called automatically after each successful full scan cycle.
        Uses the same DB engine as the rest of the manager — no subprocess.

        If no unmapped faces exist this is a fast no-op (one COUNT query).
        Errors are caught and logged; a clustering failure never aborts
        the scan loop or marks the scan as failed.
        """
        try:
            # Import inline so the clustering module doesn't have to be loaded
            # at manager startup (avoids faiss import cost on every restart
            # if clustering is unused).
            import sys
            from pathlib import Path as _Path
            # scripts/ is at /app/code/scripts/ inside the container
            # (the whole repo is bind-mounted at /app/code).
            # We try two candidate paths so this also works when running
            # directly from the repo root (e.g. during local testing).
            for _candidate in [
                _Path("//app/code/scripts"),
                _Path(__file__).resolve().parent.parent.parent / "scripts",
            ]:
                if _candidate.exists() and str(_candidate) not in sys.path:
                    sys.path.insert(0, str(_candidate))
                    break

            from sqlalchemy.orm import Session as _Session
            # Reuse the already-connected engine.
            engine = self.db.engine

            # Quick check: any unmapped faces?
            from sqlalchemy import text as _text
            with engine.connect() as conn:
                unmapped = conn.execute(
                    _text(
                        "SELECT COUNT(*) FROM faces f "
                        "LEFT JOIN face_identity_map m ON m.face_id = f.id "
                        "WHERE m.id IS NULL AND f.embedding_vec IS NOT NULL"
                    )
                ).scalar()

            if unmapped == 0:
                logger.debug("Incremental clustering: no unmapped faces, skipping.")
                return

            logger.info(f"Incremental clustering: {unmapped} unmapped faces to assign...")
            from cluster_faces import _mode_incremental
            with _Session(engine) as session:
                threshold = getattr(self.config, "identity_cluster_threshold", 0.6)
                _mode_incremental(session, threshold=threshold)
            logger.info("Incremental clustering: complete.")

        except Exception as e:
            logger.error(
                f"Incremental clustering failed (non-fatal, will retry next scan cycle): {e}"
            )

    def _recover_pending_images(self) -> None:
        """One-shot recovery for orphaned `images.status='pending'` rows.

        Three phases, all driven off SQL — does NOT re-run the detection
        pipeline against files that already have face rows. That avoids
        unique-violations on file_path and double-detection of the same image.

        Phase A (flip-to-completed):
            Pending rows with face_count > 0 are partial commits from a
            pre-P1 (caller-owned-Session) era — detection ran, faces were
            written, but the Image.status flip never landed. They are
            already ~99% done. Flip them to 'completed' so they leave
            the pending bucket.

        Phase B (outbox backfill):
            For every face row whose embedding_id is not yet present in
            the FAISS live_ids set, enqueue an outbox row so the reaper
            adds it to the index. This covers both the rows we just
            flipped in Phase A AND any pre-existing 'completed' rows
            whose embeddings were never persisted to FAISS.

        Phase C (mark-failed for empty pending):
            Pending rows with face_count=0 had no faces detected (legit)
            OR died before detection (illegit). Either way, marking them
            'failed' clears the pending bucket; the scanner will re-pick
            up any file that's still on disk on the next walk.

        The recovery only runs once per process start and is idempotent
        across restarts: a row that was flipped to completed last time
        won't be touched again.
        """
        from sqlalchemy import text

        log_prefix = "[recovery]"

        # Wait for DB before starting recovery — on boot the DB container
        # may still be initialising when this thread runs.
        guard = ConnectivityGuard(self.db.engine, poll_interval=10, label="Recovery")
        if not guard.wait_for_db(self._stop_event):
            return  # stop_event fired before DB came up

        try:
            session = self.db.SessionLocal()
        except Exception as e:
            logger.error(f"{log_prefix} cannot open recovery session: {e}")
            return

        try:
            # --- Phase A: flip partial-commit pending rows to completed.
            try:
                with self.db.engine.begin() as conn:
                    res = conn.execute(
                        text(
                            "UPDATE images SET status='completed' "
                            " WHERE status='pending' AND face_count > 0"
                        )
                    )
                    flipped = res.rowcount or 0
                logger.info(f"{log_prefix} phaseA: flipped {flipped} pending->completed (had faces)")
            except Exception as e:
                logger.error(f"{log_prefix} phaseA failed: {e}")
                flipped = 0

            # --- Phase B: backfill outbox for faces missing from FAISS.
            # Use ORM to leverage pgvector type adapter; raw SQL on
            # `embedding_vec` would return text needing manual parsing.
            try:
                import numpy as np

                from src.face.storage.database import Face
                from src.face.storage.outbox import serialize_embedding

                live = set()
                if (
                    self.processor is not None
                    and getattr(self.processor, "faiss_index", None) is not None
                    and hasattr(self.processor.faiss_index, "live_ids_set")
                ):
                    fi = self.processor.faiss_index
                    # Take snapshot under merge_lock so we don't read a partially
                    # updated set while a merge is publishing new IDs.
                    with fi.merge_lock:
                        live = set(fi.live_ids_set)

                BATCH = 500
                last_id = 0
                enqueued = 0
                while not self._stop_event.is_set():
                    rows = (
                        session.query(Face.id, Face.embedding_id, Face.embedding_vec)
                        .filter(Face.id > last_id)
                        .filter(Face.embedding_id.isnot(None))
                        .filter(Face.embedding_vec.isnot(None))
                        .order_by(Face.id)
                        .limit(BATCH)
                        .all()
                    )
                    if not rows:
                        break
                    last_id = rows[-1].id

                    # Bulk-insert outbox rows; ON CONFLICT DO NOTHING covers
                    # the case where Phase B is re-run.
                    with self.db.engine.begin() as conn:
                        for r in rows:
                            if r.embedding_id in live:
                                continue
                            try:
                                vec = np.asarray(r.embedding_vec, dtype=np.float32)
                                if vec.shape != (512,):
                                    logger.warning(
                                        f"{log_prefix} phaseB skip face_id={r.embedding_id}: "
                                        f"unexpected shape {vec.shape}"
                                    )
                                    continue
                                emb_bytes = serialize_embedding(vec)
                                conn.execute(
                                    text(
                                        "INSERT INTO faiss_outbox "
                                        "  (face_id, embedding, status, attempts, created_at) "
                                        "VALUES (:fid, :emb, 'pending', 0, NOW()) "
                                        "ON CONFLICT (face_id) DO NOTHING"
                                    ),
                                    {"fid": r.embedding_id, "emb": emb_bytes},
                                )
                                enqueued += 1
                            except Exception as ie:
                                logger.warning(
                                    f"{log_prefix} phaseB skip face_id={r.embedding_id}: {ie}"
                                )

                    # Periodic progress for the long backfill.
                    if last_id % (BATCH * 10) < BATCH:
                        logger.info(f"{log_prefix} phaseB progress: enqueued={enqueued}")

                logger.info(f"{log_prefix} phaseB: enqueued {enqueued} outbox rows")
            except Exception as e:
                logger.error(f"{log_prefix} phaseB failed: {e}")

            # --- Phase C: mark empty pending rows failed so they leave the bucket.
            try:
                with self.db.engine.begin() as conn:
                    res = conn.execute(
                        text(
                            "UPDATE images "
                            "   SET status='failed', "
                            "       error_message='recovery: pending with no faces, never started' "
                            " WHERE status='pending' AND face_count = 0"
                        )
                    )
                    cleared = res.rowcount or 0
                logger.info(f"{log_prefix} phaseC: marked {cleared} empty pending rows failed")
            except Exception as e:
                logger.error(f"{log_prefix} phaseC failed: {e}")

            logger.info(f"{log_prefix} done")
        except Exception as e:
            logger.error(f"{log_prefix} aborted: {e}")
        finally:
            session.close()

    def _worker_loop(self) -> None:
        """Worker thread loop to process files from the queue.

        Each file gets a fresh DB Session opened from the engine pool and
        closed in finally. This is the per-file transaction boundary —
        commit/rollback decisions live inside `processor.process_file`.
        """
        while not self._stop_event.is_set():
            try:
                # Get file from queue
                try:
                    record = self.processing_queue.get(timeout=1)
                except queue.Empty:
                    continue

                # Gate on DB health before processing — if DB dropped while
                # we were waiting on the queue, pause quietly until it's back.
                if not self._worker_guard.wait_for_db(self._stop_event):
                    break  # stop_event fired

                # Anchor "processing started" time for rate calculation.
                # First-pull-wins; never resets (rolling-rate cumulative
                # across the lifetime of this manager instance).
                if self._processing_anchor_time is None:
                    self._processing_anchor_time = datetime.now()

                self.current_file = record.path

                session = self.db.SessionLocal()
                try:
                    result = self.processor.process_file(Path(record.path), session=session)
                finally:
                    session.close()

                if result.status == "success":
                    self.files_processed += 1
                    # Per-drive processed counter (1A: track by source).
                    root = getattr(record, "source_root", "") or ""
                    if root and root in self.per_drive:
                        self.per_drive[root]["processed"] = (
                            self.per_drive[root].get("processed", 0) + 1
                        )
                    # Update manifest
                    self.manifest.add_file(
                        record.path,
                        result.file_hash,
                        record.size,
                        record.mtime,
                        is_processed=True
                    )
                    self.manifest.save_manifest()
                else:
                    self.files_failed += 1
                    # Per-drive failed counter (1A: track by source).
                    root = getattr(record, "source_root", "") or ""
                    if root and root in self.per_drive:
                        self.per_drive[root]["failed"] = (
                            self.per_drive[root].get("failed", 0) + 1
                        )
                    logger.warning(f"Failed to process {record.path}: {result.error_message}")

                self.processing_queue.task_done()
                self.current_file = None

            except Exception as e:
                logger.error(f"Error in worker thread: {e}")
                time.sleep(1)
                
    def _on_file_event(self, file_path: str, event_type: str) -> None:
        """Callback for file system events."""
        logger.info(f"File event: {event_type} - {file_path}")
        
        if event_type == "deleted":
            self.manifest.mark_deleted(file_path)
            self.manifest.save_manifest()
        else:
            # For created/modified, check if it needs processing
            try:
                p = Path(file_path)
                if not p.exists():
                    return
                    
                stat = p.stat()
                if self.manifest.needs_processing(file_path, stat.st_mtime, stat.st_size):
                    # Derive source_root from path so per-drive counters
                    # reflect watcher activity, not just full scans. Match
                    # the longest configured root that the file lives under.
                    source_root = ""
                    try:
                        for src in (self.config.drive_sources or []):
                            root = src.get("path") if isinstance(src, dict) else getattr(src, "path", None)
                            if root and file_path.startswith(str(root)):
                                if len(str(root)) > len(source_root):
                                    source_root = str(root)
                    except Exception:
                        source_root = ""

                    record = FileRecord(
                        path=file_path,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        extension=p.suffix.lower(),
                        source_root=source_root,
                    )
                    # Try to add to queue without blocking too long
                    try:
                        self.processing_queue.put(record, timeout=0.1)
                        # Watcher events count toward both discovered AND
                        # queued (they passed the manifest dedup check) -
                        # this keeps the invariant queued <= discovered
                        # honest for realtime activity.
                        self.files_discovered += 1
                        self.files_queued += 1
                        # Bump per-drive counters so dashboard reflects
                        # realtime watcher events, not just batched scans.
                        if source_root:
                            bucket = self.per_drive.get(source_root)
                            if bucket is None:
                                bucket = {"discovered": 0, "queued": 0,
                                          "processed": 0, "failed": 0}
                                self.per_drive[source_root] = bucket
                            bucket["discovered"] = bucket.get("discovered", 0) + 1
                            bucket["queued"] = bucket.get("queued", 0) + 1
                    except queue.Full:
                        logger.warning(f"Queue full, skipping real-time event for {file_path}")
            except Exception as e:
                logger.error(f"Error handling file event: {e}")
