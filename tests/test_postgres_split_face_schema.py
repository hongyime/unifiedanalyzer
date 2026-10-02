"""Guards for the W2/W9 Postgres-split changes to the analyzer.

Pure source-text assertions (no DB, no imports of heavy modules):
  * entity_faces carries the GAN synthetic-avatar columns moved from the
    collector repo, plus the face_gan_overrides operator table.
  * face_worker no longer scans host drives (A1): collector media_items only.
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _REPO_ROOT / "src" / "db" / "schema.sql"
_FACE_WORKER = _REPO_ROOT / "src" / "face_worker.py"


def _schema() -> str:
    return _SCHEMA.read_text(encoding="utf-8")


def _face_worker() -> str:
    return _FACE_WORKER.read_text(encoding="utf-8")


def test_entity_faces_has_gan_columns():
    src = _schema()
    assert "ADD COLUMN IF NOT EXISTS gan_score REAL NULL" in src
    assert "ADD COLUMN IF NOT EXISTS gan_model_version TEXT NULL" in src
    assert "ADD COLUMN IF NOT EXISTS gan_scored_at TIMESTAMPTZ NULL" in src
    assert "idx_entity_faces_gan_score_high" in src


def test_face_gan_overrides_table_declared():
    src = _schema()
    assert "CREATE TABLE IF NOT EXISTS face_gan_overrides" in src
    assert "is_synthetic  BOOLEAN     NOT NULL" in src


def test_gan_one_shot_migration_present():
    migration = _REPO_ROOT / "src" / "db" / "migrations" / "004_add_gan_face_columns.sql"
    assert migration.exists()
    body = migration.read_text(encoding="utf-8")
    assert "ALTER TABLE entity_faces" in body
    assert "face_gan_overrides" in body


def test_face_worker_has_no_drive_scanning():
    # A1: faces come only from collector media_items. The drive walk, its EXIF
    # helper, the loop drive-tick, and the scan CLI command are all gone.
    src = _face_worker()
    assert "ingest_drive_media" not in src
    assert "_store_drive_exif" not in src
    assert "DRIVE_SOURCES" not in src
    assert "DriveScanner" not in src


def test_face_worker_still_ingests_collector_media():
    src = _face_worker()
    assert "def ingest_collector_media(" in src
    assert "def loop(" in src
