"""FAISS write-ahead outbox + reaper (Phase 2 of audit redesign).

Decouples the indexing pipeline from FAISS by routing every face write
through a Postgres outbox table. The pipeline writes Image + Face + the
outbox row in one transaction; a separate reaper thread drains pending
rows into FAISS asynchronously.

Failure-mode invariants:

  * The pipeline never touches FAISS directly — pipeline crash cannot
    leave FAISS rows without DB rows.
  * Reaper crash mid-FAISS-merge is safe because:
      - on retry, BatchedFAISSIndex.contains() / .add() dedup by face_id,
      - merge writes ids file then index file atomically,
      - rows stuck in `merging` for >stuck_timeout_s are reclaimed.
  * After max_attempts exceeded, rows are parked as `failed` and surfaced
    via `count_by_status()` for ops triage.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey, Index, Integer,
    LargeBinary, String, Text, text,
)
from sqlalchemy.orm import Session

from src.face.storage.database import Base, Database
from src.face.storage.faiss_index import BatchedFAISSIndex
from src.face.utils.connectivity import ConnectivityGuard


logger = logging.getLogger(__name__)


# --- Model -------------------------------------------------------------------


class FaissOutbox(Base):
    """Pending FAISS writes (outbox pattern).

    Lifecycle:
        pending  -> claimed by reaper -> merging  -> committed (terminal)
                                                  -> pending (retry)
                                                  -> failed (terminal, attempts >= max)
    """
    __tablename__ = "faiss_outbox"

    id = Column(BigInteger, primary_key=True)
    face_id = Column(
        String(64),
        ForeignKey("faces.embedding_id"),
        unique=True,
        nullable=False,
        index=True,
    )
    embedding = Column(LargeBinary, nullable=False)  # raw float32[512] = 2048 bytes
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    claimed_at = Column(DateTime)
    committed_at = Column(DateTime)

    __table_args__ = (
        Index("ix_faiss_outbox_status_created", "status", "created_at"),
        Index("ix_faiss_outbox_status_claimed", "status", "claimed_at"),
    )


# --- Helpers -----------------------------------------------------------------


def serialize_embedding(emb: np.ndarray) -> bytes:
    """Serialize a 512-d float32 vector to 2048 raw bytes."""
    arr = np.asarray(emb, dtype=np.float32).reshape(-1)
    if arr.shape[0] != 512:
        raise ValueError(f"expected 512-d embedding, got shape {arr.shape}")
    return arr.tobytes()


def deserialize_embedding(b: bytes) -> np.ndarray:
    """Deserialize 2048 raw bytes back into a 512-d float32 vector."""
    arr = np.frombuffer(b, dtype=np.float32)
    if arr.shape[0] != 512:
        raise ValueError(f"expected 512 floats from {len(b)} bytes, got {arr.shape}")
    return arr


# --- Reaper ------------------------------------------------------------------


class FaissReaper:
    """Background drain from `faiss_outbox` into BatchedFAISSIndex.

    Runs as a daemon thread. Multiple reapers can run concurrently against
    the same database thanks to `FOR UPDATE SKIP LOCKED`; in practice one
    is enough because FAISS's `merge_lock` serialises the actual writes.
    """

    def __init__(
        self,
        database: Database,
        faiss_index: BatchedFAISSIndex,
        poll_interval_ms: int = 500,
        batch_size: int = 256,
        stuck_timeout_s: int = 120,
        max_attempts: int = 5,
    ):
        self.database = database
        self.faiss_index = faiss_index
        self.poll_interval_s = poll_interval_ms / 1000.0
        self.batch_size = batch_size
        self.stuck_timeout_s = stuck_timeout_s
        self.max_attempts = max_attempts

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._guard = ConnectivityGuard(
            database.engine, poll_interval=15, label="FaissReaper"
        )

    # --- lifecycle ---

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("FaissReaper already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="faiss-reaper", daemon=True
        )
        self._thread.start()
        logger.info(
            "FaissReaper started (poll=%.2fs, batch=%d, stuck_timeout=%ds, max_attempts=%d)",
            self.poll_interval_s, self.batch_size, self.stuck_timeout_s, self.max_attempts,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("FaissReaper did not stop within %.1fs", timeout)
        logger.info("FaissReaper stopped")

    # --- main loop ---

    def _run(self) -> None:
        tick = 0
        while not self._stop.is_set():
            # Gate on DB health — if postgres is down (machine boot, network
            # loss), block quietly here until it's back. Logs once on drop
            # and once on recovery; no spam in between.
            if not self._guard.wait_for_db(self._stop):
                break  # stop event fired
            try:
                self._reclaim_stuck()
                drained = self._drain_once()
                tick += 1
                if tick % 100 == 0:
                    self._prune_committed()
                if drained == 0:
                    self._stop.wait(self.poll_interval_s)
            except Exception:  # noqa: BLE001
                logger.exception("FaissReaper tick failed; sleeping before retry")
                self._stop.wait(self.poll_interval_s)

    # --- stuck-row reclaim ---

    def _reclaim_stuck(self) -> int:
        """Move rows stuck in `merging` for >stuck_timeout_s back to pending.

        A reaper that crashes after claiming but before committing will
        leave rows orphaned in `merging`. Without this they'd stay stuck
        forever.
        """
        session = self.database.SessionLocal()
        try:
            result = session.execute(
                text(
                    """
                    UPDATE faiss_outbox
                       SET status = 'pending'
                     WHERE status = 'merging'
                       AND claimed_at < NOW() - (:timeout_s * INTERVAL '1 second')
                    """
                ),
                {"timeout_s": self.stuck_timeout_s},
            )
            session.commit()
            n = result.rowcount or 0
            if n:
                logger.warning("FaissReaper reclaimed %d stuck 'merging' rows", n)
            return n
        finally:
            session.close()

    # --- one drain cycle ---

    def _drain_once(self) -> int:
        """Claim a batch, push to FAISS, mark committed. Returns rows drained."""
        session = self.database.SessionLocal()
        try:
            # 1. Claim batch atomically — SKIP LOCKED so multiple reapers are safe.
            rows = session.execute(
                text(
                    """
                    SELECT id, face_id, embedding, attempts
                      FROM faiss_outbox
                     WHERE status = 'pending'
                       AND attempts < :max_attempts
                     ORDER BY created_at
                     LIMIT :batch
                     FOR UPDATE SKIP LOCKED
                    """
                ),
                {"batch": self.batch_size, "max_attempts": self.max_attempts},
            ).fetchall()

            if not rows:
                session.commit()  # release any held locks
                return 0

            ids = [r.id for r in rows]
            session.execute(
                text(
                    """
                    UPDATE faiss_outbox
                       SET status = 'merging',
                           claimed_at = NOW(),
                           attempts = attempts + 1
                     WHERE id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
            session.commit()

        except Exception:
            session.rollback()
            session.close()
            raise

        # 2. Push to FAISS outside the claim transaction. add() is idempotent
        #    (skips face_ids already present) so a previous half-finished
        #    attempt's vectors are not duplicated.
        try:
            for r in rows:
                emb = deserialize_embedding(bytes(r.embedding))
                self.faiss_index.add(emb, r.face_id)
            # Force a merge so the rows we just claimed are durably in FAISS
            # before we mark them committed. force_merge writes ids before
            # index, atomically, and is itself recoverable on crash.
            self.faiss_index.force_merge()

        except Exception as exc:  # noqa: BLE001
            err = repr(exc)[:500]
            logger.exception("FaissReaper merge failed for %d rows", len(rows))
            try:
                session.execute(
                    text(
                        """
                        UPDATE faiss_outbox
                           SET status = CASE
                                          WHEN attempts >= :max_attempts THEN 'failed'
                                          ELSE 'pending'
                                        END,
                               last_error = :err
                         WHERE id = ANY(:ids)
                        """
                    ),
                    {"ids": ids, "err": err, "max_attempts": self.max_attempts},
                )
                session.commit()
            finally:
                session.close()
            return 0

        # 3. Mark committed.
        try:
            session.execute(
                text(
                    """
                    UPDATE faiss_outbox
                       SET status = 'committed',
                           committed_at = NOW(),
                           last_error = NULL
                     WHERE id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
            session.commit()
        finally:
            session.close()

        logger.info("FaissReaper committed %d rows", len(rows))
        return len(rows)

    # --- retention ---

    def _prune_committed(self) -> int:
        """Delete committed outbox rows older than retention_days.

        Each committed row carries a redundant 2KB embedding blob already
        stored in faces.embedding_vec. At 17k faces that's ~35MB of dead
        weight; it grows linearly and the rows serve no purpose after
        commitment. Only deletes status='committed'; never touches
        pending/merging/failed.
        """
        RETENTION_DAYS = 7
        session = self.database.SessionLocal()
        try:
            result = session.execute(
                text(
                    """
                    DELETE FROM faiss_outbox
                     WHERE status = 'committed'
                       AND committed_at < NOW() - (:days * INTERVAL '1 day')
                    """
                ),
                {"days": RETENTION_DAYS},
            )
            session.commit()
            n = result.rowcount or 0
            if n:
                logger.info("FaissReaper pruned %d committed outbox rows (>%dd old)", n, RETENTION_DAYS)
            return n
        except Exception:
            session.rollback()
            logger.exception("FaissReaper prune failed")
            return 0
        finally:
            session.close()

    # --- ops introspection ---

    def count_by_status(self) -> dict:
        """Return {status: count} across the outbox. Cheap, useful for /health."""
        session = self.database.SessionLocal()
        try:
            rows = session.execute(
                text("SELECT status, COUNT(*) AS n FROM faiss_outbox GROUP BY status")
            ).fetchall()
            return {r.status: r.n for r in rows}
        finally:
            session.close()


# --- Pipeline-side helper ----------------------------------------------------


def enqueue_face(session: Session, face_id: str, embedding: np.ndarray) -> None:
    """Insert a row into `faiss_outbox` for the given face.

    Caller passes the same Session it used to insert the Face row, so the
    outbox row participates in the same transaction. If anything in the
    surrounding tx aborts, this row never lands — preserving the invariant
    that every outbox row references an actual face row.
    """
    row = FaissOutbox(
        face_id=face_id,
        embedding=serialize_embedding(embedding),
        status="pending",
        attempts=0,
    )
    session.add(row)
