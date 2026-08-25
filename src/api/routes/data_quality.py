"""Data quality proof surfaces."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from src.db.connection import CollectorUnavailableError, get_analyzer_pool, get_collector_pool
from src.pipeline.data_quality_ledger import build_data_quality_ledger

router = APIRouter(tags=["data-quality"])


def _cache_path() -> Path:
    return Path(os.getenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_PATH", "tmp_data_quality_ledger_latest.json"))


def _cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("ANALYZER_DATA_QUALITY_LEDGER_CACHE_TTL_SECONDS", "900") or "900"))
    except (TypeError, ValueError):
        return 900


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_age_seconds(payload: dict[str, Any]) -> int | None:
    generated = _parse_ts(payload.get("generated_at"))
    if generated is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - generated).total_seconds()))


def _write_ledger_cache(payload: dict[str, Any]) -> None:
    if payload.get("ok") is not True:
        return
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
    tmp.replace(path)


def cached_data_quality_ledger() -> dict[str, Any] | None:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    age = _cache_age_seconds(payload)
    ttl = _cache_ttl_seconds()
    if age is None or (ttl and age > ttl):
        return None
    cached = dict(payload)
    cached["cache"] = {
        "used": True,
        "path": str(path),
        "age_seconds": age,
        "ttl_seconds": ttl,
    }
    return cached


@router.get("/data-quality/ledger")
async def data_quality_ledger():
    analyzer_pool = get_analyzer_pool()
    collector_pool = None
    try:
        collector_pool = get_collector_pool()
    except CollectorUnavailableError:
        collector_pool = None

    async with analyzer_pool.acquire() as analyzer_conn:
        if collector_pool is None:
            ledger = await build_data_quality_ledger(analyzer_conn, None)
            _write_ledger_cache(ledger)
            return ledger
        async with collector_pool.acquire() as collector_conn:
            ledger = await build_data_quality_ledger(analyzer_conn, collector_conn)
            _write_ledger_cache(ledger)
            return ledger
