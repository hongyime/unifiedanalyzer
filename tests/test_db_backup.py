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


def test_backup_config_allows_missing_database_for_listing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv("ANALYZER_DATABASE_URL", raising=False)
    monkeypatch.setenv("ANALYZER_DB_BACKUP_DIR", os.fspath(tmp_path))

    config = BackupConfig.from_env(allow_missing_database_url=True)

    assert config.root == tmp_path
    assert config.database_url == "postgres://unused@127.0.0.1/postgres"
