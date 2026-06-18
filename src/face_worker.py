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


def main() -> None:
    logger.info("Face worker — Stage 1: schema init (schema=%s)", FACE_DB_SCHEMA)
    tables = init_schema()
    logger.info("facetracker schema tables present: %s", tables)
    logger.info("Schema init complete. Stage 2 (collector-media ingest) not yet implemented.")


if __name__ == "__main__":
    main()
