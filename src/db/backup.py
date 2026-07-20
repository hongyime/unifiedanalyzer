from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

BackupKind = Literal["daily", "weekly", "monthly"]

BACKUP_KINDS: tuple[BackupKind, ...] = ("daily", "weekly", "monthly")
DEFAULT_RETENTION: dict[BackupKind, int] = {
    "daily": 7,
    "weekly": 4,
    "monthly": 3,
}
DEFAULT_BACKUP_ROOT_WINDOWS = Path(r"Z:\unifiedanalyzer\backups\db")
DEFAULT_BACKUP_ROOT_POSIX = Path("/app/backups/db")
FILENAME_RE = re.compile(
    r"^unifiedanalyzer_(daily|weekly|monthly)_(\d{8}T\d{6}Z)\.dump$"
)


class BackupError(RuntimeError):
    """Raised when a DB backup cannot be created safely."""


@dataclass(frozen=True)
class BackupConfig:
    database_url: str
    root: Path
    pg_dump_bin: str = "pg_dump"
    retention: dict[BackupKind, int] | None = None

    @classmethod
    def from_env(
        cls,
        *,
        database_url: str | None = None,
        root: str | Path | None = None,
        pg_dump_bin: str | None = None,
        retention_daily: int | None = None,
        retention_weekly: int | None = None,
        retention_monthly: int | None = None,
        allow_missing_database_url: bool = False,
    ) -> "BackupConfig":
        db_url = database_url or os.getenv("ANALYZER_DATABASE_URL")
        if not db_url:
            if not allow_missing_database_url:
                raise BackupError("Missing ANALYZER_DATABASE_URL")
            db_url = "postgres://unused@127.0.0.1/postgres"

        configured_root = root or os.getenv("ANALYZER_DB_BACKUP_DIR")
        backup_root = Path(configured_root) if configured_root else _default_backup_root()

        retention: dict[BackupKind, int] = {
            "daily": _env_int(
                "ANALYZER_DB_BACKUP_RETENTION_DAILY",
                retention_daily,
                DEFAULT_RETENTION["daily"],
            ),
            "weekly": _env_int(
                "ANALYZER_DB_BACKUP_RETENTION_WEEKLY",
                retention_weekly,
                DEFAULT_RETENTION["weekly"],
            ),
            "monthly": _env_int(
                "ANALYZER_DB_BACKUP_RETENTION_MONTHLY",
                retention_monthly,
                DEFAULT_RETENTION["monthly"],
            ),
        }

        return cls(
            database_url=db_url,
            root=backup_root,
            pg_dump_bin=pg_dump_bin or os.getenv("PG_DUMP_BIN", "pg_dump"),
            retention=retention,
        )

    def retention_for(self, kind: BackupKind) -> int:
        values = DEFAULT_RETENTION.copy()
        if self.retention:
            values.update(self.retention)
        return values[kind]


@dataclass(frozen=True)
class BackupFile:
    kind: BackupKind
    path: Path
    created_at: datetime
    size_bytes: int

    @property
    def period_key(self) -> str:
        return period_key(self.kind, self.created_at)


@dataclass(frozen=True)
class BackupRunResult:
    created: tuple[Path, ...]
    deleted: tuple[Path, ...]
    would_create: tuple[Path, ...]
    would_delete: tuple[Path, ...]

    def to_dict(self) -> dict:
        return {
            "created": [str(p) for p in self.created],
            "deleted": [str(p) for p in self.deleted],
            "would_create": [str(p) for p in self.would_create],
            "would_delete": [str(p) for p in self.would_delete],
        }


def _default_backup_root() -> Path:
    if os.name == "nt":
        return DEFAULT_BACKUP_ROOT_WINDOWS
    return DEFAULT_BACKUP_ROOT_POSIX


def _env_int(key: str, override: int | None, default: int) -> int:
    raw = override if override is not None else os.getenv(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BackupError(f"{key} must be an integer") from exc
    if value < 0:
        raise BackupError(f"{key} must be >= 0")
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def period_key(kind: BackupKind, when: datetime) -> str:
    when = _as_utc(when)
    if kind == "daily":
        return when.strftime("%Y-%m-%d")
    if kind == "weekly":
        iso = when.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if kind == "monthly":
        return when.strftime("%Y-%m")
    raise ValueError(f"Unsupported backup kind: {kind}")


def list_backups(root: str | Path) -> list[BackupFile]:
    backup_root = Path(root)
    if not backup_root.exists():
        return []

    backups: list[BackupFile] = []
    for path in backup_root.rglob("unifiedanalyzer_*.dump"):
        if not path.is_file():
            continue
        parsed = _parse_backup_filename(path)
        if parsed is None:
            continue
        kind, created_at = parsed
        backups.append(
            BackupFile(
                kind=kind,
                path=path,
                created_at=created_at,
                size_bytes=path.stat().st_size,
            )
        )
    return sorted(backups, key=lambda item: item.created_at, reverse=True)


def due_backup_kinds(
    backups: list[BackupFile],
    now: datetime | None = None,
) -> tuple[BackupKind, ...]:
    when = _as_utc(now or utc_now())
    due: list[BackupKind] = []
    for kind in BACKUP_KINDS:
        current_period = period_key(kind, when)
        if not any(b.kind == kind and b.period_key == current_period for b in backups):
            due.append(kind)
    return tuple(due)


def target_path(root: str | Path, kind: BackupKind, now: datetime | None = None) -> Path:
    when = _as_utc(now or utc_now())
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    return Path(root) / kind / f"unifiedanalyzer_{kind}_{stamp}.dump"


def run_due_backups(
    config: BackupConfig,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    apply_retention: bool = True,
) -> BackupRunResult:
    when = _as_utc(now or utc_now())
    due = due_backup_kinds(list_backups(config.root), when)
    return run_backup_kinds(
        config,
        due,
        now=when,
        dry_run=dry_run,
        apply_retention=apply_retention,
    )


def run_backup_kinds(
    config: BackupConfig,
    kinds: tuple[BackupKind, ...] | list[BackupKind],
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    apply_retention: bool = True,
) -> BackupRunResult:
    when = _as_utc(now or utc_now())
    normalized = _normalize_kinds(kinds)
    targets = tuple(target_path(config.root, kind, when) for kind in normalized)

    if dry_run:
        planned = [
            BackupFile(kind=kind, path=path, created_at=when, size_bytes=0)
            for kind, path in zip(normalized, targets, strict=True)
        ]
        would_delete = (
            _stale_backup_paths(config, list_backups(config.root) + planned)
            if apply_retention
            else tuple()
        )
        return BackupRunResult(
            created=tuple(),
            deleted=tuple(),
            would_create=targets,
            would_delete=would_delete,
        )

    created: list[Path] = []
    if targets:
        primary = targets[0]
        _run_pg_dump(config, primary)
        created.append(primary)
        for target in targets[1:]:
            _copy_backup(primary, target)
            created.append(target)

    deleted = tuple(prune_backups(config, dry_run=False)) if apply_retention else tuple()
    return BackupRunResult(
        created=tuple(created),
        deleted=deleted,
        would_create=tuple(),
        would_delete=tuple(),
    )


def prune_backups(config: BackupConfig, *, dry_run: bool = False) -> tuple[Path, ...]:
    stale = _stale_backup_paths(config, list_backups(config.root))

    if dry_run:
        return stale

    for path in stale:
        path.unlink(missing_ok=True)
        logger.info("Deleted stale analyzer DB backup: %s", path)
    return stale


def _stale_backup_paths(config: BackupConfig, backups: list[BackupFile]) -> tuple[Path, ...]:
    ordered = sorted(backups, key=lambda item: item.created_at, reverse=True)
    stale: list[Path] = []
    for kind in BACKUP_KINDS:
        keep = config.retention_for(kind)
        kind_backups = [b for b in ordered if b.kind == kind]
        for backup in kind_backups[keep:]:
            stale.append(backup.path)
    return tuple(stale)


def backups_to_json(backups: list[BackupFile]) -> str:
    return json.dumps(
        [
            {
                "kind": backup.kind,
                "path": str(backup.path),
                "created_at": backup.created_at.isoformat(),
                "period": backup.period_key,
                "size_bytes": backup.size_bytes,
            }
            for backup in backups
        ],
        indent=2,
    )


def _normalize_kinds(kinds: tuple[BackupKind, ...] | list[BackupKind]) -> tuple[BackupKind, ...]:
    normalized: list[BackupKind] = []
    for kind in kinds:
        if kind not in BACKUP_KINDS:
            raise BackupError(f"Unsupported backup kind: {kind}")
        if kind not in normalized:
            normalized.append(kind)
    return tuple(normalized)


def _run_pg_dump(config: BackupConfig, target: Path) -> None:
    pg_dump = shutil.which(config.pg_dump_bin) or (
        config.pg_dump_bin if Path(config.pg_dump_bin).exists() else None
    )
    if not pg_dump:
        raise BackupError(
            f"pg_dump binary not found: {config.pg_dump_bin}. "
            "Install the PostgreSQL client or set PG_DUMP_BIN."
        )

    conn = _parse_database_url(config.database_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    if tmp.exists():
        tmp.unlink()

    cmd = [
        pg_dump,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(tmp),
        "--host",
        conn["host"],
        "--port",
        str(conn["port"]),
        "--username",
        conn["user"],
        "--dbname",
        conn["database"],
    ]

    env = os.environ.copy()
    if conn["password"]:
        env["PGPASSWORD"] = conn["password"]
    if conn.get("sslmode"):
        env["PGSSLMODE"] = conn["sslmode"]

    logger.info("Creating analyzer DB backup: %s", target)
    proc = subprocess.run(
        cmd,
        env=env,
        text=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "").strip()
        raise BackupError(f"pg_dump failed with exit code {proc.returncode}: {detail}")

    tmp.replace(target)
    logger.info("Analyzer DB backup created: %s", target)


def _copy_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(source, tmp)
    tmp.replace(target)
    logger.info("Analyzer DB backup copied: %s", target)


def _parse_database_url(database_url: str) -> dict[str, str | int]:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgres"):
        raise BackupError("ANALYZER_DATABASE_URL must be a postgres URL")
    query = parse_qs(parsed.query)
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    return {
        "host": host,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or "collector"),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/") or "postgres"),
        "sslmode": query.get("sslmode", [""])[0],
    }


def _parse_backup_filename(path: Path) -> tuple[BackupKind, datetime] | None:
    match = FILENAME_RE.match(path.name)
    if not match:
        return None
    kind = match.group(1)
    if kind not in BACKUP_KINDS:
        return None
    created_at = datetime.strptime(match.group(2), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )
    return kind, created_at


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
