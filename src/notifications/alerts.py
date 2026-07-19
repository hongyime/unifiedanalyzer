"""Notification dispatchers for all 7 alert types."""

import html
import logging
from datetime import datetime, timezone

from src.notifications import telegram

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {
    "critical": "⚠️",  # warning sign
    "warning": "\U0001f7e1",     # yellow circle
    "info": "ℹ️",       # info
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _num(value) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def _esc(value) -> str:
    return html.escape(str(value))


async def notify_startup():
    url = telegram.get_dashboard_url()
    await telegram.send(
        f"✅ <b>UnifiedAnalyzer started</b>\n"
        f"Dashboard: {url}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


async def notify_shutdown():
    await telegram.send(
        f"\U0001f534 <b>UnifiedAnalyzer shutting down</b>\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )


async def notify_new_alerts(alerts: list[dict]):
    if not alerts:
        return
    url = telegram.get_dashboard_url()
    lines = [f"\U0001f514 <b>{len(alerts)} new alert(s)</b>\n"]
    for a in alerts[:10]:
        icon = _SEVERITY_ICON.get(a.get("severity", "info"), "•")
        lines.append(f"{icon} {a['title']}")
    if len(alerts) > 10:
        lines.append(f"  ...and {len(alerts) - 10} more")
    lines.append(f"\n{url}/alerts")
    await telegram.send("\n".join(lines))


async def notify_run_summary(run_type: str, stats: dict):
    events = stats.get("events", 0)
    alerts = stats.get("alerts", 0)
    entities = stats.get("entities", 0)
    signals = stats.get("signals", 0)

    if events == 0 and alerts == 0 and entities == 0:
        return

    icon = "\U0001f504" if run_type == "incremental" else "\U0001f9f9"
    parts = []
    if entities:
        parts.append(f"{entities} entities")
    if events:
        parts.append(f"{events} events")
    if alerts:
        parts.append(f"{alerts} alerts")
    if signals:
        parts.append(f"{signals} signals")

    await telegram.send(
        f"{icon} <b>{run_type.title()} run complete</b>\n"
        f"{', '.join(parts)}"
    )


async def notify_collector_health(issues: list[dict]):
    if not issues:
        return
    url = telegram.get_collector_dashboard_url()
    lines = ["\U0001f6a8 <b>Collector health warning</b>\n"]
    for issue in issues:
        lines.append(f"• <b>{issue['source']}</b>: {issue['message']}")
    lines.append(f"\n{url}/collectors")
    await telegram.send("\n".join(lines))


async def notify_daily_digest(digest: dict):
    url = telegram.get_dashboard_url()
    lines = ["\U0001f4ca <b>Daily Digest</b>\n"]
    lines.append(f"Entities: {digest['entity_count']} ({digest['primary']} primary, {digest['secondary']} secondary)")
    lines.append(f"Faces linked to entities: {digest.get('faces_linked', 0)}")
    lines.append(f"Alerts (24h): {digest['alerts_24h']} ({digest['unread']} unread)")
    lines.append(f"Events (24h): {digest['events_24h']}")
    lines.append(f"Runs (24h): {digest['runs_24h']} ({digest['failed_runs']} failed)")

    if digest.get("most_active"):
        lines.append(f"Most active: {digest['most_active']}")
    if digest.get("failing_phases"):
        lines.append(f"⚠️ Failing phases: {', '.join(digest['failing_phases'])}")
    if digest.get("collectors_down"):
        lines.append(f"⚠️ Collectors down: {', '.join(digest['collectors_down'])}")

    lines.append(f"\n{url}")
    await telegram.send("\n".join(lines))


async def notify_merge_candidate(entity_a_name: str, entity_b_name: str,
                                  confidence: float, shared_signal: str):
    url = telegram.get_dashboard_url()
    await telegram.send(
        f"\U0001f517 <b>Merge candidate detected</b>\n"
        f"<b>{entity_a_name}</b> ↔ <b>{entity_b_name}</b>\n"
        f"Confidence: {confidence:.0%} via {shared_signal}\n"
        f"{url}/entities"
    )


async def notify_error(run_type: str, error: str):
    await telegram.send(
        f"❌ <b>{run_type.title()} run failed</b>\n"
        f"<code>{error[:500]}</code>"
    )


async def notify_status(s: dict):
    """Periodic system-status heartbeat to the group chat.

    Unlike the event-driven notifications above (run complete, new alert, …),
    this is a recurring "here's where things stand" snapshot posted on an
    interval by the scheduler, so the group always has a current picture even
    when nothing eventful happened. Built by scheduler._build_status.
    """
    url = telegram.get_dashboard_url()
    actionable_failures = int(s.get("failed_runs_24h", 0) or 0)
    interrupted_runs = int(s.get("interrupted_runs_24h", 0) or 0)

    # Warn-icon the header if anything is actually wrong: DB down, an actionable
    # production run failed in the last 24h, or a pipeline phase is failing.
    # Scheduler restart/stale-lock cleanup rows are reported separately below.
    healthy = (
        s.get("db_ok")
        and not actionable_failures
        and not s.get("failing_phases")
    )
    icon = "✅" if healthy else "⚠️"
    lines = [
        f"{icon} <b>UnifiedAnalyzer status</b>",
        f"<i>{_now()}</i>",
        "",
        "<b>People and identity graph</b>",
    ]
    lines.append(
        f"Tracking {_num(s.get('entity_count'))} entities: "
        f"{_num(s.get('primary'))} primary identity records and "
        f"{_num(s.get('secondary'))} secondary/supporting records."
    )
    # Faces: detected corpus vs actually linked to a tracked entity.
    lines.append(
        f"Faces: {_num(s.get('faces_detected'))} detected in media; "
        f"{_num(s.get('faces_linked'))} linked to known entities."
    )
    lines.append(
        f"Alerts: {_num(s.get('alerts_24h'))} new in the last 24h; "
        f"{_num(s.get('unread'))} still unread."
    )
    lines.append("")
    lines.append("<b>Pipeline</b>")
    if s.get("run_state"):
        run_state = str(s["run_state"]).replace(" · ", "; ")
        lines.append(f"Current state: {run_state}.")
    else:
        lines.append("Current state: unknown; scheduler has not reported a run state yet.")

    if actionable_failures:
        lines.append(
            f"Actionable failed production runs in the last 24h: {actionable_failures}."
        )
        for failure in (s.get("recent_failed_runs") or [])[:3]:
            finished_at = failure.get("finished_at")
            if hasattr(finished_at, "strftime"):
                when = finished_at.strftime("%Y-%m-%d %H:%M UTC")
            else:
                when = str(finished_at or "unknown time")
            run_type = str(failure.get("run_type") or "unknown run").replace("_", " ")
            error = str(failure.get("error_message") or "no error captured").replace("\n", " ")
            lines.append(f"• {_esc(run_type)} at {_esc(when)}: <code>{_esc(error[:240])}</code>")
    else:
        lines.append("Actionable failed production runs in the last 24h: 0.")

    if interrupted_runs:
        lines.append(
            f"Restart/stale-lock cleanup rows in the last 24h: {interrupted_runs}. "
            f"These are kept for audit history, but they are not counted as active pipeline failures."
        )
    if s.get("failing_phases"):
        lines.append(f"Failing phases: {', '.join(s['failing_phases'])}.")
    else:
        lines.append("Failing phases: none currently reported.")

    lines.append("")
    lines.append("<b>Collector signals</b>")
    if s.get("collectors_down"):
        lines.append(
            "Quiet sources flagged by analyzer: "
            + ", ".join(s["collectors_down"])
            + "."
        )
    else:
        lines.append("No collector source is currently quiet enough for analyzer to flag.")

    lines.append("")
    lines.append(f"Dashboard: {url}")
    await telegram.send("\n".join(lines))
