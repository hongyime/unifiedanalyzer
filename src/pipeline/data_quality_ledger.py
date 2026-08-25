"""Read-only end-to-end quality ledger for Collector -> Analyzer -> Supabase.

The ledger deliberately reports only aggregate counts and timestamps. It is a
production proof surface, not an evidence export endpoint.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


DEFAULT_LEDGER_SOURCES = (
    "facebook",
    "instagram",
    "threads",
    "tiktok",
    "x",
    "telegram",
    "whatsapp",
    "website",
    "search",
    "github",
    "exposure",
)


RAW_SOURCE_QUERIES: dict[str, tuple[tuple[str, str, tuple[Any, ...]], ...]] = {
    "facebook": (
        ("facebook_posts", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM facebook_posts WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("facebook",)),
        ("browser_ingest_events", "SELECT COALESCE(SUM(stored_count),0)::int AS count, MAX(created_at) AS latest FROM browser_ingest_events WHERE platform = $1 AND endpoint <> 'browser_heartbeat' AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("facebook",)),
    ),
    "instagram": (
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("instagram",)),
        ("browser_ingest_events", "SELECT COALESCE(SUM(stored_count),0)::int AS count, MAX(created_at) AS latest FROM browser_ingest_events WHERE platform = $1 AND endpoint <> 'browser_heartbeat' AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("instagram",)),
    ),
    "threads": (
        ("threads_posts", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM threads_posts WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("threads",)),
        ("browser_ingest_events", "SELECT COALESCE(SUM(stored_count),0)::int AS count, MAX(created_at) AS latest FROM browser_ingest_events WHERE platform = $1 AND endpoint <> 'browser_heartbeat' AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("threads",)),
    ),
    "tiktok": (
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("tiktok",)),
        ("browser_ingest_events", "SELECT COALESCE(SUM(stored_count),0)::int AS count, MAX(created_at) AS latest FROM browser_ingest_events WHERE platform = $1 AND endpoint <> 'browser_heartbeat' AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("tiktok",)),
    ),
    "x": (
        ("x_posts", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM x_posts WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("x",)),
        ("browser_ingest_events", "SELECT COALESCE(SUM(stored_count),0)::int AS count, MAX(created_at) AS latest FROM browser_ingest_events WHERE platform = $1 AND endpoint <> 'browser_heartbeat' AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("x",)),
    ),
    "telegram": (
        ("telegram_messages", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM telegram_messages WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("telegram",)),
    ),
    "whatsapp": (
        ("whatsapp_messages", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM whatsapp_messages WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("whatsapp",)),
    ),
    "website": (
        ("website_pages", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM website_pages WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
    ),
    "search": (
        ("search_results", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM search_results WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
    ),
    "github": (
        ("media_items", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM media_items WHERE source = $1 AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')", ("github",)),
    ),
    "exposure": (
        ("exposure_findings", "SELECT COUNT(*)::int AS count, MAX(collected_at) AS latest FROM exposure_findings WHERE collected_at >= NOW() - ($1::int * INTERVAL '1 hour')", ()),
    ),
}


def _configured_sources() -> list[str]:
    configured = os.getenv("ANALYZER_DATA_QUALITY_SOURCES")
    if not configured:
        return list(DEFAULT_LEDGER_SOURCES)
    return [
        item.strip().lower()
        for item in configured.split(",")
        if item.strip()
    ]


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _max_dt(values: list[Any]) -> Any:
    dates = [value for value in values if value is not None]
    return max(dates) if dates else None


def _age_seconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = int((datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds())
    try:
        grace = max(0, int(os.getenv("ANALYZER_DATA_QUALITY_FUTURE_SKEW_GRACE_SECONDS", "300") or "300"))
    except (TypeError, ValueError):
        grace = 300
    if delta < -grace:
        return delta
    return max(0, delta)


async def _available_tables(conn, tables: set[str]) -> set[str]:
    if not tables:
        return set()
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY($1::text[])
        """,
        sorted(tables),
    )
    return {str(row["table_name"]) for row in rows}


async def _fetch_count_latest(conn, sql: str, *args: Any) -> dict[str, Any]:
    row = await conn.fetchrow(sql, *args)
    if not row:
        return {"count": 0, "latest": None}
    return {"count": int(row["count"] or 0), "latest": row["latest"]}


async def _raw_source_stage(conn, source: str, available_tables: set[str]) -> dict[str, Any]:
    lookback_hours = max(1, int(os.getenv("ANALYZER_DATA_QUALITY_LOOKBACK_HOURS", "24") or "24"))
    signals: list[dict[str, Any]] = []
    for table, sql, args in RAW_SOURCE_QUERIES.get(source, ()):
        if table not in available_tables:
            signals.append({"table": table, "available": False, "count": 0, "latest_at": None})
            continue
        row = await _fetch_count_latest(conn, sql, *args, lookback_hours)
        signals.append({
            "table": table,
            "available": True,
            "count": row["count"],
            "latest_at": _iso(row["latest"]),
            "latest_age_seconds": _age_seconds(row["latest"]),
        })
    latest = _max_dt([signal.get("latest_at") for signal in signals])
    return {
        "count": sum(int(signal.get("count") or 0) for signal in signals),
        "latest_at": latest,
        "latest_age_seconds": _age_seconds(latest),
        "lookback_hours": lookback_hours,
        "signals": signals,
    }


async def _analyzer_source_stage(conn, source: str, available_tables: set[str]) -> dict[str, Any]:
    lookback_hours = max(1, int(os.getenv("ANALYZER_DATA_QUALITY_LOOKBACK_HOURS", "24") or "24"))

    async def maybe(table: str, sql: str, *args: Any) -> dict[str, Any]:
        if table not in available_tables:
            return {"count": 0, "latest": None}
        return await _fetch_count_latest(conn, sql, *args)

    timeline = await maybe(
        "timeline_events",
        "SELECT COUNT(*)::int AS count, MAX(occurred_at) AS latest FROM timeline_events WHERE source = $1 AND occurred_at >= NOW() - ($2::int * INTERVAL '1 hour')",
        source,
        lookback_hours,
    )
    text = await maybe(
        "timeline_text_features",
        "SELECT COUNT(*)::int AS count, MAX(processed_at) AS latest FROM timeline_text_features WHERE source = $1 AND processed_at >= NOW() - ($2::int * INTERVAL '1 hour')",
        source,
        lookback_hours,
    )
    media = await maybe(
        "media_analysis",
        "SELECT COUNT(*)::int AS count, MAX(processed_at) AS latest FROM media_analysis WHERE source = $1 AND processed_at >= NOW() - ($2::int * INTERVAL '1 hour')",
        source,
        lookback_hours,
    )
    indicators = await maybe(
        "normalized_indicators",
        "SELECT COUNT(*)::int AS count, MAX(updated_at) AS latest FROM normalized_indicators WHERE source_families @> ARRAY[$1]::text[] AND updated_at >= NOW() - ($2::int * INTERVAL '1 hour')",
        source,
        lookback_hours,
    )
    exported = await maybe(
        "normalized_indicators",
        """
        SELECT COUNT(*)::int AS count, MAX(exported_at) AS latest
        FROM normalized_indicators
        WHERE source_families @> ARRAY[$1]::text[]
          AND export_status = 'exported'
          AND exported_at >= NOW() - ($2::int * INTERVAL '1 hour')
        """,
        source,
        lookback_hours,
    )
    latest = _max_dt([timeline["latest"], text["latest"], media["latest"], indicators["latest"], exported["latest"]])
    return {
        "timeline_events": {"count": timeline["count"], "latest_at": _iso(timeline["latest"])},
        "text_features": {"count": text["count"], "latest_at": _iso(text["latest"])},
        "media_analysis": {"count": media["count"], "latest_at": _iso(media["latest"])},
        "normalized_indicators": {"count": indicators["count"], "latest_at": _iso(indicators["latest"])},
        "supabase_exported_indicators": {"count": exported["count"], "latest_at": _iso(exported["latest"])},
        "total_analyzer_signals": sum(
            int(item["count"] or 0)
            for item in (timeline, text, media, indicators)
        ),
        "latest_at": _iso(latest),
        "latest_age_seconds": _age_seconds(latest),
        "lookback_hours": lookback_hours,
    }


def _empty_raw_stage(lookback_hours: int) -> dict[str, Any]:
    return {
        "count": 0,
        "latest_at": None,
        "latest_age_seconds": None,
        "lookback_hours": lookback_hours,
        "signals": [],
    }


def _empty_analyzer_stage(lookback_hours: int) -> dict[str, Any]:
    return {
        "timeline_events": {"count": 0, "latest_at": None},
        "text_features": {"count": 0, "latest_at": None},
        "media_analysis": {"count": 0, "latest_at": None},
        "normalized_indicators": {"count": 0, "latest_at": None},
        "supabase_exported_indicators": {"count": 0, "latest_at": None},
        "total_analyzer_signals": 0,
        "latest_at": None,
        "latest_age_seconds": None,
        "lookback_hours": lookback_hours,
    }


def _merge_signal(stage: dict[str, Any], table: str, count: int, latest: Any) -> None:
    stage.setdefault("signals", []).append({
        "table": table,
        "available": True,
        "count": count,
        "latest_at": _iso(latest),
        "latest_age_seconds": _age_seconds(latest),
    })
    stage["count"] = int(stage.get("count") or 0) + int(count or 0)
    latest_values = [
        datetime.fromisoformat(str(stage["latest_at"]).replace("Z", "+00:00"))
        if stage.get("latest_at") else None,
        latest,
    ]
    latest_dt = _max_dt([value for value in latest_values if value is not None])
    stage["latest_at"] = _iso(latest_dt)
    stage["latest_age_seconds"] = _age_seconds(latest_dt)


async def _raw_stages(
    conn,
    sources: list[str],
    available_tables: set[str],
    lookback_hours: int,
) -> dict[str, dict[str, Any]]:
    stages = {source: _empty_raw_stage(lookback_hours) for source in sources}
    requested = set(sources)
    for source in sources:
        for table, _sql, _args in RAW_SOURCE_QUERIES.get(source, ()):
            if table not in available_tables:
                stages[source]["signals"].append({"table": table, "available": False, "count": 0, "latest_at": None})

    if "media_items" in available_tables:
        rows = await conn.fetch(
            """
            SELECT source, COUNT(*)::int AS count, MAX(collected_at) AS latest
            FROM media_items
            WHERE source = ANY($1::text[])
              AND collected_at >= NOW() - ($2::int * INTERVAL '1 hour')
            GROUP BY source
            """,
            sorted(requested),
            lookback_hours,
        )
        for row in rows:
            source = str(row["source"])
            if source in stages:
                _merge_signal(stages[source], "media_items", int(row["count"] or 0), row["latest"])

    if "browser_ingest_events" in available_tables:
        rows = await conn.fetch(
            """
            SELECT platform AS source,
                   COALESCE(SUM(stored_count), 0)::int AS count,
                   MAX(created_at) AS latest
            FROM browser_ingest_events
            WHERE platform = ANY($1::text[])
              AND endpoint <> 'browser_heartbeat'
              AND stored_count > 0
              AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')
            GROUP BY platform
            """,
            sorted(requested),
            lookback_hours,
        )
        for row in rows:
            source = str(row["source"])
            if source in stages:
                _merge_signal(stages[source], "browser_ingest_events", int(row["count"] or 0), row["latest"])

    post_tables = {
        "facebook": ("facebook_posts", "collected_at"),
        "threads": ("threads_posts", "collected_at"),
        "x": ("x_posts", "collected_at"),
        "telegram": ("telegram_messages", "collected_at"),
        "whatsapp": ("whatsapp_messages", "collected_at"),
        "website": ("website_pages", "collected_at"),
        "search": ("search_results", "collected_at"),
        "exposure": ("exposure_findings", "collected_at"),
    }
    for source, (table, time_column) in post_tables.items():
        if source not in stages or table not in available_tables:
            continue
        row = await _fetch_count_latest(
            conn,
            f"SELECT COUNT(*)::int AS count, MAX({time_column}) AS latest FROM {table} WHERE {time_column} >= NOW() - ($1::int * INTERVAL '1 hour')",
            lookback_hours,
        )
        _merge_signal(stages[source], table, row["count"], row["latest"])

    return stages


async def _analyzer_stages(
    conn,
    sources: list[str],
    available_tables: set[str],
    lookback_hours: int,
) -> dict[str, dict[str, Any]]:
    stages = {source: _empty_analyzer_stage(lookback_hours) for source in sources}

    async def merge_table(stage_key: str, table: str, time_column: str) -> None:
        if table not in available_tables:
            return
        rows = await conn.fetch(
            f"""
            SELECT source, COUNT(*)::int AS count, MAX({time_column}) AS latest
            FROM {table}
            WHERE source = ANY($1::text[])
              AND {time_column} >= NOW() - ($2::int * INTERVAL '1 hour')
            GROUP BY source
            """,
            sources,
            lookback_hours,
        )
        for row in rows:
            source = str(row["source"])
            if source not in stages:
                continue
            stages[source][stage_key] = {"count": int(row["count"] or 0), "latest_at": _iso(row["latest"])}

    await merge_table("timeline_events", "timeline_events", "occurred_at")
    await merge_table("text_features", "timeline_text_features", "processed_at")
    await merge_table("media_analysis", "media_analysis", "processed_at")

    if "normalized_indicators" in available_tables:
        rows = await conn.fetch(
            """
            SELECT family.source, COUNT(*)::int AS count, MAX(updated_at) AS latest
            FROM normalized_indicators
            CROSS JOIN LATERAL unnest(source_families) AS family(source)
            WHERE family.source = ANY($1::text[])
              AND updated_at >= NOW() - ($2::int * INTERVAL '1 hour')
            GROUP BY family.source
            """,
            sources,
            lookback_hours,
        )
        for row in rows:
            source = str(row["source"])
            if source in stages:
                stages[source]["normalized_indicators"] = {
                    "count": int(row["count"] or 0),
                    "latest_at": _iso(row["latest"]),
                }
        rows = await conn.fetch(
            """
            SELECT family.source, COUNT(*)::int AS count, MAX(exported_at) AS latest
            FROM normalized_indicators
            CROSS JOIN LATERAL unnest(source_families) AS family(source)
            WHERE family.source = ANY($1::text[])
              AND export_status = 'exported'
              AND exported_at >= NOW() - ($2::int * INTERVAL '1 hour')
            GROUP BY family.source
            """,
            sources,
            lookback_hours,
        )
        for row in rows:
            source = str(row["source"])
            if source in stages:
                stages[source]["supabase_exported_indicators"] = {
                    "count": int(row["count"] or 0),
                    "latest_at": _iso(row["latest"]),
                }

    for stage in stages.values():
        latest = _max_dt([
            datetime.fromisoformat(value["latest_at"].replace("Z", "+00:00"))
            for key in (
                "timeline_events",
                "text_features",
                "media_analysis",
                "normalized_indicators",
                "supabase_exported_indicators",
            )
            for value in [stage[key]]
            if value.get("latest_at")
        ])
        stage["total_analyzer_signals"] = sum(
            int(stage[key]["count"] or 0)
            for key in ("timeline_events", "text_features", "media_analysis", "normalized_indicators")
        )
        stage["latest_at"] = _iso(latest)
        stage["latest_age_seconds"] = _age_seconds(latest)

    return stages


def _source_state(raw: dict[str, Any], analyzer: dict[str, Any]) -> tuple[str, str]:
    raw_count = int(raw.get("count") or 0)
    analyzer_count = int(analyzer.get("total_analyzer_signals") or 0)
    exported_count = int((analyzer.get("supabase_exported_indicators") or {}).get("count") or 0)
    indicator_count = int((analyzer.get("normalized_indicators") or {}).get("count") or 0)
    if _has_future_timestamp(raw) or _has_future_timestamp(analyzer):
        return "clock_skew", "source has future-dated evidence; check collector/analyzer clocks or bad test data"
    if raw_count <= 0 and analyzer_count <= 0:
        return "quiet", "no collector or analyzer evidence for this source"
    if raw_count <= 0 and analyzer_count > 0:
        return "analyzer_only", "analyzer has historical evidence but no current raw collector signal"
    if analyzer_count <= 0:
        return "gap", "collector has raw evidence but analyzer has no derived evidence"
    if indicator_count > 0 and exported_count <= 0:
        return "export_gap", "analyzer indicators exist but none are exported to Supabase"
    return "ok", "collector evidence has an analyzer evidence path"


def _has_future_timestamp(value: Any) -> bool:
    if isinstance(value, dict):
        age = value.get("latest_age_seconds")
        try:
            if age is not None and int(age) < 0:
                return True
        except (TypeError, ValueError):
            pass
        return any(_has_future_timestamp(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_future_timestamp(item) for item in value)
    return False


async def build_data_quality_ledger(analyzer_conn, collector_conn=None) -> dict[str, Any]:
    rows = []
    sources = _configured_sources()
    lookback_hours = max(1, int(os.getenv("ANALYZER_DATA_QUALITY_LOOKBACK_HOURS", "24") or "24"))
    analyzer_tables = await _available_tables(
        analyzer_conn,
        {"timeline_events", "timeline_text_features", "media_analysis", "normalized_indicators"},
    )
    raw_tables = {
        table
        for source_queries in RAW_SOURCE_QUERIES.values()
        for table, _sql, _args in source_queries
    }
    collector_tables = await _available_tables(collector_conn, raw_tables) if collector_conn is not None else set()
    raw_by_source = (
        await _raw_stages(collector_conn, sources, collector_tables, lookback_hours)
        if collector_conn is not None
        else {
            source: {"count": None, "latest_at": None, "latest_age_seconds": None, "signals": []}
            for source in sources
        }
    )
    analyzer_by_source = await _analyzer_stages(analyzer_conn, sources, analyzer_tables, lookback_hours)
    for source in sources:
        raw = (
            raw_by_source[source]
            if collector_conn is not None
            else {"count": None, "latest_at": None, "latest_age_seconds": None, "signals": []}
        )
        analyzer = analyzer_by_source[source]
        state, detail = _source_state(raw, analyzer)
        rows.append({
            "source": source,
            "state": state,
            "ok": state in {"ok", "quiet", "analyzer_only"},
            "detail": detail,
            "raw_collector": raw,
            "analyzer": analyzer,
        })

    gaps = [row for row in rows if row["state"] in {"gap", "export_gap", "clock_skew"}]
    return {
        "status": "ok" if not gaps else "degraded",
        "ok": not gaps,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": rows,
        "summary": {
            "total_sources": len(rows),
            "ok_sources": sum(1 for row in rows if row["ok"]),
            "gap_sources": len(gaps),
            "states": {
                state: sum(1 for row in rows if row["state"] == state)
                for state in sorted({row["state"] for row in rows})
            },
        },
    }
