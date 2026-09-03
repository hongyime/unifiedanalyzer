from __future__ import annotations
# ruff: noqa: E402

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from src.db.backup import (
    BackupConfig,
    due_backup_kinds,
    list_backups,
    prune_backups,
    run_backup_kinds,
    run_due_backups,
)


NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _write_backup(root: Path, kind: str, stamp: str, data: bytes = b"dump") -> Path:
    path = root / kind / f"unifiedanalyzer_{kind}_{stamp}.dump"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_due_backup_kinds_uses_current_calendar_periods(tmp_path: Path):
    _write_backup(tmp_path, "daily", "20260720T010000Z")
    _write_backup(tmp_path, "weekly", "20260713T010000Z")
    _write_backup(tmp_path, "monthly", "20260701T010000Z")

    assert due_backup_kinds(list_backups(tmp_path), NOW) == ("weekly",)


def test_run_due_backups_dry_run_plans_all_missing_kinds(tmp_path: Path):
    config = BackupConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        root=tmp_path,
    )

    result = run_due_backups(config, now=NOW, dry_run=True)

    assert result.created == ()
    assert result.deleted == ()
    assert [path.parent.name for path in result.would_create] == [
        "daily",
        "weekly",
        "monthly",
    ]
    assert not list(tmp_path.rglob("*"))


def test_retention_prunes_oldest_by_kind(tmp_path: Path):
    for day in range(1, 10):
        _write_backup(tmp_path, "daily", f"202607{day:02d}T010000Z")
    config = BackupConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        root=tmp_path,
        retention={"daily": 7, "weekly": 4, "monthly": 3},
    )

    stale = prune_backups(config, dry_run=True)

    assert [path.name for path in stale] == [
        "unifiedanalyzer_daily_20260702T010000Z.dump",
        "unifiedanalyzer_daily_20260701T010000Z.dump",
    ]
    deleted = prune_backups(config, dry_run=False)
    assert deleted == stale
    assert len(list_backups(tmp_path)) == 7


def test_dry_run_retention_accounts_for_planned_backup(tmp_path: Path):
    old = _write_backup(tmp_path, "daily", "20260719T010000Z")
    config = BackupConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        root=tmp_path,
        retention={"daily": 1, "weekly": 4, "monthly": 3},
    )

    result = run_backup_kinds(config, ("daily",), now=NOW, dry_run=True)

    assert result.would_delete == (old,)
    assert old.exists()


def test_run_backup_kinds_invokes_pg_dump_without_password_in_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import src.db.backup as backup

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        output = Path(cmd[cmd.index("--file") + 1])
        output.write_bytes(b"custom-format-dump")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(backup.shutil, "which", lambda _: "pg_dump")
    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    config = BackupConfig(
        database_url="postgres://collector:s3cret@localhost:5500/unifiedanalyzer?sslmode=disable",
        root=tmp_path,
        retention={"daily": 7, "weekly": 4, "monthly": 3},
    )
    result = run_backup_kinds(config, ("daily",), now=NOW, apply_retention=False)

    assert len(result.created) == 1
    assert result.created[0].exists()
    assert captured["env"]["PGPASSWORD"] == "s3cret"
    assert captured["env"]["PGSSLMODE"] == "disable"
    assert "s3cret" not in " ".join(str(part) for part in captured["cmd"])
    assert "--format=custom" in captured["cmd"]
    assert "--dbname" in captured["cmd"]
    assert "unifiedanalyzer" in captured["cmd"]
    assert "--exclude-table-data" in captured["cmd"]
    assert "public.timeline_embeddings" in captured["cmd"]
    assert "--compress" in captured["cmd"]
    assert "gzip:1" in captured["cmd"]


def test_run_backup_kinds_records_durable_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    import src.db.backup as backup

    statements = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            statements.append((sql, params))

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("--file") + 1])
        output.write_bytes(b"custom-format-dump")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(backup, "_connect_state_db", lambda _config: FakeConn())
    monkeypatch.setattr(backup.shutil, "which", lambda name: name if name == "pg_dump" else None)
    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    config = BackupConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        root=tmp_path,
        retention={"daily": 7, "weekly": 4, "monthly": 3},
    )

    result = run_backup_kinds(config, ("daily",), now=NOW, apply_retention=False)

    assert result.created[0].stat().st_size == len(b"custom-format-dump")
    assert any("INSERT INTO analyzer_backup_runs" in sql for sql, _ in statements)
    update = next(params for sql, params in statements if "UPDATE analyzer_backup_runs" in sql)
    assert update[0] == "success"
    assert update[2] == len(b"custom-format-dump")
    assert update[4] == "skipped: pg_restore binary not found"


def test_backup_config_from_env_uses_retention_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(
        "ANALYZER_DATABASE_URL",
        "postgres://collector:collector@localhost:5500/unifiedanalyzer",
    )
    monkeypatch.setenv("ANALYZER_DB_BACKUP_DIR", os.fspath(tmp_path))

    config = BackupConfig.from_env()

    assert config.root == tmp_path
    assert config.retention_for("daily") == 7
    assert config.retention_for("weekly") == 4
    assert config.retention_for("monthly") == 3
    assert config.exclude_table_data == (
        "public.timeline_embeddings",
        "public.timeline_text_features",
    )
    assert config.compression == "gzip:1"


def test_backup_config_allows_full_dump_by_clearing_excluded_table_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv(
        "ANALYZER_DATABASE_URL",
        "postgres://collector:collector@localhost:5500/unifiedanalyzer",
    )
    monkeypatch.setenv("ANALYZER_DB_BACKUP_DIR", os.fspath(tmp_path))
    monkeypatch.setenv("ANALYZER_DB_BACKUP_EXCLUDE_TABLE_DATA", "")

    config = BackupConfig.from_env()

    assert config.exclude_table_data == ()


def test_backup_config_allows_uncompressed_dump(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv(
        "ANALYZER_DATABASE_URL",
        "postgres://collector:collector@localhost:5500/unifiedanalyzer",
    )
    monkeypatch.setenv("ANALYZER_DB_BACKUP_DIR", os.fspath(tmp_path))
    monkeypatch.setenv("ANALYZER_DB_BACKUP_COMPRESSION", "")

    config = BackupConfig.from_env()

    assert config.compression is None


def test_backup_config_allows_missing_database_for_listing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv("ANALYZER_DATABASE_URL", raising=False)
    monkeypatch.setenv("ANALYZER_DB_BACKUP_DIR", os.fspath(tmp_path))

    config = BackupConfig.from_env(allow_missing_database_url=True)

    assert config.root == tmp_path
    assert config.database_url == "postgres://unused@127.0.0.1/postgres"


def test_prune_backups_sweeps_orphaned_tmp_files(tmp_path: Path):
    # A healthy dump within retention plus orphaned .dump.tmp leftovers from
    # dumps that were interrupted before tmp->final rename (unique timestamps
    # mean the same-name cleanup in _run_pg_dump never removes these).
    keep = _write_backup(tmp_path, "daily", "20260720T010000Z")
    orphan1 = tmp_path / "daily" / "unifiedanalyzer_daily_20260719T010000Z.dump.tmp"
    orphan1.parent.mkdir(parents=True, exist_ok=True)
    orphan1.write_bytes(b"partial-dump")
    orphan2 = tmp_path / "weekly" / "unifiedanalyzer_weekly_20260713T010000Z.dump.tmp"
    orphan2.parent.mkdir(parents=True, exist_ok=True)
    orphan2.write_bytes(b"partial-dump")
    config = BackupConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        root=tmp_path,
        retention={"daily": 7, "weekly": 4, "monthly": 3},
    )

    # A dry run must never touch the filesystem.
    prune_backups(config, dry_run=True)
    assert orphan1.exists()
    assert orphan2.exists()

    # A real prune sweeps orphaned .tmp files but keeps real dumps.
    prune_backups(config, dry_run=False)
    assert not orphan1.exists()
    assert not orphan2.exists()
    assert keep.exists()
