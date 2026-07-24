from __future__ import annotations

import subprocess
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.pipeline import recovery_drill as rd


NOW = datetime(2026, 7, 23, 12, 34, 56, tzinfo=timezone.utc)


def _write_backup(root: Path, kind: str, stamp: str, data: bytes = b"dump") -> Path:
    path = root / kind / f"unifiedanalyzer_{kind}_{stamp}.dump"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_default_scratch_database_name_is_guarded():
    name = rd.default_scratch_database_name(NOW)

    assert name == "ua_restore_drill_20260723_123456"
    assert rd.validate_scratch_database_name(name) == name


@pytest.mark.parametrize("name", ["unifiedanalyzer", "postgres", "ua_restore", "ua_restore_drill_bad-char!"])
def test_validate_scratch_database_name_rejects_unsafe_names(name: str):
    with pytest.raises(rd.RecoveryDrillError):
        rd.validate_scratch_database_name(name)


def test_select_backup_path_uses_newest_nonempty_dump(tmp_path: Path):
    older = _write_backup(tmp_path, "daily", "20260720T010000Z")
    latest = _write_backup(tmp_path, "daily", "20260723T010000Z")
    _write_backup(tmp_path, "weekly", "20260724T010000Z", data=b"")

    config = rd.RecoveryDrillConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        backup_dir=tmp_path,
    )

    assert rd.select_backup_path(config) == latest
    assert older.exists()


def test_pg_restore_command_does_not_put_password_in_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dump = _write_backup(tmp_path, "daily", "20260723T010000Z")
    monkeypatch.setattr(rd.shutil, "which", lambda name: name if name == "pg_restore" else None)
    config = rd.RecoveryDrillConfig(
        database_url="postgres://collector:s3cret@localhost:5500/unifiedanalyzer?sslmode=disable",
        backup_dir=tmp_path,
    )

    cmd, env = rd.pg_restore_command(config, dump, "ua_restore_drill_20260723_123456")

    joined = " ".join(cmd)
    assert "s3cret" not in joined
    assert env["PGPASSWORD"] == "s3cret"
    assert env["PGSSLMODE"] == "disable"
    assert "--dbname" in cmd
    assert "ua_restore_drill_20260723_123456" in cmd
    assert str(dump) in cmd


def test_pg_restore_command_uses_restore_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dump = _write_backup(tmp_path, "daily", "20260723T010000Z")
    restore_list = tmp_path / "restore.list"
    restore_list.write_text("1; INDEX public idx_x owner\n", encoding="utf-8")
    monkeypatch.setattr(rd.shutil, "which", lambda name: name if name == "pg_restore" else None)
    config = rd.RecoveryDrillConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        backup_dir=tmp_path,
    )

    cmd, _env = rd.pg_restore_command(
        config,
        dump,
        "ua_restore_drill_20260723_123456",
        use_list_path=restore_list,
    )

    assert "--use-list" in cmd
    assert str(restore_list) in cmd
    assert cmd[-1] == str(dump)


def test_run_pg_restore_filters_default_derived_hnsw_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dump = _write_backup(tmp_path, "daily", "20260723T010000Z")
    monkeypatch.setattr(rd.shutil, "which", lambda name: name if name == "pg_restore" else None)
    restore_list = "\n".join([
        "1; 1259 111 TABLE public entities collector",
        "2; 1259 222 INDEX public idx_timeline_emb_hnsw collector",
        "3; 1259 333 INDEX public idx_entities_source collector",
    ])
    seen_restore_list = {}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["pg_restore", "-l"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=restore_list, stderr="")
        if "--use-list" in cmd:
            path = Path(cmd[cmd.index("--use-list") + 1])
            seen_restore_list["text"] = path.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rd.subprocess, "run", fake_run)
    config = rd.RecoveryDrillConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        backup_dir=tmp_path,
    )

    skipped = rd._run_pg_restore(config, dump, "ua_restore_drill_20260723_123456")

    assert skipped == ["2; 1259 222 INDEX public idx_timeline_emb_hnsw collector"]
    assert "idx_timeline_emb_hnsw" not in seen_restore_list["text"]
    assert "idx_entities_source" in seen_restore_list["text"]


def test_run_pg_restore_raises_useful_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dump = _write_backup(tmp_path, "daily", "20260723T010000Z")
    monkeypatch.setattr(rd.shutil, "which", lambda name: name if name == "pg_restore" else None)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="bad archive")

    monkeypatch.setattr(rd.subprocess, "run", fake_run)
    config = rd.RecoveryDrillConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        backup_dir=tmp_path,
        skip_restore_item_patterns=(),
    )

    with pytest.raises(rd.RecoveryDrillError, match="bad archive"):
        rd._run_pg_restore(config, dump, "ua_restore_drill_20260723_123456")


def test_run_recovery_drill_reports_restore_failure_and_drops_scratch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dump = _write_backup(tmp_path, "daily", "20260723T010000Z")
    calls: list[str] = []

    async def fake_create(_database_url: str, scratch_database: str):
        calls.append(f"create:{scratch_database}")

    async def fake_drop(_database_url: str, scratch_database: str):
        calls.append(f"drop:{scratch_database}")

    def fake_restore(_config, _backup_path, _scratch_database):
        raise rd.RecoveryDrillError("restore blew up")

    monkeypatch.setattr(rd, "_create_scratch_database", fake_create)
    monkeypatch.setattr(rd, "_drop_scratch_database", fake_drop)
    monkeypatch.setattr(rd, "_run_pg_restore", fake_restore)
    config = rd.RecoveryDrillConfig(
        database_url="postgres://collector:collector@localhost:5500/unifiedanalyzer",
        backup_dir=tmp_path,
        backup_path=dump,
        scratch_database="ua_restore_drill_20260723_123456",
    )

    report = asyncio.run(rd.run_recovery_drill(config))

    assert report.restored is False
    assert report.error == "restore blew up"
    assert "recovery drill failed: restore blew up" in report.gaps
    assert report.dropped_scratch is True
    assert calls == [
        "create:ua_restore_drill_20260723_123456",
        "drop:ua_restore_drill_20260723_123456",
    ]


def test_transaction_timeout_restore_warning_is_tolerated():
    detail = """
pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter "transaction_timeout"
Command was: SET transaction_timeout = 0;
pg_restore: warning: errors ignored on restore: 1
"""

    assert rd._is_only_transaction_timeout_restore_warning(detail) is True
    assert rd._is_only_transaction_timeout_restore_warning(detail + "\nERROR: relation x already exists") is False


def test_gaps_from_replay_surfaces_unresolved_and_derived_work():
    gaps = rd.gaps_from_replay({
        "effect_unsupported": 2,
        "effect_skipped_unresolved": 1,
        "replay": {
            "unresolved": 3,
            "ambiguous": 1,
            "invalid": 0,
            "derived_rebuild_required": {
                "identity_scores": 4,
                "timeline_events": 2,
            },
        },
    })

    assert "3 decision event(s) could not resolve stable references" in gaps
    assert "1 decision event(s) resolved to multiple entities" in gaps
    assert "1 decision effect(s) skipped because references were unresolved" in gaps
    assert "2 decision effect(s) still require derived-table rebuild or manual replay" in gaps
    assert "derived rebuild required: identity_scores (4)" in gaps
