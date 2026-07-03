"""Notification dispatchers for all 7 alert types."""

import logging
from datetime import datetime, timezone

from src.notifications import telegram

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {
    "critical": "⚠️",  # warning sign
    "warning": "\U0001f7e1",     # yellow circle
    "info": "ℹ️",       # info
}


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
    url = telegram.get_dashboard_url()
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
    # Warn-icon the header if anything is actually wrong (DB down, a run failed in
    # the last 24h, or a pipeline phase is failing) — not just a fixed ✅.
    healthy = (
        s.get("db_ok")
        and not s.get("failed_runs_24h")
        and not s.get("failing_phases")
    )
    icon = "✅" if healthy else "⚠️"
    lines = [f"{icon} <b>UnifiedAnalyzer status</b>"]
    lines.append(
        f"Entities: {s.get('entity_count', 0)} "
        f"({s.get('primary', 0)} primary, {s.get('secondary', 0)} secondary)"
    )
    # Faces: detected corpus vs actually linked to a tracked entity.
    lines.append(
        f"Faces: {s.get('faces_detected', 0)} detected, "
        f"{s.get('faces_linked', 0)} linked to entities"
    )
    lines.append(f"Alerts: {s.get('alerts_24h', 0)} (24h), {s.get('unread', 0)} unread")
    if s.get("run_state"):
        lines.append(f"Pipeline: {s['run_state']}")
    if s.get("failed_runs_24h"):
        lines.append(f"⚠️ Failed runs (24h): {s['failed_runs_24h']}")
    if s.get("failing_phases"):
        lines.append(f"⚠️ Failing phases: {', '.join(s['failing_phases'])}")
    if s.get("collectors_down"):
        lines.append(f"⚠️ Collectors quiet: {', '.join(s['collectors_down'])}")
    lines.append(f"\n{url}")
    await telegram.send("\n".join(lines))
