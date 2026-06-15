"""Prometheus-style text metrics for the analyzer dashboard.

Exposes a single GET /api/metrics endpoint in the Prometheus text exposition
format (https://prometheus.io/docs/instrumenting/exposition_formats/). No
client library dependency — we hand-render the text, which keeps requirements
unchanged and the output trivially greppable.

Mirrors the unifiedcollector dashboard's /metrics shape, adapted to analyzer
domain entities (entities/alerts/runs/timeline/media_analysis) instead of
collector media counts.
"""
import logging

from fastapi import APIRouter, Response

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


def _line(name: str, value, labels: dict | None = None) -> str:
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{label_str}}} {value}"
    return f"{name} {value}"


@router.get("/metrics", response_class=Response)
async def metrics() -> Response:
    """Render current analyzer state as Prometheus text.

    Never raises: any DB error degrades the affected gauge to 0 and flips the
    corresponding *_up gauge, so a scrape during an outage still returns 200
    with a usable signal rather than failing the whole endpoint.
    """
    lines: list[str] = []
    analyzer_up = 1
    collector_up = 1

    # ── Analyzer DB ──
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            entity_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
            alerts_total = await conn.fetchval("SELECT COUNT(*) FROM alerts")
            alerts_unread = await conn.fetchval(
                "SELECT COUNT(*) FROM alerts WHERE is_read = FALSE"
            )
            timeline_count = await conn.fetchval("SELECT COUNT(*) FROM timeline_events")
            signal_count = await conn.fetchval("SELECT COUNT(*) FROM identity_signals")

            run_rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM analysis_runs GROUP BY status"
            )
            sev_rows = await conn.fetch(
                "SELECT severity, COUNT(*) AS n FROM alerts WHERE is_read = FALSE GROUP BY severity"
            )
            media_rows = await conn.fetch(
                "SELECT analysis_type, COUNT(*) AS n FROM media_analysis GROUP BY analysis_type"
            )
            media_total = await conn.fetchval(
                "SELECT COUNT(DISTINCT media_item_id) FROM media_analysis"
            )
            last_run = await conn.fetchval(
                "SELECT EXTRACT(EPOCH FROM finished_at) FROM analysis_runs "
                "WHERE status = 'completed' ORDER BY finished_at DESC LIMIT 1"
            )

        lines += [
            "# HELP analyzer_entities_total Number of resolved entities.",
            "# TYPE analyzer_entities_total gauge",
            _line("analyzer_entities_total", entity_count),
            "# HELP analyzer_alerts_total Total alerts ever raised.",
            "# TYPE analyzer_alerts_total gauge",
            _line("analyzer_alerts_total", alerts_total),
            "# HELP analyzer_alerts_unread Unread alerts.",
            "# TYPE analyzer_alerts_unread gauge",
            _line("analyzer_alerts_unread", alerts_unread),
            "# HELP analyzer_timeline_events_total Timeline events normalized across sources.",
            "# TYPE analyzer_timeline_events_total gauge",
            _line("analyzer_timeline_events_total", timeline_count),
            "# HELP analyzer_identity_signals_total Identity signals recorded.",
            "# TYPE analyzer_identity_signals_total gauge",
            _line("analyzer_identity_signals_total", signal_count),
            "# HELP analyzer_media_analysis_total Distinct media items analyzed.",
            "# TYPE analyzer_media_analysis_total gauge",
            _line("analyzer_media_analysis_total", media_total or 0),
        ]
        lines.append("# HELP analyzer_runs Analysis runs by status.")
        lines.append("# TYPE analyzer_runs gauge")
        for r in run_rows:
            lines.append(_line("analyzer_runs", r["n"], {"status": r["status"]}))
        lines.append("# HELP analyzer_alerts_unread_by_severity Unread alerts by severity.")
        lines.append("# TYPE analyzer_alerts_unread_by_severity gauge")
        for r in sev_rows:
            lines.append(_line("analyzer_alerts_unread_by_severity", r["n"], {"severity": r["severity"]}))
        lines.append("# HELP analyzer_media_analysis_rows media_analysis rows by analysis_type.")
        lines.append("# TYPE analyzer_media_analysis_rows gauge")
        for r in media_rows:
            lines.append(_line("analyzer_media_analysis_rows", r["n"], {"analysis_type": r["analysis_type"]}))
        if last_run is not None:
            lines.append("# HELP analyzer_last_completed_run_timestamp_seconds Unix time of last completed run.")
            lines.append("# TYPE analyzer_last_completed_run_timestamp_seconds gauge")
            lines.append(_line("analyzer_last_completed_run_timestamp_seconds", int(last_run)))
    except Exception as e:  # noqa: BLE001 — scrape must stay 200
        analyzer_up = 0
        logger.warning("metrics: analyzer DB error: %s", e)

    # ── Collector DB (read-only health probe) ──
    try:
        pool = get_collector_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:  # noqa: BLE001
        collector_up = 0
        logger.warning("metrics: collector DB error: %s", e)

    lines += [
        "# HELP analyzer_db_up Analyzer database reachable (1/0).",
        "# TYPE analyzer_db_up gauge",
        _line("analyzer_db_up", analyzer_up),
        "# HELP collector_db_up Collector database reachable (1/0).",
        "# TYPE collector_db_up gauge",
        _line("collector_db_up", collector_up),
    ]

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
