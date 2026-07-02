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


def ingest_collector_media(limit: int = 50) -> dict:
    """Stage 2: index faces from collector image media into facetracker.faces,
    linking to analyzer entities via public.entity_faces.

    Collector-media-only scope (D3): pulls media_items (images/profile photos)
    from the collector DB, runs InsightFace, writes Image+Face rows in the
    facetracker schema (deduped by file_path), and bridges to entities by the
    media item's (source, entity_id) attribution. Bounded by `limit` for
    resumable batching. Reuses src/pipeline media-path resolution.
    """
    import cv2
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from src.face.engine.detector import FaceDetector
    from src.face.storage.database import Image as FtImage, Face as FtFace
    from src.pipeline.media_common import resolve_media_path

    stats = {"scanned": 0, "images_indexed": 0, "faces": 0, "linked": 0, "skipped": 0}

    analyzer_engine = create_engine(
        analyzer_sqlalchemy_url(),
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
    )
    collector_engine = create_engine(_collector_sqlalchemy_url(), pool_pre_ping=True)
    _wait_for_db(analyzer_engine)
    _wait_for_db(collector_engine)

    # Already-indexed collector file_paths (dedupe cursor).
    with analyzer_engine.connect() as aconn:
        done = {r[0] for r in aconn.execute(text("SELECT file_path FROM images")).fetchall()}
        entity_lookup = _build_entity_lookup(aconn)

    # Candidate collector media.
    with collector_engine.connect() as cconn:
        rows = cconn.execute(
            text(
                "SELECT id::text AS id, source, entity_id, content_type, file_path "
                "FROM media_items WHERE content_type = ANY(:cts) AND file_path IS NOT NULL "
                "ORDER BY collected_at DESC"
            ),
            {"cts": list(_FACE_CONTENT_TYPES)},
        ).fetchall()

    detector = None
    Session = sessionmaker(bind=analyzer_engine)

    for r in rows:
        if stats["images_indexed"] >= limit:
            break
        if r.file_path in done:
            continue
        stats["scanned"] += 1
        disk = resolve_media_path(r.file_path)
        if disk is None:
            stats["skipped"] += 1
            continue
        img = cv2.imread(str(disk))
        if img is None:
            stats["skipped"] += 1
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
                if eid:
                    sess.execute(text(
                        "INSERT INTO public.entity_faces (entity_id, face_id, media_item_id, confidence, method) "
                        "VALUES (:e, :f, :m, :c, 'media_attribution') ON CONFLICT (entity_id, face_id) DO NOTHING"
                    ), {"e": eid, "f": face_row.id, "m": r.id, "c": float(f.quality_score)})
                    stats["linked"] += 1
            sess.commit()
            stats["images_indexed"] += 1
        except Exception:
            sess.rollback()
            logger.exception("ingest failed for media_item %s", r.id)
            stats["skipped"] += 1
        finally:
            sess.close()

    analyzer_engine.dispose()
    collector_engine.dispose()
    return stats


def ingest_drive_media(limit: int = 200) -> dict:
    """Index faces from image files on the configured DRIVE_SOURCES (e.g. the
    W:/X:/Y:/Z: host drives, mounted into this container) into facetracker.images
    / facetracker.faces.

    This is the filesystem-walk counterpart to ingest_collector_media: instead of
    pulling rows from the collector's media_items table, it walks the drive trees
    via DriveScanner and runs the SAME InsightFace detect -> Image/Face write flow.
    Differences from the collector path:
      * Source is a filesystem walk (DriveScanner over Settings.drive_sources),
        scoped + excluded by DRIVE_SOURCES / EXCLUDE_PATHS / EXCLUDE_DIR_NAMES.
      * No entity bridging — a loose file on a drive has no platform owner, so we
        do NOT write public.entity_faces (faces are still FAISS/pgvector
        searchable and clusterable, just not pre-attributed to an entity).
      * Images only for now. cv2.imread can't decode videos/most RAW, so we
        filter to Settings.image_extensions. TODO(handoff): add a video-frame
        path (mirror facetracker manager.py's ffmpeg sampling) if drive videos
        need indexing.

    SCOPE/SAFETY: DriveScanner fails closed when no drive_sources are configured
    (see scanner.py D3 scope-lock) — and C: must never be added to DRIVE_SOURCES
    (OneDrive hydration hazard; see memory + docs/facetracker_merge_plan.md).

    Deduped by images.file_path (the container path), bounded by `limit` per call
    for resumable batching. Idempotent: an already-indexed path is skipped.

    We iterate each source with scanner.scan_directory() (single-threaded,
    breakable generator) rather than scanner.scan_drives() (which spawns daemon
    producer threads that would leak across repeated loop ticks when we break
    early on `limit`).
    """
    import hashlib

    import cv2
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from src.face.config import Settings
    from src.face.discovery.scanner import DriveScanner
    from src.face.engine.detector import FaceDetector
    from src.face.storage.database import Image as FtImage, Face as FtFace

    stats = {"scanned": 0, "images_indexed": 0, "faces": 0, "skipped": 0}

    settings = Settings()
    scanner = DriveScanner(settings)
    if not scanner.drive_sources:
        # Fail-closed: no DRIVE_SOURCES -> nothing to do (cheap no-op).
        return stats
    image_exts = set(settings.image_extensions)

    analyzer_engine = create_engine(
        analyzer_sqlalchemy_url(),
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
    )
    _wait_for_db(analyzer_engine)

    # Dedupe cursor: every already-indexed file_path (collector + drive).
    with analyzer_engine.connect() as aconn:
        done = {r[0] for r in aconn.execute(text("SELECT file_path FROM images")).fetchall()}

    detector = None
    Session = sessionmaker(bind=analyzer_engine)

    def _reached_limit() -> bool:
        return stats["images_indexed"] >= limit

    try:
        for source in scanner.drive_sources:
            if _reached_limit():
                break
            src_path = source.get("path") if isinstance(source, dict) else source.path
            for batch in scanner.scan_directory(src_path):
                if _reached_limit():
                    break
                for record in batch:
                    if _reached_limit():
                        break
                    # cv2 can only decode raster images; skip videos/RAW.
                    if record.extension not in image_exts:
                        continue
                    if record.path in done:
                        continue
                    stats["scanned"] += 1

                    img = cv2.imread(record.path)
                    if img is None:
                        stats["skipped"] += 1
                        continue

                    # Lazy-init detector (first use downloads/loads the ONNX
                    # model). Keep it off the path when nothing decodes.
                    if detector is None:
                        detector = FaceDetector()

                    h, w = img.shape[:2]
                    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    faces = detector.detect(rgb)

                    # file_hash / embedding_id need to fit String(64). The path
                    # itself can exceed that, so key off a sha1 of the path
                    # (40 hex chars, leaving room for the ":{idx}" face suffix).
                    path_hash = hashlib.sha1(record.path.encode("utf-8")).hexdigest()

                    sess = Session()
                    try:
                        image_row = FtImage(
                            file_path=record.path, file_hash=path_hash,
                            file_size=int(record.size), file_mtime=record.mtime,
                            width=w, height=h, status="completed", is_video=False,
                            face_count=len(faces),
                        )
                        sess.add(image_row)
                        sess.flush()  # assign image_row.id
                        for idx, f in enumerate(faces):
                            if f.embedding is None:
                                continue
                            x1, y1, x2, y2 = [int(v) for v in f.bbox]
                            face_row = FtFace(
                                image_id=image_row.id,
                                embedding_id=f"{path_hash}:{idx}",
                                bbox_x1=x1 / w, bbox_y1=y1 / h, bbox_x2=x2 / w, bbox_y2=y2 / h,
                                bbox_px_x1=x1, bbox_px_y1=y1, bbox_px_x2=x2, bbox_px_y2=y2,
                                quality_score=float(f.quality_score),
                                laplacian_variance=float(f.laplacian_variance),
                                face_area_percent=float(f.area_ratio * 100.0),
                                detection_confidence=float(f.confidence),
                                embedding_vec=f.embedding.tolist(),
                            )
                            sess.add(face_row)
                            stats["faces"] += 1
                        sess.commit()
                        stats["images_indexed"] += 1
                        done.add(record.path)
                    except Exception:
                        sess.rollback()
                        logger.exception("drive ingest failed for %s", record.path)
                        stats["skipped"] += 1
                    finally:
                        sess.close()
    finally:
        analyzer_engine.dispose()

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

    stats = {"faces_checked": 0, "linked": 0}
    analyzer_engine = create_engine(
        analyzer_sqlalchemy_url(),
        connect_args={"options": f"-csearch_path={FACE_DB_SCHEMA},public"},
        pool_pre_ping=True,
    )
    collector_engine = create_engine(_collector_sqlalchemy_url(), pool_pre_ping=True)
    _wait_for_db(analyzer_engine)
    _wait_for_db(collector_engine)
    try:
        with analyzer_engine.connect() as aconn:
            lookup = _build_entity_lookup(aconn)
            q = (
                "SELECT f.id AS face_id, i.file_hash AS mid, f.quality_score AS q "
                "FROM faces f JOIN images i ON i.id = f.image_id "
                "WHERE i.file_hash ~* '^[0-9a-f-]{36}$' "
                "AND NOT EXISTS (SELECT 1 FROM public.entity_faces ef WHERE ef.face_id = f.id)"
            )
            if limit:
                q += f" LIMIT {int(limit)}"
            faces = aconn.execute(text(q)).fetchall()
        stats["faces_checked"] = len(faces)
        if not faces:
            return stats

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
    finally:
        analyzer_engine.dispose()
        collector_engine.dispose()
    return stats


def loop(batch: int, interval: int) -> None:
    """Run ingest_collector_media in a continuous loop (the intended end-state:
    a separate long-running worker process, see docs/facetracker_merge_plan.md §6).

    Each tick indexes up to `batch` un-indexed collector images, then sleeps
    `interval` seconds. Idempotent + resumable (ingest dedupes by file_path), so
    a tick that finds nothing new (e.g. while collector source media is still
    being restored — task B1) is a cheap no-op. Errors are logged, not fatal.

    Drive scanning: when DRIVE_SOURCES is configured, every `drive_every`th tick
    also runs ingest_drive_media over the mounted W/X/Y/Z drives. It's on a
    slower cadence (FACE_WORKER_DRIVE_INTERVAL, default 1h) because a full drive
    walk — especially over the SMB W:/X: shares — is far heavier than the
    collector DB query. TODO(handoff): the walk re-stats already-indexed files
    each pass; add an mtime/cursor optimization if SMB walk cost becomes a
    problem.
    """
    import time
    drive_limit = int(os.getenv("FACE_WORKER_DRIVE_BATCH", "200"))
    drive_interval = int(os.getenv("FACE_WORKER_DRIVE_INTERVAL", "3600"))
    drive_every = max(1, drive_interval // max(1, interval))
    # Re-attribution cadence: faces are bridged at index time, but entity links
    # keep growing, so periodically re-link already-indexed faces that have since
    # become resolvable. Cheaper than the drive walk; every ~30min by default.
    relink_interval = int(os.getenv("FACE_WORKER_RELINK_INTERVAL", "1800"))
    relink_every = max(1, relink_interval // max(1, interval))
    logger.info(
        "Face worker loop: batch=%d interval=%ds | drive_batch=%d drive_every=%d | relink_every=%d ticks",
        batch, interval, drive_limit, drive_every, relink_every,
    )
    tick = 0
    while True:
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

        if tick % drive_every == 0:
            try:
                dstats = ingest_drive_media(drive_limit)
                if dstats.get("scanned") or dstats.get("images_indexed"):
                    logger.info("drive ingest tick: %s", dstats)
            except Exception:
                logger.exception("drive ingest tick failed (will retry next cycle)")

        tick += 1
        time.sleep(interval)


def main() -> None:
    import os
    import sys
    logger.info("Face worker (schema=%s)", FACE_DB_SCHEMA)
    tables = init_schema()
    logger.info("facetracker schema tables present: %s", tables)
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "ingest":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        logger.info("Stage 2 ingest (limit=%d)…", limit)
        logger.info("ingest stats: %s", ingest_collector_media(limit))
    elif cmd == "scan":
        # One-shot drive scan over DRIVE_SOURCES (W/X/Y/Z mounts). Used for
        # testing/manual backfill; the `loop` command runs it on a cadence.
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        logger.info("Drive scan (limit=%d)…", limit)
        logger.info("drive scan stats: %s", ingest_drive_media(limit))
    elif cmd == "relink":
        # One-shot backfill of public.entity_faces for already-indexed faces whose
        # media_item now resolves to an entity (limit optional; default all).
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
        logger.info("entity_faces relink (limit=%s)…", limit)
        logger.info("relink stats: %s", relink_entity_faces(limit))
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
