"""Main pipeline processor for face indexing."""

import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import threading
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.face.utils.logging import get_logger
from src.face.readers.image_reader import ImageReader
from src.face.readers.video_reader import VideoReader
from src.face.engine.detector import FaceDetector, FaceDetectionResult
from src.face.engine.tracker import FaceTracker, TrackedFace
from src.face.engine.quality import QualityScorer
from src.face.pipeline.thumbnail import ThumbnailGenerator
from src.face.storage.database import Database, Image, Face
from src.face.storage.faiss_index import BatchedFAISSIndex
from src.face.storage.outbox import enqueue_face
from src.face.discovery.onedrive import OneDriveHandler
from src.face.config import settings

logger = get_logger(__name__)


def _bbox_iou(a, b) -> float:
    """IoU between two bounding boxes given as [x1, y1, x2, y2]."""
    ax1, ay1, ax2, ay2 = float(a[0]), float(a[1]), float(a[2]), float(a[3])
    bx1, by1, bx2, by2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    a_area = max(0.0, (ax2 - ax1)) * max(0.0, (ay2 - ay1))
    b_area = max(0.0, (bx2 - bx1)) * max(0.0, (by2 - by1))
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


def _match_detection_to_tracked(detections, tracked_bbox, min_iou: float = 0.3):
    """Return the detection with highest IoU vs `tracked_bbox`, or None."""
    best = None
    best_iou = min_iou
    for det in detections:
        iou = _bbox_iou(det.bbox, tracked_bbox)
        if iou > best_iou:
            best_iou = iou
            best = det
    return best


class ProcessingResult:
    """Result of processing a single file."""

    def __init__(self):
        self.file_path: Optional[Path] = None
        self.file_hash: Optional[str] = None
        self.status: str = "pending"  # pending, processing, success, failed
        self.faces_detected: int = 0
        self.faces_processed: int = 0
        self.face_objects: List[Tuple[Face, np.ndarray]] = []
        self.error_message: Optional[str] = None
        self.processing_time: float = 0.0
        self.is_video: bool = False
        self.video_frames_processed: int = 0
        self.track_count: int = 0
        # Source media dimensions (populated by _process_image / _process_video).
        # Stored on the Image record so downstream code can reason about
        # aspect ratio, normalized bboxes for new readers, etc.
        self.width: int = 0
        self.height: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "file_path": str(self.file_path),
            "file_hash": self.file_hash,
            "status": self.status,
            "faces_detected": self.faces_detected,
            "faces_processed": self.faces_processed,
            "error_message": self.error_message,
            "processing_time": self.processing_time,
            "is_video": self.is_video,
            "video_frames_processed": self.video_frames_processed,
            "track_count": self.track_count
        }


class PipelineProcessor:
    """Orchestrate the complete face processing pipeline.

    The pipeline does NOT touch FAISS directly. Each file produces a single
    Postgres transaction containing: 1 Image row, N Face rows, and N
    `faiss_outbox` rows. A separate FaissReaper thread drains the outbox
    into FAISS asynchronously. This decouples ingest from FAISS write
    latency and eliminates the "Postgres committed but FAISS missing"
    failure window.
    """

    def __init__(
        self,
        db: Database,
        faiss_index: BatchedFAISSIndex,
        thumbnail_cache_path: Path,
        use_onedrive: bool = True,
        providers: List[str] = None
    ):
        """
        Initialize pipeline processor.

        Args:
            db: Database (engine + SessionLocal factory). Pipeline opens a
                fresh Session per file via `db.SessionLocal()`.
            faiss_index: Batched FAISS index — held only for read-side
                idempotency checks during enqueue. Writes go through the
                outbox.
            thumbnail_cache_path: Base path for thumbnail storage.
            use_onedrive: Enable OneDrive handling.
            providers: ONNX runtime providers.
        """
        logger.info("Initializing PipelineProcessor")

        self.db = db
        self.faiss_index = faiss_index
        self.thumbnail_cache_path = thumbnail_cache_path
        
        # Initialize components
        self.image_reader = ImageReader()
        self.video_reader = VideoReader(fps=1.0)  # 1 FPS for video
        self.face_detector = FaceDetector(providers=providers)
        self.face_tracker = FaceTracker(max_age=30, n_init=1)
        self.quality_scorer = QualityScorer()
        self.thumbnail_generator = ThumbnailGenerator(
            face_size=512,
            margin_ratio=0.15
        )
        
        self.onedrive = OneDriveHandler(settings) if use_onedrive else None
        
        # Ensure thumbnail cache directory exists
        self.thumbnail_cache_path.mkdir(parents=True, exist_ok=True)

        # Serialize video processing across worker threads. Unlike image
        # processing (which is naturally safe per-call), video processing
        # mutates self.face_tracker state via reset()/update() across many
        # frames in one call. Two workers processing two videos concurrently
        # would corrupt DeepSORT's frame_count / active_tracks dict and
        # produce wrong track IDs. Image processing does NOT use the
        # tracker, so it stays parallel.
        self._video_lock = threading.Lock()

        logger.info("PipelineProcessor initialized")

    def _check_storage_alive(self) -> bool:
        """Check if critical storage root is still available."""
        # Use thumbnail_cache_path as proxy for storage root availability
        # In a real environment, this is Y:\facetracker\faces
        if not self.thumbnail_cache_path.parent.exists():
            logger.error(f"CRITICAL: Storage root {self.thumbnail_cache_path.parent} not found! Pausing operations.")
            return False
        return True

    def process_file(self, file_path: Path, session: Optional[Session] = None) -> ProcessingResult:
        """
        Process a single image or video file.

        The DB session is owned by the CALLER. Pass an open Session and the
        pipeline will commit (or rollback) it before returning. If no
        session is provided we open one ourselves — convenient for tests
        and one-shot scripts, NOT for the indexing manager which should
        own session lifecycle.

        Args:
            file_path: Path to file to process.
            session: SQLAlchemy Session (caller-owned). If None, a session
                is opened internally and closed in finally.

        Returns:
            ProcessingResult with all metadata and face records.
        """
        owns_session = session is None
        if owns_session:
            session = self.db.SessionLocal()

        try:
            return self._process_file_impl(file_path, session)
        finally:
            if owns_session:
                session.close()

    def _process_file_impl(self, file_path: Path, session: Session) -> ProcessingResult:
        # STOP if storage root is missing (e.g. Y:\ disconnected)
        if not self._check_storage_alive():
            result = ProcessingResult()
            result.file_path = file_path
            result.status = "failed"
            result.error_message = "Storage root unavailable"
            return result

        import time
        start_time = time.time()

        result = ProcessingResult()
        result.file_path = file_path
        result.status = "processing"

        logger.info(f"Processing file: {file_path}")

        onedrive_original_path = None
        should_revert = False

        try:
            # Handle OneDrive placeholders: download to temp, process, then revert
            if self.onedrive:
                local_path, should_revert = self.onedrive.process_file(str(file_path))
                if local_path is None:
                    result.status = "failed"
                    result.error_message = "Failed to download OneDrive file"
                    result.processing_time = time.time() - start_time
                    return result
                if should_revert:
                    logger.info(f"OneDrive placeholder downloaded: {file_path} -> {local_path}")
                    onedrive_original_path = str(file_path)
                file_path = Path(local_path)

            # Compute file hash
            from src.face.utils.hashing import compute_file_hash
            result.file_hash = compute_file_hash(file_path)

            # Check if already processed (uses caller's session)
            existing = (
                session.query(Image).filter(Image.file_hash == result.file_hash).first()
            )
            if existing and existing.status == "completed":
                logger.info(f"File already processed: {file_path}")
                result.status = "success"
                result.processing_time = time.time() - start_time
                return result

            # Determine file type and process
            if self.video_reader.can_read(file_path):
                self._process_video(file_path, result)
            elif self.image_reader.can_read(file_path):
                self._process_image(file_path, result)
            else:
                result.status = "failed"
                result.error_message = f"Unsupported file type: {file_path.suffix}"

        except Exception as e:
            logger.error(f"Processing failed for {file_path}: {e}")
            result.status = "failed"
            result.error_message = str(e)
        finally:
            # Revert OneDrive file back to online-only after processing
            if should_revert and onedrive_original_path and self.onedrive:
                self.onedrive.revert_to_online_only(onedrive_original_path)

        result.processing_time = time.time() - start_time

        # Save image record (and outbox rows) atomically in caller's session
        if result.status == "success":
            self._save_image_record(session, result)

        logger.info(
            f"Completed processing {file_path}: "
            f"{result.faces_processed} faces in {result.processing_time:.2f}s"
        )

        return result

    def _process_image(self, file_path: Path, result: ProcessingResult):
        """Process a single image file."""
        # Load image
        image = self.image_reader.read(file_path)
        if image is None:
            result.status = "failed"
            result.error_message = "Failed to load image"
            return
        
        # Detect faces
        detections = self.face_detector.detect(image)
        result.faces_detected = len(detections)
        
        height, width = image.shape[:2]
        result.width = int(width)
        result.height = int(height)
        
        if not detections:
            logger.debug(f"No faces detected in {file_path}")
            result.status = "success"
            return
        
        # Process each face
        for detection in detections:
            face_obj = self._process_face(
                image=image,
                bbox=detection.bbox,
                quality_score=detection.quality_score,
                source_path=file_path,
                file_hash=result.file_hash,
                embedding=detection.embedding,
                frame_number=0,
                img_width=width,
                img_height=height
            )
            
            if face_obj:
                result.face_objects.append(face_obj)
                result.faces_processed += 1
        
        result.status = "success"

    def _process_video(self, file_path: Path, result: ProcessingResult):
        """Process a video file with tracking at 1 FPS.

        Acquires self._video_lock for the duration. With INDEX_WORKERS>1,
        two workers could otherwise enter this method concurrently and
        corrupt the shared face_tracker state (frame_count, active_tracks)
        because face_tracker.reset() resets it to zero on entry. Image
        processing does NOT use the tracker, so it stays parallel.
        """
        result.is_video = True

        with self._video_lock:
            self._process_video_locked(file_path, result)

    def _process_video_locked(self, file_path: Path, result: ProcessingResult):
        """Locked body of _process_video. See _process_video for rationale.

        For each track, we keep the BEST-quality frame (by detector quality
        score, matched to the tracker bbox via IoU). On finalization we
        re-detect on that best frame and pull the embedding from the
        detection that overlaps the track - never blindly index 0.
        """
        # Reset tracker for new video
        self.face_tracker.reset()
        
        # Track best frame per track ID: (frame, bbox, quality)
        best_frames: Dict[int, Tuple[np.ndarray, np.ndarray, float]] = {}
        
        # Process frames
        frame_count = 0
        for frame, timestamp in self.video_reader.read_frames(file_path):
            # Capture media dimensions from the first frame we see.
            if frame_count == 0:
                fh, fw = frame.shape[:2]
                result.width = int(fw)
                result.height = int(fh)
            # Detect faces in frame
            detections = self.face_detector.detect(frame)
            
            if detections:
                # Format for tracker
                det_list = [(d.bbox, d.confidence) for d in detections]
                
                # Update tracker
                tracked_faces = self.face_tracker.update(det_list, frame_count, timestamp)
                
                # Store best frame per track — match tracker bbox to detection
                # by IoU and use the detector's actual quality_score, not a
                # hardcoded constant. If no detection overlaps the tracked
                # box, skip this frame for this track (the tracker may be
                # extrapolating; we don't want to commit a track that the
                # detector doesn't see).
                for tracked in tracked_faces:
                    matched_det = _match_detection_to_tracked(detections, tracked.bbox)
                    if matched_det is None:
                        continue
                    quality = float(matched_det.quality_score)
                    track_id = tracked.track_id
                    if track_id not in best_frames or quality > best_frames[track_id][2]:
                        best_frames[track_id] = (frame.copy(), tracked.bbox.copy(), quality)
            
            frame_count += 1
        
        result.video_frames_processed = frame_count
        result.track_count = len(best_frames)
        
        # Process best frame for each track. Re-detect on the saved best
        # frame and pull the embedding from the detection whose bbox best
        # overlaps the track's bbox — using detections[0] blindly causes
        # cross-identity contamination when the frame has multiple faces.
        for track_id, (frame, bbox, quality) in best_frames.items():
            h, w = frame.shape[:2]
            
            best_detections = self.face_detector.detect(frame)
            matched_det = _match_detection_to_tracked(best_detections, bbox)
            if matched_det is None:
                logger.warning(
                    f"Skipping track {track_id} in {file_path}: "
                    f"no detection overlaps tracker bbox on best frame "
                    f"(detections={len(best_detections)})"
                )
                continue
            best_emb = matched_det.embedding

            face_obj = self._process_face(
                image=frame,
                bbox=bbox,
                quality_score=quality,
                source_path=file_path,
                file_hash=result.file_hash,
                embedding=best_emb,
                frame_number=-1,  # Best frame from video
                track_id=track_id,
                video_path=file_path,
                img_width=w,
                img_height=h
            )
            
            if face_obj:
                result.face_objects.append(face_obj)
                result.faces_processed += 1
        
        result.status = "success"

    def _process_face(
        self,
        image: np.ndarray,
        bbox: np.ndarray,
        quality_score: float,
        source_path: Path,
        file_hash: str,
        img_width: int,
        img_height: int,
        embedding: Optional[np.ndarray] = None,
        frame_number: int = 0,
        track_id: Optional[int] = None,
        video_path: Optional[Path] = None
    ) -> Optional[Tuple[Face, np.ndarray]]:
        """Process a single face detection.

        Returns the unsaved Face object plus its embedding. The embedding
        rides alongside until `_save_image_record` writes both the Face
        row and the matching `faiss_outbox` row in one transaction. The
        pipeline no longer calls `faiss_index.add()` directly — the
        outbox reaper owns FAISS writes.
        """
        try:
            if embedding is None:
                logger.warning("No embedding provided for face")
                return None

            x1, y1, x2, y2 = bbox.astype(int)

            # Generate unique ID for face
            face_id = uuid.uuid4().hex

            # Generate thumbnail
            thumbnail_filename = f"{face_id}.jpg"
            thumbnail_rel_path = f"{thumbnail_filename[:2]}/{thumbnail_filename}"
            thumbnail_full_path = self.thumbnail_cache_path / thumbnail_rel_path

            _, thumb_bytes = self.thumbnail_generator.generate_face_thumbnail(
                image=image,
                bbox=bbox,
                output_path=thumbnail_full_path
            )

            # Create face object (not yet attached to session)
            face_obj = Face(
                embedding_id=face_id,
                embedding_vec=embedding.astype(np.float32),
                quality_score=float(quality_score),
                bbox_px_x1=int(x1),
                bbox_px_y1=int(y1),
                bbox_px_x2=int(x2),
                bbox_px_y2=int(y2),
                bbox_x1=float(x1 / img_width),
                bbox_y1=float(y1 / img_height),
                bbox_x2=float(x2 / img_width),
                bbox_y2=float(y2 / img_height),
                frame_number=frame_number,
                track_id=track_id,
                video_path=str(video_path) if video_path else None,
                thumbnail_path=thumbnail_rel_path
            )

            return face_obj, embedding.astype(np.float32)

        except Exception as e:
            logger.error(f"Face processing failed: {e}")
            return None

    def _save_image_record(self, session: Session, result: ProcessingResult):
        """Persist the image, faces, and outbox rows in a single transaction.

        All three writes share one transaction so a crash anywhere leaves
        either nothing or everything. The outbox row is what makes the
        face eventually visible in FAISS — without it, the reaper has
        no work.

        Handles three cases for the Image row:
          1. No existing row for this file_path → INSERT new (most common).
          2. Existing row with status='failed' (e.g. Phase C orphan-recovery
             marked it failed because it had no faces, but the live scanner
             has now actually examined it) → UPDATE in place to completed.
          3. Existing row with status='completed' but the upstream file_hash
             idempotency check missed it (e.g. file content changed but
             path stayed the same) → UPDATE in place to refresh.

        Cases 2/3 fix a real bug: scanner ingestion + orphan recovery used
        to collide on `ix_images_file_path` UniqueViolation, killing the
        worker mid-file and leaving Faces uncommitted.
        """
        try:
            stat = result.file_path.stat()
            file_path_str = str(result.file_path)

            # Detect OneDrive paths so we can flag the row for host-side
            # eviction. The Linux container can't run `attrib +U` itself;
            # scripts/onedrive_evict.ps1 polls this column hourly and runs
            # the attrib call from the Windows host. Without this, scanning
            # OneDrive in bulk hydrates the local C: cache permanently.
            is_onedrive = (
                "/OneDrive/" in file_path_str
                or "/OneDrive - " in file_path_str
            )

            existing = (
                session.query(Image)
                .filter(Image.file_path == file_path_str)
                .first()
            )

            if existing is None:
                image_record = Image(
                    file_path=file_path_str,
                    file_hash=result.file_hash,
                    file_size=stat.st_size,
                    file_mtime=stat.st_mtime,
                    width=result.width or None,
                    height=result.height or None,
                    status="completed",
                    face_count=result.faces_processed,
                    is_video=result.is_video,
                    video_frames=result.video_frames_processed,
                    error_message=None,
                    onedrive_revert_pending=is_onedrive,
                )
                session.add(image_record)
                try:
                    session.flush()  # populate image_record.id
                except IntegrityError:
                    # Race: another worker beat us to INSERT for the same
                    # file_path (possible at index_workers > 1). Roll back
                    # the failed flush so the session is usable, then fall
                    # through to the UPDATE path — the other worker's row
                    # is now the authoritative one and we just need to
                    # attach our faces to it.
                    session.rollback()
                    image_record = (
                        session.query(Image)
                        .filter(Image.file_path == file_path_str)
                        .first()
                    )
                    if image_record is None:
                        # Extremely unlikely — means the race-winning row
                        # was deleted between our flush and this re-query.
                        raise RuntimeError(
                            f"file_path UniqueViolation race but row vanished: {file_path_str}"
                        )
                    logger.warning(
                        "file_path INSERT race resolved via UPDATE path",
                        file_path=file_path_str,
                    )
            else:
                # In-place update. Clearing error_message is important — the
                # row may have carried a stale "recovery: ..." note from
                # Phase C of orphan recovery.
                existing.file_hash = result.file_hash
                existing.file_size = stat.st_size
                existing.file_mtime = stat.st_mtime
                existing.width = result.width or None
                existing.height = result.height or None
                existing.status = "completed"
                existing.face_count = result.faces_processed
                existing.is_video = result.is_video
                existing.video_frames = result.video_frames_processed
                existing.error_message = None
                # Re-flag for revert if this is a OneDrive path. Idempotent —
                # if eviction already happened the daemon will see Offline=True
                # and just flip pending=False without doing anything.
                if is_onedrive:
                    existing.onedrive_revert_pending = True
                session.flush()
                image_record = existing

            for face, emb in result.face_objects:
                face.image_id = image_record.id
                session.add(face)
                # Outbox row tied to the same Face — same TX boundary.
                enqueue_face(session, face.embedding_id, emb)

            session.commit()

        except Exception as e:
            logger.error(f"Failed to save image record: {e}")
            session.rollback()
            raise
