from pathlib import Path

from src.pipeline import media_common


def _set_media_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    collector_root = tmp_path / "collector_root"
    derived_root = tmp_path / "media_derived"
    (collector_root / "media").mkdir(parents=True)
    derived_root.mkdir()

    monkeypatch.setattr(media_common, "COLLECTOR_MEDIA_ROOT", str(collector_root))
    monkeypatch.setattr(
        media_common,
        "_MEDIA_CONFINEMENT_ROOT",
        (collector_root / "media").resolve(),
    )
    monkeypatch.setattr(media_common, "MEDIA_DERIVED_PATH", derived_root.resolve())
    return collector_root, derived_root


def test_resolve_media_path_accepts_container_media_path(monkeypatch, tmp_path):
    collector_root, _derived_root = _set_media_roots(monkeypatch, tmp_path)
    image = collector_root / "media" / "telegram" / "a.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    assert media_common.resolve_media_path("/media/telegram/a.jpg") == image.resolve()


def test_resolve_media_path_accepts_vault_media_blob(monkeypatch, tmp_path):
    collector_root, _derived_root = _set_media_roots(monkeypatch, tmp_path)
    blob = collector_root / "media" / "blobs" / "ab" / "cd" / "abcd.jpg"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"image")

    assert media_common.resolve_media_path("/vault/media/blobs/ab/cd/abcd.jpg") == blob.resolve()


def test_resolve_media_path_accepts_windows_collector_path(monkeypatch, tmp_path):
    collector_root, _derived_root = _set_media_roots(monkeypatch, tmp_path)
    image = collector_root / "media" / "instagram" / "photo.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    assert (
        media_common.resolve_media_path(r"Z:\unifiedcollector\media\instagram\photo.jpg")
        == image.resolve()
    )


def test_resolve_media_path_keeps_derived_paths_confined(monkeypatch, tmp_path):
    _collector_root, derived_root = _set_media_roots(monkeypatch, tmp_path)
    frame = derived_root / "video_frames" / "frame.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")

    assert (
        media_common.resolve_media_path("Z:/unifiedanalyzer/media_derived/video_frames/frame.jpg")
        == frame.resolve()
    )


def test_resolve_media_path_rejects_collector_traversal(monkeypatch, tmp_path):
    collector_root, _derived_root = _set_media_roots(monkeypatch, tmp_path)
    secret = collector_root / "secret.jpg"
    secret.write_bytes(b"secret")

    assert media_common.resolve_media_path("/media/../secret.jpg") is None
