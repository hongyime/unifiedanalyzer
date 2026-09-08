"""
Pure-function tests — batch 42.

Covers previously untested modules:
- face.storage.database: Database class (constructor/initial-state), Identity/FaceIdentityMap
  dataclass-like ORM model field presence, Base is declarative
- face.pipeline.thumbnail: ThumbnailGenerator (constructor, field defaults)
- face.discovery.manifest: FileManifestManager (constructor, _load_manifest default shape,
  needs_processing JSON-fallback path without DB)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# face.storage.database: Database constructor + model class presence
# ---------------------------------------------------------------------------

class TestDatabaseClass:
    def test_constructor_stores_url(self):
        from src.face.storage.database import Database
        db = Database("postgres://u:p@localhost/db")
        assert db.database_url == "postgres://u:p@localhost/db"

    def test_engine_initially_none(self):
        from src.face.storage.database import Database
        db = Database("postgres://u:p@localhost/db")
        assert db.engine is None

    def test_session_local_initially_none(self):
        from src.face.storage.database import Database
        db = Database("postgres://u:p@localhost/db")
        assert db.SessionLocal is None

    def test_get_session_raises_when_not_connected(self):
        from src.face.storage.database import Database
        db = Database("postgres://u:p@localhost/db")
        import pytest
        with pytest.raises(RuntimeError):
            db.get_session()


class TestFaceDatabaseModels:
    def test_base_is_declarative(self):
        from src.face.storage.database import Base
        assert hasattr(Base, "metadata")

    def test_image_model_has_expected_columns(self):
        from src.face.storage.database import Image
        cols = {c.key for c in Image.__table__.columns}
        for col in ("id", "file_path", "file_hash", "status", "face_count"):
            assert col in cols, f"Image.{col} missing"

    def test_face_model_has_expected_columns(self):
        from src.face.storage.database import Face
        cols = {c.key for c in Face.__table__.columns}
        for col in ("id", "image_id", "embedding_id", "quality_score"):
            assert col in cols, f"Face.{col} missing"

    def test_identity_model_has_expected_columns(self):
        from src.face.storage.database import Identity
        cols = {c.key for c in Identity.__table__.columns}
        for col in ("id", "name", "is_verified"):
            assert col in cols, f"Identity.{col} missing"

    def test_face_identity_map_has_expected_columns(self):
        from src.face.storage.database import FaceIdentityMap
        cols = {c.key for c in FaceIdentityMap.__table__.columns}
        for col in ("id", "face_id", "identity_id", "is_primary"):
            assert col in cols, f"FaceIdentityMap.{col} missing"

    def test_image_tablename(self):
        from src.face.storage.database import Image
        assert Image.__tablename__ == "images"

    def test_face_tablename(self):
        from src.face.storage.database import Face
        assert Face.__tablename__ == "faces"



# ---------------------------------------------------------------------------
# face.discovery.manifest: FileManifestManager constructor + JSON fallback
# ---------------------------------------------------------------------------

class TestFileManifestManager:
    def _make(self, tmp_path):
        from src.face.config import Settings
        from src.face.discovery.manifest import FileManifestManager
        cfg = Settings.model_construct(
            face_storage_root=str(tmp_path),
            supported_images=".jpg",
            supported_raw=".cr2",
            supported_videos=".mp4",
            onedrive_enabled=False,
        )
        return FileManifestManager(cfg)

    def test_db_engine_initially_none(self, tmp_path):
        m = self._make(tmp_path)
        assert m._db_engine is None

    def test_manifest_path_set(self, tmp_path):
        m = self._make(tmp_path)
        assert "file_manifest.json" in str(m.manifest_path)

    def test_wire_db_sets_engine(self, tmp_path):
        from unittest.mock import MagicMock
        m = self._make(tmp_path)
        engine = MagicMock()
        m.wire_db(engine)
        assert m._db_engine is engine

    def test_load_manifest_default_shape(self, tmp_path):
        m = self._make(tmp_path)
        manifest = m._load_manifest()
        assert "files" in manifest
        assert "deleted" in manifest
        assert "last_updated" in manifest
        assert isinstance(manifest["files"], dict)

    def test_needs_processing_true_for_new_file(self, tmp_path):
        m = self._make(tmp_path)
        # File not in manifest → needs processing
        assert m.needs_processing("/some/new/photo.jpg", 1700000000.0, 1024) is True

    def test_needs_processing_false_for_processed_unchanged(self, tmp_path):
        m = self._make(tmp_path)
        m.add_file("/photo.jpg", "hash123", 1024, 1700000000.0, is_processed=True)
        # Same mtime (+/- 0.5s) and same size → no reprocessing needed
        assert m.needs_processing("/photo.jpg", 1700000000.0, 1024) is False

    def test_needs_processing_true_when_size_changed(self, tmp_path):
        m = self._make(tmp_path)
        m.add_file("/photo.jpg", "hash123", 1024, 1700000000.0, is_processed=True)
        # Different size → needs reprocessing
        assert m.needs_processing("/photo.jpg", 1700000000.0, 2048) is True

    def test_needs_processing_true_when_mtime_changed(self, tmp_path):
        m = self._make(tmp_path)
        m.add_file("/photo.jpg", "hash123", 1024, 1700000000.0, is_processed=True)
        # mtime changed by > 1s → needs reprocessing
        assert m.needs_processing("/photo.jpg", 1700000010.0, 1024) is True
