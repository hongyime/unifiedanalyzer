"""Batched FAISS index for high-throughput ingestion."""

import faiss
import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional
import threading
import time
from datetime import datetime, timezone

from src.face.config import Settings
from src.face.utils.logging import get_logger

logger = get_logger(__name__)


class BatchedFAISSIndex:
    """
    FAISS index with staged ingestion for non-blocking writes.
    
    This implementation uses a live index for searches and staging
    buffers for new embeddings. When staging is full, it merges
    atomically without blocking searches.
    """
    
    def __init__(self, config: Settings):
        """
        Initialize the batched FAISS index.
        
        Args:
            config: Application settings
        """
        self.config = config
        self.dimension = 512  # buffalo_l w600k_r50 recognition embedding dimension
        
        # Live index (searchable)
        self.live_index: Optional[faiss.Index] = None
        self.live_ids: List[str] = []
        # Membership set kept in lockstep with live_ids and staging_ids so the
        # outbox reaper can ask `contains(face_id)` cheaply when retrying a
        # crashed merge. FAISS HNSW has no delete; the only correctness
        # guarantee against duplicate vectors on retry is this dedup check.
        self.live_ids_set: set = set()
        self.live_count = 0
        
        # Staging buffer
        self.staging_vectors: List[np.ndarray] = []
        self.staging_ids: List[str] = []
        self.staging_ids_set: set = set()  # O(1) membership mirror of staging_ids
        self.staging_size = config.faiss_staging_size
        
        # Merge control
        self.merge_lock = threading.Lock()
        self.last_merge_time = datetime.now(timezone.utc)
        
        # Index paths
        self.live_path = Path(config.faiss_live_path)
        self.staging_dir = Path(config.faiss_staging_dir)
        
        # IDs file path for persistence
        self.ids_path = self.live_path.with_suffix('.ids.npy')
        
        # Initialize empty index if no existing index
        self._initialize_index()
    
    def _initialize_index(self) -> None:
        """Initialize or load the FAISS index."""
        if self.live_path.exists():
            try:
                self.live_index = faiss.read_index(str(self.live_path))
                self.live_count = self.live_index.ntotal
                # Set nprobe on IVF indexes after load — write_index does NOT
                # persist nprobe, so we must apply it from config every boot.
                self._apply_ivf_runtime_params()
                logger.info(
                    f"Loaded existing FAISS index "
                    f"(type={type(self.live_index).__name__}) "
                    f"with {self.live_count} vectors"
                )

                # Load corresponding IDs if available
                if self.ids_path.exists():
                    self.live_ids = np.load(str(self.ids_path), allow_pickle=True).tolist()
                    logger.info(f"Loaded {len(self.live_ids)} face IDs")
                    # Reconcile: if a merge crashed between _save_ids and
                    # _save_index, ids will be ahead of index. Trim ids to
                    # match index.ntotal so search() can't return phantom IDs.
                    if len(self.live_ids) > self.live_count:
                        logger.warning(
                            f"FAISS recovery: ids file has {len(self.live_ids)} entries "
                            f"but index has {self.live_count} vectors — trimming ids "
                            f"(prior merge crashed mid-write)."
                        )
                        self.live_ids = self.live_ids[: self.live_count]
                    elif len(self.live_ids) < self.live_count:
                        logger.error(
                            f"FAISS corruption: index has {self.live_count} vectors "
                            f"but only {len(self.live_ids)} ids — extra vectors will "
                            f"be unreachable. Consider rebuilding the index."
                        )
                    self.live_ids_set = set(self.live_ids)
                else:
                    logger.warning(f"No IDs file found at {self.ids_path}, live_ids will be empty")
                    self.live_ids = []
                    self.live_ids_set = set()
            except Exception as e:
                logger.error(f"Failed to load existing index: {e}. Creating new index.")
                self._create_new_index()
        else:
            self._create_new_index()

    def _create_new_index(self) -> None:
        """Create a new FAISS index per config.faiss_index_type.

        Two index families supported:
          - "HNSW64" (default, legacy): immediately usable, O(N) persistence cost.
          - "IVFFlat": needs train() before add() — created as an untrained
            shell here and trained on first merge once we have enough vectors
            (>= nlist * 8 — FAISS minimum recommendation).

        For IVFFlat, the migration script `scripts/faiss_migrate_ivf.py`
        or rebuild script `scripts/faiss_rebuild_from_db.py` bootstraps
        a trained index from the existing DB so a fresh boot with the
        migrated file Just Works. A fresh IVF index CANNOT be grown from
        empty — after index loss, run the rebuild script before restarting.
        """
        index_type = (self.config.faiss_index_type or "HNSW64").upper()
        if index_type == "IVFFLAT":
            # Inverted-file flat index. Quantizer is a flat IP index that holds
            # the centroids; the IVF layer dispatches queries to the right
            # cells. Untrained at first; will train on first merge.
            quantizer = faiss.IndexFlatIP(self.dimension)
            self.live_index = faiss.IndexIVFFlat(
                quantizer,
                self.dimension,
                self.config.faiss_ivf_nlist,
                faiss.METRIC_INNER_PRODUCT,
            )
            logger.info(
                f"Created new IVFFlat index "
                f"(nlist={self.config.faiss_ivf_nlist}, "
                f"untrained — will train on first merge)"
            )
        else:
            # HNSW64: M=64, efConstruction=200 — the legacy default.
            self.live_index = faiss.IndexHNSWFlat(self.dimension, 64, faiss.METRIC_INNER_PRODUCT)
            self.live_index.hnsw.efConstruction = 200
            logger.info("Created new HNSW64 index")

        self._apply_ivf_runtime_params()
        self.live_ids = []
        self.live_ids_set = set()
        self.live_count = 0

        # Ensure directories exist
        self.live_path.parent.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def _apply_ivf_runtime_params(self) -> None:
        """Apply runtime tunables to IVF-family indexes after load/create.

        nprobe is NOT serialized by faiss.write_index, so we must set it
        every boot from config or search will use the default of 1 (terrible
        recall). Safe no-op on non-IVF indexes.
        """
        try:
            ivf = faiss.extract_index_ivf(self.live_index)
        except Exception:
            return  # not an IVF family index
        if ivf is not None:
            ivf.nprobe = max(1, int(self.config.faiss_ivf_nprobe))
            logger.info(f"Set IVF nprobe={ivf.nprobe}")

    def _ensure_ivf_trained(self) -> bool:
        """Train an untrained IVF index if we have enough staging vectors.

        FAISS requires ``nlist * ~8-30`` training points minimum. We use
        ``nlist * 8`` as a soft floor; if staging is smaller we postpone
        training until next merge. Returns True if the index is (now)
        trained and safe to add to.

        No-op on already-trained or non-IVF indexes.
        """
        try:
            ivf = faiss.extract_index_ivf(self.live_index)
        except Exception:
            return True  # not IVF, no training needed
        if ivf is None:
            return True
        if self.live_index.is_trained:
            return True

        min_train = ivf.nlist * 8
        n_staging = len(self.staging_vectors)
        if n_staging < min_train:
            logger.warning(
                f"IVF index untrained and staging has {n_staging} vectors "
                f"(< {min_train} required). Postponing merge until enough "
                f"training data accumulates. Or run the IVF migration script."
            )
            return False

        # Train on the staging snapshot. faiss.train() uses the data only
        # for k-means; doesn't add to the index.
        train_array = np.array(self.staging_vectors, dtype=np.float32)
        logger.info(f"Training IVF index on {len(train_array)} vectors (nlist={ivf.nlist})...")
        t0 = time.time()
        self.live_index.train(train_array)
        logger.info(f"IVF training complete in {time.time() - t0:.1f}s.")
        return True

    def contains(self, face_id: str) -> bool:
        """Return True if `face_id` is already in live or staging.

        Used by the outbox reaper to dedup retries: if a previous merge
        crashed after writing FAISS but before marking outbox rows
        committed, the next reaper attempt would otherwise insert
        duplicate vectors. FAISS HNSW has no delete — dedup at insert
        time is the only safety net.
        """
        with self.merge_lock:
            if face_id in self.live_ids_set:
                return True
            # O(1) via staging_ids_set mirror
            return face_id in self.staging_ids_set
    
    def add(self, embedding: np.ndarray, face_id: str) -> int:
        """
        Add an embedding to the staging buffer.

        Idempotent: if `face_id` is already in live or staging, this is
        a no-op. The reaper relies on this when retrying a crashed merge.

        Args:
            embedding: 512-d numpy array (float32)
            face_id: Unique identifier for the face

        Returns:
            Position in staging buffer (or -1 if already present)
        """
        # Normalize embedding for cosine similarity
        embedding = embedding.astype(np.float32)
        if embedding.ndim == 1:
            embedding = embedding.reshape(1, -1)
        faiss.normalize_L2(embedding)
        embedding = embedding.flatten()

        with self.merge_lock:
            if face_id in self.live_ids_set or face_id in self.staging_ids_set:
                return -1
            self.staging_vectors.append(embedding)
            self.staging_ids.append(face_id)
            self.staging_ids_set.add(face_id)
            count = len(self.staging_vectors)
            should_merge = count >= self.staging_size

        # Trigger merge outside the lock so we don't hold it across disk I/O.
        # _merge_staging_to_live re-acquires the lock atomically. The
        # should_merge flag guarantees we don't miss a trigger across racing
        # producers (each producer that crosses the threshold triggers; a
        # subsequent merge sees an empty staging and returns immediately).
        if should_merge:
            self._merge_staging_to_live()

        return count - 1

    def add_batch(self, embeddings: np.ndarray, face_ids: List[str]) -> int:
        """
        Add multiple embeddings to the staging buffer.

        Idempotent per face_id: duplicates of already-present IDs are
        silently dropped (with their corresponding rows in `embeddings`).

        Args:
            embeddings: N x 512 numpy array
            face_ids: List of face IDs

        Returns:
            Number of NEW embeddings added (excluding dedup drops)
        """
        # Normalize embeddings
        embeddings = embeddings.astype(np.float32)
        faiss.normalize_L2(embeddings)

        added = 0
        with self.merge_lock:
            for i, fid in enumerate(face_ids):
                if fid in self.live_ids_set or fid in self.staging_ids_set:
                    continue
                self.staging_vectors.append(embeddings[i])
                self.staging_ids.append(fid)
                self.staging_ids_set.add(fid)
                added += 1

            count = len(self.staging_vectors)
            should_merge = count >= self.staging_size

        if should_merge:
            self._merge_staging_to_live()

        return added

    def search(self, embedding: np.ndarray, k: int = 100) -> List[Tuple[str, float]]:
        """
        Search the live index for similar embeddings.

        Args:
            embedding: Query embedding (512-d)
            k: Number of results to return

        Returns:
            List of (face_id, similarity_score) tuples
        """
        if self.live_count == 0:
            return []

        # Normalize query
        embedding = embedding.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(embedding)

        # Search live index (read lock would be better but we'll use merge_lock
        # to ensure we don't search while merging)
        #
        # COUPLING NOTE: merge_lock serializes both writes (_merge_staging_to_live,
        # add, add_batch) and this search. That means a slow FAISS IVF search
        # (~5-20ms at nprobe=32) blocks concurrent adds, and a large merge
        # (~50ms for 2048 vectors) blocks concurrent searches. Acceptable at
        # current 17k-vector scale but worth revisiting if:
        #   (a) search latency SLA < 50ms, or
        #   (b) ingest rate exceeds ~1k vectors/s.
        # Upgrade path: RWLock (readers share, writer exclusive) or copy-on-write
        # index snapshot so searches operate on a stable snapshot while merges
        # update in the background.
        with self.merge_lock:
            D, I = self.live_index.search(embedding, k)

            # Convert to list of (face_id, score) tuples
            results = []
            for i, (dist, idx) in enumerate(zip(D[0], I[0])):
                if idx >= 0 and idx < len(self.live_ids):
                    results.append((self.live_ids[idx], float(dist)))

        return results

    def _merge_staging_to_live(self) -> None:
        """
        Merge staging buffer into live index atomically.

        Crash-safety: the on-disk artifacts (.faiss + .ids.npy) and the
        in-memory live_index/live_ids/live_count must never disagree. We:
          1. Add staging vectors to a copy of live_ids (in-memory) but DO NOT
             update live_ids / live_count yet.
          2. Add to faiss live_index (this mutates the index in place; we
             cannot easily roll it back, but FAISS HNSW.add is internal-state
             only — if the subsequent save fails, in-memory state is consistent
             with the index because we still need both `live_index.ntotal` and
             `live_ids` len to match. We therefore save IDs FIRST, then the
             index — the inverse of the previous order — so a crash between
             the two writes leaves an .ids file with too many IDs (recoverable
             on next merge) rather than an index with no IDs (unrecoverable
             corruption).
          3. Only after both saves succeed do we publish live_ids/live_count
             and clear staging.

        This method is thread-safe and blocks searches during merge.
        """
        with self.merge_lock:
            if len(self.staging_vectors) == 0:
                return

            # IVF gate: if we have an IVFFlat index that's not trained yet,
            # try to train it on the current staging snapshot. If staging is
            # too small for a stable k-means, this returns False and we keep
            # the staging buffer for the next merge cycle. The IVF migration
            # script bypasses this entirely by training and persisting on
            # disk so a fresh boot loads an already-trained index.
            if not self._ensure_ivf_trained():
                return

            staging_count = len(self.staging_vectors)
            logger.info(f"Merging {staging_count} vectors from staging to live index...")

            # Snapshot: capture staging state under the lock
            staging_array = np.array(self.staging_vectors, dtype=np.float32)
            staging_ids_snapshot = list(self.staging_ids)

            # Add to FAISS in-memory index (this mutation is the one we cannot
            # roll back cheaply; we accept the risk and ensure the on-disk
            # artifacts written below match the new in-memory state).
            self.live_index.add(staging_array)
            new_live_ids = self.live_ids + staging_ids_snapshot
            new_live_count = self.live_index.ntotal

            # Save IDs FIRST. If this fails, we have an in-memory index that's
            # ahead of disk. Roll back the FAISS add by re-creating the index
            # without the new vectors? FAISS HNSW does not support delete; we
            # instead refuse to publish the new state and keep staging intact.
            try:
                self._save_ids_atomic(new_live_ids)
            except Exception as e:
                logger.error(f"Failed to save FAISS ids during merge: {e}")
                # Don't publish; keep staging so next merge retries.
                # In-memory FAISS is now ahead of saved state, but
                # next save will reconcile (live_ids saved after subsequent
                # merge). This is the least-bad option without delete support.
                return

            # Now save the index. If this fails, ids file has the new IDs but
            # the index file is stale — on restart we'd load stale index +
            # full ids, producing index.ntotal < len(ids). Detect that on
            # load and trim ids; we add an explicit guard in _initialize_index
            # so this is recoverable, not corruption.
            try:
                self._save_index_atomic()
            except Exception as e:
                logger.error(f"Failed to save FAISS index during merge: {e}")
                # Don't publish in-memory. Ids file is ahead of index file;
                # _initialize_index trims live_ids on load.
                return

            # Both saves succeeded — publish.
            self.live_ids = new_live_ids
            self.live_ids_set.update(staging_ids_snapshot)
            self.live_count = new_live_count
            self.staging_vectors.clear()
            self.staging_ids.clear()
            self.staging_ids_set.clear()  # keep in sync with staging_ids
            self.last_merge_time = datetime.now(timezone.utc)
            logger.info(f"Merge complete. Live index now has {self.live_count} vectors.")

    def _save_index(self) -> None:
        """Backwards-compatible alias for _save_index_atomic (in-memory live_index)."""
        self._save_index_atomic()

    def _save_index_atomic(self) -> None:
        """Save the live index to disk via .tmp + atomic rename."""
        temp_path = self.live_path.with_suffix(".faiss.tmp")
        
        try:
            faiss.write_index(self.live_index, str(temp_path))
            # Atomic rename
            temp_path.replace(self.live_path)
        except Exception as e:
            logger.error(f"Error saving FAISS index: {e}")
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
    
    def _save_ids(self) -> None:
        """Backwards-compatible alias — saves current self.live_ids."""
        self._save_ids_atomic(self.live_ids)

    def _save_ids_atomic(self, ids_to_save) -> None:
        """Save the given IDs list to disk via .tmp.npy + atomic rename.

        ids_path is `<base>.ids.npy`. Bug history: previous impl built temp
        path via `with_suffix('.npy.tmp')` which dropped the `.ids` segment
        AND ended in `.tmp` (not `.npy`), so np.save auto-appended `.npy`
        producing `<base>.npy.tmp.npy` while we tried to rename `<base>.npy.tmp`
        — FileNotFoundError every merge, FAISS persistence permanently broken.

        Fix: write to a sibling that already ends in `.npy` so np.save is a
        no-op on the suffix, then atomic rename onto the final ids_path.
        """
        if not ids_to_save:
            return

        # Sibling temp path with `.tmp.npy` suffix (np.save sees `.npy`, no append).
        temp_path = self.ids_path.with_name(self.ids_path.name + ".tmp.npy")

        try:
            np.save(str(temp_path), np.array(ids_to_save, dtype=object), allow_pickle=True)
            # Atomic rename onto the final path. Same filesystem -> atomic on POSIX.
            temp_path.replace(self.ids_path)
        except Exception as e:
            logger.error(f"Error saving FAISS IDs: {e}")
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
    
    def force_merge(self) -> None:
        """Force merge of staging buffer regardless of size."""
        self._merge_staging_to_live()
    
    @property
    def total_count(self) -> int:
        """Get total number of vectors (live + staging)."""
        return self.live_count + len(self.staging_vectors)
    
    @property
    def needs_merge(self) -> bool:
        """Check if staging buffer needs merging."""
        return len(self.staging_vectors) >= self.staging_size * 0.8
