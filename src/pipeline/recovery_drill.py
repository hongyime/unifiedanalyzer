from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from src.db.backup import BackupConfig, BackupError, list_backups, _parse_database_url
from src.pipeline.decision_replay import apply_decision_replay


SCRATCH_DB_RE = re.compile(r"^ua_restore_drill_[a-z0-9_]{8,80}$")
DEFAULT_SKIP_RESTORE_ITEM_PATTERNS = ("idx_timeline_emb_hnsw",)


class RecoveryDrillError(RuntimeError):
    """Raised when the analyzer recovery drill cannot safely continue."""


@dataclass(frozen=True)
class RecoveryDrillConfig:
    database_url: str
    backup_dir: Path
    backup_path: Path | None = None
    scratch_database: str | None = None
    pg_restore_bin: str = "pg_restore"
    decision_log_dir: Path | None = None
    decision_limit: int | None = None
    keep_scratch: bool = False
    dry_run: bool = False
    skip_restore_item_patterns: tuple[str, ...] = DEFAULT_SKIP_RESTORE_ITEM_PATTERNS


@dataclass
class RecoveryDrillReport:
    backup_path: str | None = None
    scratch_database: str | None = None
    dry_run: bool = False
    restored: bool = False
    restore_seconds: float | None = None
    dropped_scratch: bool = False
    kept_scratch: bool = False
    error: str | None = None
    skipped_restore_items: list[str] = field(default_factory=list)
    table_counts: dict[str, int | None] = field(default_factory=dict)
    replay_apply: dict[str, Any] | None = None
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_path": self.backup_path,
            "scratch_database": self.scratch_database,
            "dry_run": self.dry_run,
            "restored": self.restored,
            "restore_seconds": self.restore_seconds,
            "dropped_scratch": self.dropped_scratch,
            "kept_scratch": self.kept_scratch,
            "error": self.error,
            "skipped_restore_items": self.skipped_restore_items,
            "table_counts": self.table_counts,
            "replay_apply": self.replay_apply,
            "gaps": self.gaps,
        }

    def to_text(self) -> str:
        lines = [
            "Analyzer recovery drill",
            f"Backup: {self.backup_path or 'none'}",
            f"Scratch DB: {self.scratch_database or 'none'}",
            f"Dry run: {'yes' if self.dry_run else 'no'}",
            f"Restored: {'yes' if self.restored else 'no'}",
        ]
        if self.restore_seconds is not None:
            lines.append(f"Restore time: {self.restore_seconds:.1f}s")
        if self.error:
            lines.append(f"Error: {self.error}")
        if self.skipped_restore_items:
            lines.append("")
            lines.append("Skipped restore items:")
            lines.extend(f"  - {item}" for item in self.skipped_restore_items)
        if self.table_counts:
            lines.append("")
            lines.append("Scratch table counts:")
            lines.extend(f"  {name}: {count}" for name, count in sorted(self.table_counts.items()))
        if self.replay_apply:
            lines.append("")
            lines.append("Decision replay:")
            lines.append(
                f"  audit applied={self.replay_apply.get('audit_applied', 0)}; "
                f"existing={self.replay_apply.get('audit_skipped_existing', 0)}; "
                f"safe effects={self.replay_apply.get('effect_applied', 0)}; "
                f"unsupported={self.replay_apply.get('effect_unsupported', 0)}"
            )
            replay = self.replay_apply.get("replay") or {}
            lines.append(
                f"  scanned={replay.get('scanned', 0)}; "
                f"restorable={replay.get('restorable', 0)}; "
                f"unresolved={replay.get('unresolved', 0)}; "
                f"ambiguous={replay.get('ambiguous', 0)}; "
                f"invalid={replay.get('invalid', 0)}"
            )
        if self.gaps:
            lines.append("")
            lines.append("Recovery gaps:")
            lines.extend(f"  - {gap}" for gap in self.gaps)
        if self.kept_scratch:
            lines.append("")
            lines.append("Scratch DB kept for inspection.")
        elif self.dropped_scratch:
            lines.append("")
            lines.append("Scratch DB dropped after drill.")
        return "\n".join(lines)


def default_scratch_database_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    return f"ua_restore_drill_{stamp}"


def validate_scratch_database_name(name: str) -> str:
    value = str(name or "").strip().lower()
    if not SCRATCH_DB_RE.match(value):
        raise RecoveryDrillError(
            "scratch database name must match "
            f"{SCRATCH_DB_RE.pattern}; refusing unsafe name {name!r}"
        )
    if value in {"unifiedanalyzer", "postgres", "template0", "template1"}:
        raise RecoveryDrillError(f"refusing protected database name {name!r}")
    return value


def select_backup_path(config: RecoveryDrillConfig) -> Path:
    if config.backup_path:
        path = config.backup_path
        if not path.exists() or not path.is_file():
            raise RecoveryDrillError(f"backup path does not exist: {path}")
        if path.stat().st_size <= 0:
            raise RecoveryDrillError(f"backup path is empty: {path}")
        return path
    backups = [item for item in list_backups(config.backup_dir) if item.size_bytes > 0]
    if not backups:
        raise RecoveryDrillError(f"no analyzer backup dumps found under {config.backup_dir}")
    return backups[0].path


def pg_restore_command(
    config: RecoveryDrillConfig,
    backup_path: Path,
    scratch_database: str,
    *,
    use_list_path: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    conn = _parse_database_url(config.database_url)
    restore_bin = _resolve_pg_restore(config.pg_restore_bin)
    cmd = [
        restore_bin,
        "--no-owner",
        "--no-privileges",
        "--dbname",
        scratch_database,
        "--host",
        str(conn["host"]),
        "--port",
        str(conn["port"]),
        "--username",
        str(conn["user"]),
    ]
    if use_list_path:
        cmd.extend(["--use-list", str(use_list_path)])
    cmd.append(str(backup_path))
    env = os.environ.copy()
    if conn["password"]:
        env["PGPASSWORD"] = str(conn["password"])
    if conn.get("sslmode"):
        env["PGSSLMODE"] = str(conn["sslmode"])
    return cmd, env


async def run_recovery_drill(config: RecoveryDrillConfig) -> RecoveryDrillReport:
    backup_path = select_backup_path(config)
    scratch_database = validate_scratch_database_name(
        config.scratch_database or default_scratch_database_name()
    )
    report = RecoveryDrillReport(
        backup_path=str(backup_path),
        scratch_database=scratch_database,
        dry_run=config.dry_run,
        kept_scratch=config.keep_scratch,
    )
    if config.dry_run:
        report.gaps.append("dry run only: backup was selected but not restored")
        return report

    await _create_scratch_database(config.database_url, scratch_database)
    created = True
    try:
        try:
            started = time.monotonic()
            report.skipped_restore_items = await asyncio.to_thread(
                _run_pg_restore,
                config,
                backup_path,
                scratch_database,
            )
            report.restore_seconds = round(time.monotonic() - started, 3)
            report.restored = True
            for item in report.skipped_restore_items:
                report.gaps.append(f"restore skipped derived item: {item}")

            scratch_conn = await _connect_database(config.database_url, scratch_database)
            try:
                report.table_counts = await _table_counts(scratch_conn)
                replay = await apply_decision_replay(
                    scratch_conn,
                    log_dir=config.decision_log_dir,
                    limit=config.decision_limit,
                    require_backup=False,
                )
                report.replay_apply = replay.to_dict()
                report.gaps.extend(gaps_from_replay(report.replay_apply))
            finally:
                await scratch_conn.close()
        except Exception as exc:
            report.error = str(exc)
            report.gaps.append(f"recovery drill failed: {exc}")
    finally:
        if created and not config.keep_scratch:
            await _drop_scratch_database(config.database_url, scratch_database)
            report.dropped_scratch = True
        elif created:
            report.kept_scratch = True
    return report


def gaps_from_replay(replay_apply: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    replay = replay_apply.get("replay") or {}
    unresolved = int(replay.get("unresolved") or 0)
    ambiguous = int(replay.get("ambiguous") or 0)
    invalid = int(replay.get("invalid") or 0)
    unsupported = int(replay_apply.get("effect_unsupported") or 0)
    skipped_unresolved = int(replay_apply.get("effect_skipped_unresolved") or 0)
    if unresolved:
        gaps.append(f"{unresolved} decision event(s) could not resolve stable references")
    if ambiguous:
        gaps.append(f"{ambiguous} decision event(s) resolved to multiple entities")
    if invalid:
        gaps.append(f"{invalid} decision event(s) failed JSONL/schema validation")
    if skipped_unresolved:
        gaps.append(f"{skipped_unresolved} decision effect(s) skipped because references were unresolved")
    if unsupported:
        gaps.append(f"{unsupported} decision effect(s) still require derived-table rebuild or manual replay")
    derived = replay.get("derived_rebuild_required") or {}
    for name, count in sorted(derived.items()):
        gaps.append(f"derived rebuild required: {name} ({count})")
    return gaps


def _resolve_pg_restore(pg_restore_bin: str) -> str:
    resolved = shutil.which(pg_restore_bin)
    if resolved:
        return resolved
    if Path(pg_restore_bin).exists():
        return pg_restore_bin
    raise RecoveryDrillError(f"pg_restore binary not found: {pg_restore_bin}")


def _run_pg_restore(config: RecoveryDrillConfig, backup_path: Path, scratch_database: str) -> list[str]:
    skipped_items: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ua_restore_drill_") as tmp:
        use_list_path: Path | None = None
        if config.skip_restore_item_patterns:
            use_list_path, skipped_items = _write_filtered_restore_list(
                config,
                backup_path,
                Path(tmp) / "restore.list",
            )
        cmd, env = pg_restore_command(
            config,
            backup_path,
            scratch_database,
            use_list_path=use_list_path,
        )
        proc = subprocess.run(
            cmd,
            env=env,
            text=True,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if _is_only_transaction_timeout_restore_warning(detail):
            return skipped_items
        raise RecoveryDrillError(f"pg_restore failed with exit code {proc.returncode}: {detail}")
    return skipped_items


def _write_filtered_restore_list(
    config: RecoveryDrillConfig,
    backup_path: Path,
    list_path: Path,
) -> tuple[Path | None, list[str]]:
    restore_bin = _resolve_pg_restore(config.pg_restore_bin)
    proc = subprocess.run(
        [restore_bin, "-l", str(backup_path)],
        text=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RecoveryDrillError(f"pg_restore list failed with exit code {proc.returncode}: {detail}")

    patterns = tuple(p.lower() for p in config.skip_restore_item_patterns if p)
    kept: list[str] = []
    skipped: list[str] = []
    for line in proc.stdout.splitlines():
        normalized = line.lower()
        if " index " in f" {normalized} " and any(pattern in normalized for pattern in patterns):
            skipped.append(line.strip())
            continue
        kept.append(line)
    if not skipped:
        return None, []
    list_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return list_path, skipped


def _is_only_transaction_timeout_restore_warning(detail: str) -> bool:
    """Postgres client 17 emits SET transaction_timeout for older servers.

    The target server rejects only that session setting, then restore continues.
    Treat exactly that version-skew warning as non-fatal; any other ERROR/FATAL
    still fails the drill.
    """
    if 'unrecognized configuration parameter "transaction_timeout"' not in detail:
        return False
    severe = [
        line
        for line in detail.splitlines()
        if "ERROR:" in line or "FATAL:" in line
    ]
    return all('unrecognized configuration parameter "transaction_timeout"' in line for line in severe)


async def _create_scratch_database(database_url: str, scratch_database: str) -> None:
    admin = await _connect_database(database_url, "postgres")
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", scratch_database)
        if exists:
            raise RecoveryDrillError(f"scratch database already exists: {scratch_database}")
        await admin.execute(f'CREATE DATABASE "{scratch_database}" TEMPLATE template0')
    finally:
        await admin.close()


async def _drop_scratch_database(database_url: str, scratch_database: str) -> None:
    validate_scratch_database_name(scratch_database)
    admin = await _connect_database(database_url, "postgres")
    try:
        await admin.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1
              AND pid <> pg_backend_pid()
            """,
            scratch_database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch_database}"')
    finally:
        await admin.close()


async def _connect_database(database_url: str, database: str):
    conn = _parse_database_url(database_url)
    return await asyncpg.connect(
        host=conn["host"],
        port=conn["port"],
        user=conn["user"],
        password=conn["password"],
        database=database,
        ssl="disable",
        command_timeout=300,
    )


async def _table_counts(conn) -> dict[str, int | None]:
    tables = [
        "audit_log",
        "entities",
        "entity_platform_links",
        "identity_labels",
        "timeline_events",
        "analysis_runs",
    ]
    counts: dict[str, int | None] = {}
    for table in tables:
        exists = await conn.fetchval("SELECT to_regclass($1)", table)
        if exists:
            counts[table] = int(await conn.fetchval(f"SELECT count(*)::bigint FROM {table}") or 0)
        else:
            counts[table] = None
    return counts


def config_from_env(
    *,
    backup_dir: str | None = None,
    backup_path: str | None = None,
    database_url: str | None = None,
    scratch_database: str | None = None,
    pg_restore_bin: str | None = None,
    decision_log_dir: str | None = None,
    decision_limit: int | None = None,
    keep_scratch: bool = False,
    dry_run: bool = False,
    skip_restore_item_patterns: tuple[str, ...] | list[str] | None = None,
) -> RecoveryDrillConfig:
    backup_config = BackupConfig.from_env(
        database_url=database_url,
        root=backup_dir,
        pg_dump_bin=None,
    )
    if skip_restore_item_patterns is None:
        raw_skip_patterns = os.getenv(
            "ANALYZER_RECOVERY_SKIP_RESTORE_ITEMS",
            ",".join(DEFAULT_SKIP_RESTORE_ITEM_PATTERNS),
        )
        resolved_skip_patterns = tuple(
            item.strip()
            for item in raw_skip_patterns.split(",")
            if item.strip()
        )
    else:
        resolved_skip_patterns = tuple(
            str(item).strip()
            for item in skip_restore_item_patterns
            if str(item).strip()
        )
    return RecoveryDrillConfig(
        database_url=backup_config.database_url,
        backup_dir=backup_config.root,
        backup_path=Path(backup_path) if backup_path else None,
        scratch_database=scratch_database,
        pg_restore_bin=pg_restore_bin or backup_config.pg_restore_bin,
        decision_log_dir=Path(decision_log_dir) if decision_log_dir else None,
        decision_limit=decision_limit,
        keep_scratch=keep_scratch,
        dry_run=dry_run,
        skip_restore_item_patterns=resolved_skip_patterns,
    )


def report_to_json(report: RecoveryDrillReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str)
