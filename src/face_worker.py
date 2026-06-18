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


def main() -> None:
    import sys
    logger.info("Face worker (schema=%s)", FACE_DB_SCHEMA)
    tables = init_schema()
    logger.info("facetracker schema tables present: %s", tables)
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        logger.info("Stage 2 ingest (limit=%d)…", limit)
        logger.info("ingest stats: %s", ingest_collector_media(limit))
    else:
        logger.info("Schema ready. Run `python -m src.face_worker ingest [N]` to index collector media.")


if __name__ == "__main__":
    main()
