"""Face engine worker — Stage 1 (schema init).

Runs the vendored facetracker face engine (src/face/) against the UNIFIED
unifiedanalyzer Postgres database. All facetracker tables live in the
`facetracker` schema (via search_path), namespaced away from the analyzer's
own public tables — see docs/facetracker_merge_plan.md.

Stage 1 (this file, now): ensure the schema + pgvector + create the tables
(images / faces / identities / face_identity_map / faiss_outbox) from the
vendored SQLAlchemy models.

Stage 2 (TODO): ingest collector media_items + derived video frames through the
InsightFace detector/embedder, write faces, and populate public.entity_faces.
This worker runs as its OWN process (SQLAlchemy + ONNX threads), separate from
the analyzer's asyncio API/scheduler.
"""
import logging
import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("face_worker")

# Schema inside the analyzer DB that holds the vendored facetracker tables.
FACE_DB_SCHEMA = os.getenv("FACE_DB_SCHEMA", "facetracker")


def analyzer_sqlalchemy_url() -> str:
    """The unifiedanalyzer DB as a synchronous SQLAlchemy (psycopg2) URL.

    Reuses ANALYZER_DATABASE_URL (asyncpg-style postgres://) and rewrites it for
    SQLAlchemy/psycopg2. localhost -> 127.0.0.1 so we hit Docker's IPv4 port
    proxy (the ::1 IPv6 bind isn't published, which times out).
    """
    u = urlparse(os.environ["ANALYZER_DATABASE_URL"])
    host = "127.0.0.1" if u.hostname in ("localhost", None) else u.hostname
    return (
        f"postgresql+psycopg2://{u.username}:{u.password}"
        f"@{host}:{u.port or 5432}/{u.path.lstrip('/')}"
    )


def _wait_for_db(engine, timeout: int = 180) -> None:
    """Block until the DB accepts a connection. The collector Postgres (shared)
    can be mid-startup/recovery; retry quietly instead of crashing."""
    import time

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    deadline = time.time() + timeout
    attempt = 0
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as e:
            if time.time() >= deadline:
                raise
            if attempt == 0:
                logger.warning("Waiting for analyzer DB (%s)...", str(e.orig).strip()[:80])
            attempt += 1
            time.sleep(3)


def init_schema() -> list[str]:
    """Create the facetracker schema + tables in the analyzer DB. Idempotent."""
    from sqlalchemy import create_engine, text

    # Importing the models registers them on Base.metadata. The outbox import
    # ensures faiss_outbox is created too (mirrors src/face/main.py's note).
    from src.face.storage.database import Base
    from src.face.storage import outbox  # noqa: F401  (registers FaissOutbox)

    url = analyzer_sqlalchemy_url()
    # search_path=facetracker so unqualified CREATE TABLEs land in that schema.
    engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    _wait_for_db(engine)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {FACE_DB_SCHEMA}"))
            conn.commit()
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s ORDER BY table_name"
                ),
                {"s": FACE_DB_SCHEMA},
            ).fetchall()
        return [r[0] for r in rows]
    finally:
        engine.dispose()


def _collector_sqlalchemy_url() -> str:
    """The unifiedcollector DB as a sync SQLAlchemy (psycopg2) URL."""
    u = urlparse(os.environ["COLLECTOR_DATABASE_URL"])
    host = "127.0.0.1" if u.hostname in ("localhost", None) else u.hostname
    return (
        f"postgresql+psycopg2://{u.username}:{u.password}"
        f"@{host}:{u.port or 5432}/{u.path.lstrip('/')}"
    )


# Collector media content types that may contain faces.
_FACE_CONTENT_TYPES = ("image", "profile_photo")
# Owner attribution is only safe for portrait-like media. Group photos/videos
# still get indexed for search, but their faces are not labelled as the owner.
_OWNER_ATTRIBUTION_MAX_FACES = int(os.getenv("FACE_WORKER_OWNER_ATTRIBUTION_MAX_FACES", "1"))


def _build_entity_lookup(analyzer_conn) -> dict:
    """(source, platform_id|username[, lowercased]) -> analyzer entity_id.

    Mirrors src/pipeline/media_common.build_entity_lookup but sync (SQLAlchemy),
    so the ingest can attribute a collector media item's owner to an entity.
    """
    from sqlalchemy import text
    rows = analyzer_conn.execute(text(
        "SELECT entity_id::text AS eid, source, platform_id, platform_username "
        "FROM entity_platform_links"
    )).fetchall()
    lookup: dict = {}
    for r in rows:
        if r.platform_id:
            lookup[(r.source, r.platform_id)] = r.eid
        if r.platform_username:
            lookup.setdefault((r.source, r.platform_username), r.eid)
            lookup.setdefault((r.source, r.platform_username.lower()), r.eid)
    return lookup


def ingest_collector_media(limit: int = 50, tracked_only: bool = False) -> dict:
    """Stage 2: index faces from collector image media into facetracker.faces,
    linking to analyzer entities via public.entity_faces.

    Collector-media-only scope (D3): pulls media_items (images/profile photos)
    from the collector DB, runs InsightFace, writes Image+Face rows in the
    facetracker schema (deduped by file_path), and bridges to entities by the
    media item's (source, entity_id) attribution. Bounded by `limit` for
    resumable batching. Reuses src/pipeline media-path resolution.

    tracked_only (2026-07-09 face->identity fix): when True, restrict the
    candidate set to media whose (source, entity_id) resolves to a KNOWN analyzer
    entity BEFORE spending detector time. Rationale: the collector's media volume
    is dominated by untracked owners (github contributor avatars, search-result
    thumbnails, scraped website images) that will never bridge to an entity, so a
    plain `collected_at DESC` scan starves the handful of tracked-entity images
    (e.g. only 15 of ~1,650 owner-matched lemon8 images were indexed). Those
    untracked faces still have drive-social-graph value, but only tracked-owner
    faces can populate public.entity_faces and thus feed face_pair_knn /
    social_face_link / media_face_match. The `loop` runs a tracked_only pass FIRST
    each tick so entity_faces coverage is never starved by avatar volume.
    """
    import cv2
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from src.face.engine.detector import FaceDetector
    from src.face.storage.database import Image as FtImage, Face as FtFace
    from src.pipeline.media_common import (
        resolve_media_path, _MEDIA_CONFINEMENT_ROOT, MEDIA_DERIVED_PATH,
    )

    stats = {
        "scanned": 0,
        "images_indexed": 0,
        "faces": 0,
        "linked": 0,
        "owner_link_skipped_group": 0,
        "skipped": 0,
    }

    analyzer_engine = create_engine(
        analyzer_sqlalchemy_url(),
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
        # P2-1: cap this worker's share of the shared Postgres so a big drive
        # scan can't exhaust the connection pool the analyzer/scheduler need.
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    collector_engine = create_engine(
        _collector_sqlalchemy_url(), pool_pre_ping=True,
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    _wait_for_db(analyzer_engine)
    _wait_for_db(collector_engine)

    # Already-indexed collector file_paths (dedupe cursor).
    with analyzer_engine.connect() as aconn:
        done = {r[0] for r in aconn.execute(text("SELECT file_path FROM images")).fetchall()}
        done_media_ids = {
            r[0] for r in aconn.execute(text(
                "SELECT file_hash FROM images "
                "WHERE file_hash IS NOT NULL "
                "AND COALESCE(status, 'completed') IN ('completed', 'failed')"
            )).fetchall()
        }
        entity_lookup = _build_entity_lookup(aconn)

    try:
        scan_window = max(limit, int(os.getenv("FACE_COLLECTOR_MEDIA_SCAN_WINDOW", str(max(limit * 20, 1000)))))
    except (TypeError, ValueError):
        scan_window = max(limit * 20, 1000)

    # Candidate collector media. Keep this query bounded; on production
    # Collector DBs the full image/profile-photo set is large enough to starve
    # API startup and scheduler heartbeats under concurrent Analyzer workers.
    with collector_engine.connect() as cconn:
        rows = cconn.execute(
            text(
                "SELECT id::text AS id, source, entity_id, content_type, file_path "
                "FROM media_items WHERE content_type = ANY(:cts) AND file_path IS NOT NULL "
                "ORDER BY collected_at DESC "
                "LIMIT :scan_window"
            ),
            {"cts": list(_FACE_CONTENT_TYPES), "scan_window": scan_window},
        ).fetchall()

    # tracked_only: drop candidates whose owner doesn't resolve to an analyzer
    # entity, so the (expensive) detector time is spent only on media that can
    # actually populate public.entity_faces. entity_lookup was built above from
    # entity_platform_links (keyed by both platform_id and lowercased username).
    if tracked_only:
        rows = [
            r for r in rows
            if entity_lookup.get((r.source, r.entity_id))
            or (entity_lookup.get((r.source, r.entity_id.lower())) if r.entity_id else None)
        ]

    detector = None
    Session = sessionmaker(bind=analyzer_engine)

    # #36: tombstone genuinely-missing/unreadable files so they aren't re-scanned
    # every tick (e.g. the ~150 lemon8 images lost when Z was reformatted retried
    # forever). We only tombstone when the media ROOT is mounted — if the whole
    # root is absent the drive is merely offline and the files may return, so we
    # skip without tombstoning ("graceful offline"). Checked once per call, not
    # per-file, to avoid stat spam.
    media_root_online = _MEDIA_CONFINEMENT_ROOT.exists()
    derived_root_online = MEDIA_DERIVED_PATH.exists()
    stats["tombstoned"] = 0

    def _tombstone(file_path: str, file_hash: str, reason: str) -> None:
        s = Session()
        try:
            s.add(FtImage(
                file_path=file_path, file_hash=file_hash, file_size=0,
                file_mtime=0.0, width=0, height=0, status="missing",
                is_video=False, face_count=0,
            ))
            s.commit()
            stats["tombstoned"] += 1
        except Exception:
            s.rollback()  # already tombstoned/indexed — fine
        finally:
            s.close()
        done.add(file_path)

    for r in rows:
        if stats["images_indexed"] >= limit:
            break
        if r.file_path in done or r.id in done_media_ids:
            continue
        stats["scanned"] += 1
        disk = resolve_media_path(r.file_path)
        if disk is None:
            stats["skipped"] += 1
            # Root mounted but file absent => genuinely gone: tombstone. Root
            # missing => drive offline: skip, retry when it returns.
            root_online = derived_root_online if "media_derived" in r.file_path else media_root_online
            if root_online:
                _tombstone(r.file_path, r.id, "file_gone")
            continue
        img = cv2.imread(str(disk))
        if img is None:
            stats["skipped"] += 1
            _tombstone(r.file_path, r.id, "unreadable")  # drive present, file corrupt
            continue

        # Lazy-init detector (downloads model on first use; keep off the path
        # when there's nothing to process).
        if detector is None:
            detector = FaceDetector()

        h, w = img.shape[:2]
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        faces = detector.detect(rgb)

        eid = entity_lookup.get((r.source, r.entity_id)) or (
            entity_lookup.get((r.source, r.entity_id.lower())) if r.entity_id else None
        )
        can_attribute_owner = bool(eid) and len(faces) <= _OWNER_ATTRIBUTION_MAX_FACES
        if eid and faces and not can_attribute_owner:
            stats["owner_link_skipped_group"] += 1

        sess = Session()
        try:
            image_row = FtImage(
                file_path=r.file_path, file_hash=r.id, file_size=int(disk.stat().st_size),
                file_mtime=disk.stat().st_mtime, width=w, height=h,
                status="completed", is_video=False, face_count=len(faces),
            )
            sess.add(image_row)
            sess.flush()  # assign image_row.id
            for idx, f in enumerate(faces):
                if f.embedding is None:
                    continue
                x1, y1, x2, y2 = [int(v) for v in f.bbox]
                face_row = FtFace(
                    image_id=image_row.id,
                    embedding_id=f"{r.id}:{idx}",
                    bbox_x1=x1 / w, bbox_y1=y1 / h, bbox_x2=x2 / w, bbox_y2=y2 / h,
                    bbox_px_x1=x1, bbox_px_y1=y1, bbox_px_x2=x2, bbox_px_y2=y2,
                    quality_score=float(f.quality_score),
                    laplacian_variance=float(f.laplacian_variance),
                    face_area_percent=float(f.area_ratio * 100.0),
                    detection_confidence=float(f.confidence),
                    embedding_vec=f.embedding.tolist(),
                )
                sess.add(face_row)
                sess.flush()  # assign face_row.id
                stats["faces"] += 1
                if can_attribute_owner:
                    sess.execute(text(
                        "INSERT INTO public.entity_faces (entity_id, face_id, media_item_id, confidence, method) "
                        "VALUES (:e, :f, :m, :c, 'media_attribution') ON CONFLICT (entity_id, face_id) DO NOTHING"
                    ), {"e": eid, "f": face_row.id, "m": r.id, "c": float(f.quality_score)})
                    stats["linked"] += 1
            sess.commit()
            stats["images_indexed"] += 1
            done.add(r.file_path)
            done_media_ids.add(r.id)
        except Exception:
            sess.rollback()
            logger.exception("ingest failed for media_item %s", r.id)
            stats["skipped"] += 1
        finally:
            sess.close()

    analyzer_engine.dispose()
    collector_engine.dispose()
    return stats



def relink_entity_faces(limit: int | None = None) -> dict:
    """Backfill public.entity_faces for already-indexed collector faces whose
    media_item NOW resolves to an analyzer entity.

    Faces are bridged at INDEX time by ingest_collector_media, but
    entity_platform_links keeps growing as the analyzer's entity resolution runs
    — so a face indexed BEFORE its owner's platform link existed was never
    linked, and (being already indexed) is never revisited by the ingest dedup.
    That left public.entity_faces empty even though ~950+ indexed media items
    were resolvable. This re-resolves indexed-but-unlinked collector faces and
    inserts the missing rows. Idempotent (ON CONFLICT DO NOTHING); bounded by
    `limit` (None = all). Drive-scanned faces (40-char sha1 file_hash, no
    platform owner) are excluded — only collector faces (UUID file_hash =
    media_item id) can attribute.
    """
    from sqlalchemy import create_engine, text

    stats = {"faces_checked": 0, "linked": 0, "junk_entity_faces": 0, "junk_entity_faces_purged": 0}
    analyzer_engine = create_engine(
        analyzer_sqlalchemy_url(),
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
        # P2-1: cap this worker's share of the shared Postgres so a big drive
        # scan can't exhaust the connection pool the analyzer/scheduler need.
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    collector_engine = create_engine(
        _collector_sqlalchemy_url(), pool_pre_ping=True,
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    _wait_for_db(analyzer_engine)
    _wait_for_db(collector_engine)
    try:
        with analyzer_engine.connect() as aconn:
            lookup = _build_entity_lookup(aconn)
            q = (
                "SELECT f.id AS face_id, i.file_hash AS mid, f.quality_score AS q "
                "FROM faces f JOIN images i ON i.id = f.image_id "
                "WHERE i.file_hash ~* '^[0-9a-f-]{36}$' "
                "AND NOT COALESCE(f.is_junk, false) "
                "AND COALESCE(i.face_count, :unknown_face_count) <= :max_faces "
                "AND NOT EXISTS (SELECT 1 FROM public.entity_faces ef WHERE ef.face_id = f.id)"
            )
            if limit:
                q += f" LIMIT {int(limit)}"
            faces = aconn.execute(
                text(q),
                {"unknown_face_count": 999, "max_faces": _OWNER_ATTRIBUTION_MAX_FACES},
            ).fetchall()
        stats["faces_checked"] = len(faces)
        if faces:
            # Resolve each distinct media_item -> (source, entity_id) from collector.
            mids = list({f.mid for f in faces})
            attr: dict[str, tuple] = {}
            with collector_engine.connect() as cconn:
                CH = 5000
                for i in range(0, len(mids), CH):
                    for _id, src, eid in cconn.execute(
                        text("SELECT id::text, source, entity_id FROM media_items "
                             "WHERE id = ANY(CAST(:ids AS uuid[]))"),
                        {"ids": mids[i:i + CH]},
                    ).fetchall():
                        attr[_id] = (src, eid)

            with analyzer_engine.begin() as aconn:
                for f in faces:
                    a = attr.get(f.mid)
                    if not a:
                        continue
                    src, eid_raw = a
                    eid = lookup.get((src, eid_raw)) or (
                        lookup.get((src, eid_raw.lower())) if eid_raw else None
                    )
                    if not eid:
                        continue
                    aconn.execute(
                        text("INSERT INTO public.entity_faces "
                             "(entity_id, face_id, media_item_id, confidence, method) "
                             "VALUES (:e, :f, :m, :c, 'media_attribution_relink') "
                             "ON CONFLICT (entity_id, face_id) DO NOTHING"),
                        {"e": eid, "f": f.face_id, "m": f.mid, "c": float(f.q or 0.0)},
                    )
                    stats["linked"] += 1

        with analyzer_engine.connect() as aconn:
            stats["junk_entity_faces"] = int(aconn.execute(text(
                "SELECT COUNT(*) FROM public.entity_faces ef "
                "JOIN faces f ON f.id = ef.face_id "
                "WHERE COALESCE(f.is_junk, false)"
            )).scalar() or 0)
        if stats["junk_entity_faces"]:
            with analyzer_engine.begin() as aconn:
                stats["junk_entity_faces_purged"] = int(aconn.execute(text(
                    "WITH d AS (DELETE FROM public.entity_faces ef "
                    "USING faces f WHERE ef.face_id = f.id AND COALESCE(f.is_junk, false) "
                    "RETURNING ef.face_id) "
                    "SELECT COUNT(*) FROM d"
                )).scalar() or 0)
            with analyzer_engine.connect() as aconn:
                stats["junk_entity_faces"] = int(aconn.execute(text(
                    "SELECT COUNT(*) FROM public.entity_faces ef "
                    "JOIN faces f ON f.id = ef.face_id "
                    "WHERE COALESCE(f.is_junk, false)"
                )).scalar() or 0)
        assert stats["junk_entity_faces"] == 0, (
            f"entity_faces has {stats['junk_entity_faces']} junk-linked face(s)"
        )
    finally:
        analyzer_engine.dispose()
        collector_engine.dispose()
    return stats


# Collector video content types worth sampling for faces. Personal-comms video
# (telegram/beeper/whatsapp) is highest-signal for OSINT; the big YouTube/TikTok/
# Instagram volume is opt-in via FACE_VIDEO_SOURCES so a run isn't swamped by
# influencer content. Override to '' to take all sources.
_VIDEO_FACE_SOURCES = tuple(
    s.strip() for s in os.getenv(
        "FACE_VIDEO_SOURCES", "telegram,beeper,whatsapp,threads,instagram"
    ).split(",") if s.strip()
)
_VIDEO_FRAME_INTERVAL_SEC = int(os.getenv("FACE_VIDEO_FRAME_INTERVAL", "20"))
_VIDEO_FRAME_MAX = int(os.getenv("FACE_VIDEO_FRAME_MAX", "12"))
# Video frames are typically full-resolution (720p/1080p), so a real face
# occupies a much smaller fraction of the frame than in a cropped profile photo.
# The image-tuned MIN_AREA_RATIO=0.05 filtered out nearly every genuine video
# face (observed at area_ratio 0.02-0.03). Use a smaller, tunable floor for the
# video path so real faces survive; the Q3 is_junk gate still governs downstream.
_VIDEO_MIN_AREA_RATIO = float(os.getenv("FACE_VIDEO_MIN_AREA_RATIO", "0.01"))


def ingest_video_frames(limit: int = 20, tracked_only: bool = False) -> dict:
    """Q1: index faces from collector VIDEO media into facetracker.

    The face corpus was image-only (facetracker.images.is_video all False, 42k
    collector videos unprocessed by the facetracker path). ingest_collector_media
    handles only ('image','profile_photo'); video frames extracted by the
    analyzer's Tier-1 extract_video_frames() land in public.media_analysis, NOT
    facetracker — so no video face ever reached clustering / entity_faces.

    This walks collector videos (FACE_VIDEO_SOURCES), samples keyframes with
    ffmpeg (fps=1/interval, capped at FACE_VIDEO_FRAME_MAX; falls back to the
    first frame for sub-interval clips), runs InsightFace on each frame, and
    writes ONE facetracker Image per video with is_video=True + video_frames=N
    and every detected face across frames (frame index recorded on Face.frame_number
    + video_path). Bridges to the owning entity via media attribution, identical
    to the image path, so video faces feed clustering, identities, and entity_faces.

    Deduped by images.file_path (the video's collector path), bounded by `limit`
    (videos, not frames) per call for resumable batching. Idempotent.

    tracked_only mirrors ingest_collector_media: restrict to videos whose owner
    resolves to a known analyzer entity FIRST, so detector time is spent where it
    can populate entity_faces (personal-comms video is mostly untracked group
    chats). ffmpeg/drive gates fail closed (graceful offline)."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import cv2
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from src.face.engine.detector import FaceDetector
    from src.face.storage.database import Image as FtImage, Face as FtFace
    from src.pipeline.media_common import resolve_media_path, _MEDIA_CONFINEMENT_ROOT

    stats = {
        "scanned": 0,
        "videos_indexed": 0,
        "frames": 0,
        "faces": 0,
        "linked": 0,
        "owner_link_skipped_group": 0,
        "skipped": 0,
        "no_frames": 0,
    }

    if shutil.which("ffmpeg") is None:
        logger.warning("ingest_video_frames: ffmpeg not on PATH, skipping")
        return {**stats, "skipped_reason": "ffmpeg_unavailable"}
    if not _MEDIA_CONFINEMENT_ROOT.exists():
        logger.warning("ingest_video_frames: media root offline, skipping")
        return {**stats, "skipped_reason": "drive_offline"}

    analyzer_engine = create_engine(
        analyzer_sqlalchemy_url(),
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    collector_engine = create_engine(
        _collector_sqlalchemy_url(), pool_pre_ping=True,
        pool_size=int(os.getenv("FACE_DB_POOL_SIZE", "3")),
        max_overflow=int(os.getenv("FACE_DB_MAX_OVERFLOW", "2")),
    )
    _wait_for_db(analyzer_engine)
    _wait_for_db(collector_engine)

    with analyzer_engine.connect() as aconn:
        done = {r[0] for r in aconn.execute(text("SELECT file_path FROM images")).fetchall()}
        done_media_ids = {
            r[0] for r in aconn.execute(text(
                "SELECT file_hash FROM images "
                "WHERE file_hash IS NOT NULL "
                "AND COALESCE(status, 'completed') IN ('completed', 'failed')"
            )).fetchall()
        }
        entity_lookup = _build_entity_lookup(aconn)

    # Candidate videos. Restrict source when FACE_VIDEO_SOURCES is set.
    with collector_engine.connect() as cconn:
        if _VIDEO_FACE_SOURCES:
            rows = cconn.execute(
                text("SELECT id::text AS id, source, entity_id, content_type, file_path "
                     "FROM media_items WHERE content_type = 'video' AND file_path IS NOT NULL "
                     "AND source = ANY(:srcs) ORDER BY collected_at DESC"),
                {"srcs": list(_VIDEO_FACE_SOURCES)},
            ).fetchall()
        else:
            rows = cconn.execute(
                text("SELECT id::text AS id, source, entity_id, content_type, file_path "
                     "FROM media_items WHERE content_type = 'video' AND file_path IS NOT NULL "
                     "ORDER BY collected_at DESC")
            ).fetchall()

    if tracked_only:
        rows = [
            r for r in rows
            if entity_lookup.get((r.source, r.entity_id))
            or (entity_lookup.get((r.source, r.entity_id.lower())) if r.entity_id else None)
        ]

    detector = None
    Session = sessionmaker(bind=analyzer_engine)

    def _extract_frames(video_path: Path, outdir: Path) -> list[tuple[int, Path]]:
        """ffmpeg keyframe sample -> [(sec, frame_path)]. Falls back to first
        frame for clips shorter than the sampling interval."""
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path),
             "-vf", f"fps=1/{_VIDEO_FRAME_INTERVAL_SEC}",
             "-frames:v", str(_VIDEO_FRAME_MAX), "-loglevel", "error",
             str(outdir / "frame_%03d.jpg")],
            capture_output=True, timeout=180,
        )
        frames = []
        for fp in sorted(outdir.glob("frame_*.jpg")):
            try:
                idx = int(fp.stem.split("_")[1])
            except (ValueError, IndexError):
                continue
            frames.append(((idx - 1) * _VIDEO_FRAME_INTERVAL_SEC, fp))
        if not frames:
            first = outdir / "fallback.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1",
                 "-loglevel", "error", str(first)],
                capture_output=True, timeout=180,
            )
            if first.exists():
                frames.append((0, first))
        return frames

    for r in rows:
        if stats["videos_indexed"] >= limit:
            break
        if r.file_path in done or r.id in done_media_ids:
            continue
        stats["scanned"] += 1
        disk = resolve_media_path(r.file_path)
        if disk is None:
            stats["skipped"] += 1
            continue

        eid = entity_lookup.get((r.source, r.entity_id)) or (
            entity_lookup.get((r.source, r.entity_id.lower())) if r.entity_id else None
        )

        with tempfile.TemporaryDirectory(prefix="vidframes_") as td:
            outdir = Path(td)
            try:
                frames = _extract_frames(disk, outdir)
            except Exception:
                logger.debug("ffmpeg failed for %s", disk, exc_info=True)
                frames = []
            if not frames:
                stats["no_frames"] += 1
                # Tombstone so a genuinely un-sampleable video isn't retried
                # forever (mirrors the image-path tombstone policy).
                s = Session()
                try:
                    s.add(FtImage(
                        file_path=r.file_path, file_hash=r.id, file_size=0,
                        file_mtime=0.0, width=0, height=0, status="failed",
                        is_video=True, video_frames=0, face_count=0,
                    ))
                    s.commit()
                except Exception:
                    s.rollback()
                finally:
                    s.close()
                done.add(r.file_path)
                done_media_ids.add(r.id)
                continue

            if detector is None:
                detector = FaceDetector()
                # Relax the area gate for the video path (see _VIDEO_MIN_AREA_RATIO).
                # Keep the detector's other thresholds (confidence, sharpness).
                detector.MIN_AREA_RATIO = _VIDEO_MIN_AREA_RATIO

            # Detect across all frames; collect (frame_sec, w, h, faces).
            per_frame = []
            total_faces = 0
            for sec, fp in frames:
                img = cv2.imread(str(fp))
                if img is None:
                    continue
                h, w = img.shape[:2]
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                fs = detector.detect(rgb)
                per_frame.append((sec, w, h, fs))
                total_faces += len(fs)
            stats["frames"] += len(per_frame)

            # Use the first decoded frame's dims as the Image's canonical size.
            if not per_frame:
                stats["skipped"] += 1
                continue
            _sec0, w0, h0, _ = per_frame[0]
            can_attribute_owner = bool(eid) and total_faces <= _OWNER_ATTRIBUTION_MAX_FACES
            if eid and total_faces and not can_attribute_owner:
                stats["owner_link_skipped_group"] += 1

            sess = Session()
            try:
                image_row = FtImage(
                    file_path=r.file_path, file_hash=r.id,
                    file_size=int(disk.stat().st_size), file_mtime=disk.stat().st_mtime,
                    width=w0, height=h0, status="completed", is_video=True,
                    video_frames=len(per_frame), face_count=total_faces,
                )
                sess.add(image_row)
                sess.flush()
                for sec, w, h, fs in per_frame:
                    for idx, f in enumerate(fs):
                        if f.embedding is None:
                            continue
                        x1, y1, x2, y2 = [int(v) for v in f.bbox]
                        face_row = FtFace(
                            image_id=image_row.id,
                            embedding_id=f"{r.id}:{sec}:{idx}",
                            bbox_x1=x1 / w, bbox_y1=y1 / h, bbox_x2=x2 / w, bbox_y2=y2 / h,
                            bbox_px_x1=x1, bbox_px_y1=y1, bbox_px_x2=x2, bbox_px_y2=y2,
                            quality_score=float(f.quality_score),
                            laplacian_variance=float(f.laplacian_variance),
                            face_area_percent=float(f.area_ratio * 100.0),
                            detection_confidence=float(f.confidence),
                            embedding_vec=f.embedding.tolist(),
                            frame_number=sec, video_path=r.file_path,
                        )
                        sess.add(face_row)
                        sess.flush()
                        stats["faces"] += 1
                        if can_attribute_owner:
                            sess.execute(text(
                                "INSERT INTO public.entity_faces (entity_id, face_id, media_item_id, confidence, method) "
                                "VALUES (:e, :f, :m, :c, 'media_attribution') ON CONFLICT (entity_id, face_id) DO NOTHING"
                            ), {"e": eid, "f": face_row.id, "m": r.id, "c": float(f.quality_score)})
                            stats["linked"] += 1
                sess.commit()
                stats["videos_indexed"] += 1
                done.add(r.file_path)
                done_media_ids.add(r.id)
            except Exception:
                sess.rollback()
                logger.exception("video ingest failed for media_item %s", r.id)
                stats["skipped"] += 1
            finally:
                sess.close()

    analyzer_engine.dispose()
    collector_engine.dispose()
    logger.info("ingest_video_frames: %s", stats)
    return stats


def loop(batch: int, interval: int) -> None:
    """Run ingest_collector_media in a continuous loop (the intended end-state:
    a separate long-running worker process, see docs/facetracker_merge_plan.md §6).

    Each tick indexes up to `batch` un-indexed collector images, then sleeps
    `interval` seconds. Idempotent + resumable (ingest dedupes by file_path), so
    a tick that finds nothing new (e.g. while collector source media is still
    being restored — task B1) is a cheap no-op. Errors are logged, not fatal.

    """
    import time
    # Re-attribution cadence: faces are bridged at index time, but entity links
    # keep growing, so periodically re-link already-indexed faces that have since
    # become resolvable. Cheaper than the drive walk; every ~30min by default.
    relink_interval = int(os.getenv("FACE_WORKER_RELINK_INTERVAL", "1800"))
    relink_every = max(1, relink_interval // max(1, interval))
    # Q1 video cadence: video sampling is far heavier per item (ffmpeg + N-frame
    # detect) than an image, so it runs on its own slower cadence and a smaller
    # per-tick batch. FACE_WORKER_VIDEO_INTERVAL=0 disables the video path.
    video_batch = int(os.getenv("FACE_WORKER_VIDEO_BATCH", "10"))
    video_interval = int(os.getenv("FACE_WORKER_VIDEO_INTERVAL", "600"))
    video_every = max(1, video_interval // max(1, interval)) if video_interval > 0 else 0
    logger.info(
        "Face worker loop: batch=%d interval=%ds | "
        "relink_every=%d | video_batch=%d video_every=%d ticks",
        batch, interval, relink_every, video_batch, video_every,
    )
    tick = 0
    while True:
        # Tracked-entity media FIRST (2026-07-09 face->identity fix): index media
        # owned by known entities before general media, so entity_faces coverage
        # is never starved by high-volume untracked avatars. Cheap when caught up
        # (dedupes by file_path). Uses the same batch budget.
        try:
            tstats = ingest_collector_media(batch, tracked_only=True)
            if tstats.get("images_indexed"):
                logger.info("tracked-entity ingest tick: %s", tstats)
        except Exception:
            logger.exception("tracked ingest tick failed (will retry next interval)")

        try:
            stats = ingest_collector_media(batch)
            if stats.get("images_indexed"):
                logger.info("collector ingest tick: %s", stats)
        except Exception:
            logger.exception("collector ingest tick failed (will retry next interval)")

        if tick % relink_every == 0:
            try:
                rstats = relink_entity_faces()
                if rstats.get("linked"):
                    logger.info("entity_faces relink tick: %s", rstats)
            except Exception:
                logger.exception("entity_faces relink tick failed (will retry next cycle)")

        # Q1: video-frame face ingest on its own (slower) cadence. Tracked-owner
        # videos first so entity_faces coverage isn't starved by group-chat video.
        if video_every and tick % video_every == 0:
            try:
                vt = ingest_video_frames(video_batch, tracked_only=True)
                if vt.get("videos_indexed"):
                    logger.info("tracked video ingest tick: %s", vt)
                vstats = ingest_video_frames(video_batch)
                if vstats.get("videos_indexed"):
                    logger.info("video ingest tick: %s", vstats)
            except Exception:
                logger.exception("video ingest tick failed (will retry next cycle)")

        tick += 1
        time.sleep(interval)


def main() -> None:
    import os
    import sys
    import time

    try:
        _startup_delay = max(0, int(os.getenv("STARTUP_DELAY_SECONDS", "0")))
    except (TypeError, ValueError):
        _startup_delay = 0
    if _startup_delay:
        logger.info("Startup delay: sleeping %ds before schema init", _startup_delay)
        time.sleep(_startup_delay)

    logger.info("Face worker (schema=%s)", FACE_DB_SCHEMA)
    tables = init_schema()
    logger.info("facetracker schema tables present: %s", tables)
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "ingest":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        logger.info("Stage 2 ingest (limit=%d)…", limit)
        logger.info("ingest stats: %s", ingest_collector_media(limit))
    elif cmd == "ingest-tracked":
        # One-shot: index ONLY media owned by known analyzer entities. Used to
        # backfill entity_faces coverage that a plain collected_at-DESC scan
        # starved (see ingest_collector_media tracked_only doc). Big default
        # limit so a single run drains the tracked backlog.
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
        logger.info("Tracked-entity ingest (limit=%d)…", limit)
        logger.info("tracked ingest stats: %s", ingest_collector_media(limit, tracked_only=True))
    elif cmd == "relink":
        # One-shot backfill of public.entity_faces for already-indexed faces whose
        # media_item now resolves to an entity (limit optional; default all).
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
        logger.info("entity_faces relink (limit=%s)…", limit)
        logger.info("relink stats: %s", relink_entity_faces(limit))
    elif cmd == "video":
        # Q1 one-shot: index faces from collector VIDEO media into facetracker
        # (is_video=True). Pass "tracked" as the 3rd arg to restrict to
        # known-entity-owned videos first.
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        tracked = len(sys.argv) > 3 and sys.argv[3] == "tracked"
        logger.info("Video-frame ingest (limit=%d, tracked_only=%s)…", limit, tracked)
        logger.info("video ingest stats: %s", ingest_video_frames(limit, tracked_only=tracked))
    elif cmd == "loop":
        batch = int(os.getenv("FACE_WORKER_BATCH", "50"))
        interval = int(os.getenv("FACE_WORKER_INTERVAL", "300"))
        loop(batch, interval)
    else:
        logger.info(
            "Schema ready. Run `python -m src.face_worker ingest [N]` for a one-shot "
            "batch, or `python -m src.face_worker loop` for the continuous worker."
        )


if __name__ == "__main__":
    main()
