"""Notification dispatchers for all 7 alert types."""

import html
import logging
from datetime import datetime, timezone

from src.notifications import telegram
from src.notifications.intelligence import INTELLIGENCE_ALERT_TYPES, intelligence_run_lines, intelligence_status_lines

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
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        message_type="startup",
    )


async def notify_shutdown():
    await telegram.send(
        f"\U0001f534 <b>UnifiedAnalyzer shutting down</b>\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        message_type="shutdown",
    )


async def notify_new_alerts(alerts: list[dict]):
    if not alerts:
        return
    url = telegram.get_dashboard_url()
    lines = [f"\U0001f514 <b>{len(alerts)} new alert(s)</b>\n"]
    for a in alerts[:10]:
        icon = _SEVERITY_ICON.get(a.get("severity", "info"), "•")
        lines.append(f"{icon} {a['title']}")
    intel = [a for a in alerts if a.get("alert_type") in INTELLIGENCE_ALERT_TYPES]
    if intel:
        counts: dict[str, int] = {}
        for a in intel:
            counts[a["alert_type"]] = counts.get(a["alert_type"], 0) + 1
        rendered = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in sorted(counts.items()))
        lines.append(f"Intel signals: {rendered}")
    if len(alerts) > 10:
        lines.append(f"  ...and {len(alerts) - 10} more")
    lines.append(f"\n{url}/alerts")
    await telegram.send("\n".join(lines), message_type="new_alerts")


async def notify_run_summary(run_type: str, stats: dict, *, run_id: str | None = None):
    events = stats.get("events", 0)
    alerts = stats.get("alerts", 0)
    entities = stats.get("entities", 0)
    signals = stats.get("signals", 0)
    intel_lines = intelligence_run_lines(stats)

    if events == 0 and alerts == 0 and entities == 0 and signals == 0 and not intel_lines:
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

    lines = [f"{icon} <b>{run_type.title()} run complete</b>"]
    if parts:
        lines.append(", ".join(parts))
    lines.extend(intel_lines)
    await telegram.send("\n".join(lines[:8]), message_type="run_summary", related_run_id=run_id)


async def notify_collector_health(issues: list[dict]):
    if not issues:
        return
    url = telegram.get_collector_dashboard_url()
    lines = ["\U0001f6a8 <b>Collector health warning</b>\n"]
    for issue in issues:
        lines.append(f"• <b>{issue['source']}</b>: {issue['message']}")
    lines.append(f"\n{url}/collectors")
    await telegram.send("\n".join(lines), message_type="collector_health")


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
    if digest.get("intelligence"):
        lines.append("")
        lines.append("<b>Intelligence</b>")
        lines.extend(intelligence_status_lines(digest["intelligence"])[:4])

    lines.append(f"\n{url}")
    await telegram.send("\n".join(lines), message_type="daily_digest")


async def notify_merge_candidate(entity_a_name: str, entity_b_name: str,
    confidence: float,
    shared_signal: str,
    *,
    candidate: dict | None = None,
) -> bool:
    """Send a merge-review notification.

    If *candidate* is supplied (full dict from entity_relationships + platform
    links + face look-up) the message includes handles, score, deciding signals,
    face-crop links, and a 2-button inline keyboard so the reviewer can act
    directly from Telegram.
    If *candidate* is None (legacy call) the plain-text fallback is sent.
    """
    from src.notifications.merge_bot import callback_data_yes, callback_data_no

    base_url = telegram.get_dashboard_url()

    if candidate:
        id_a = candidate.get("entity_a", "")
        id_b = candidate.get("entity_b", "")
        name_a = _esc(candidate.get("name_a") or entity_a_name or "Unknown")
        name_b = _esc(candidate.get("name_b") or entity_b_name or "Unknown")
        score = candidate.get("score")
        score_str = f"{float(score):.0%}" if score is not None else f"{confidence:.0%}"
        cross = "cross-platform" if candidate.get("cross_platform") else "same platform"
        signals: list = candidate.get("signals") or [shared_signal]
        sig_str = ", ".join(str(s) for s in signals[:6]) or shared_signal
        handles_a: list = candidate.get("handles_a") or []
        handles_b: list = candidate.get("handles_b") or []
        ha_str = " · ".join(_esc(h) for h in handles_a[:5]) if handles_a else ""
        hb_str = " · ".join(_esc(h) for h in handles_b[:5]) if handles_b else ""
        face_a_rel = candidate.get("face_a")
        face_b_rel = candidate.get("face_b")

        lines = [
            "\U0001f517 <b>Review needed: probable same person</b>",
            f"<b>{name_a}</b>  ↔  <b>{name_b}</b>",
            f"Score: {score_str}  |  {cross}",
        ]
        if ha_str:
            lines.append(f"A:  {ha_str}")
        if hb_str:
            lines.append(f"B:  {hb_str}")
        if sig_str:
            lines.append(f"Signals: {_esc(sig_str)}")
        if face_a_rel:
            lines.append(f"Face A: {base_url}{face_a_rel}")
        if face_b_rel:
            lines.append(f"Face B: {base_url}{face_b_rel}")
        lines.append(f"{base_url}/review")

        text = "\n".join(lines)

        # Only attach the keyboard when we have both entity IDs
        reply_markup: dict | None = None
        if id_a and id_b:
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "\u2705 Same person",  "callback_data": callback_data_yes(id_a, id_b)},
                    {"text": "\u274c Not same",     "callback_data": callback_data_no(id_a, id_b)},
                ]]
            }

        return await telegram.send(text, reply_markup=reply_markup, message_type="merge_candidate")

    # Fallback: legacy plain-text send (no candidate data, no buttons)
    return await telegram.send(
        f"\U0001f517 <b>Review needed: probable same person</b>\n"
        f"<b>{_esc(entity_a_name)}</b> \u2194 <b>{_esc(entity_b_name)}</b>\n"
        f"Probability: {confidence:.0%} via {_esc(shared_signal)}\n"
        f"No automatic merge occurred; review this pair before merging.\n"
        f"{base_url}/entities",
        message_type="merge_candidate",
    )

async def notify_error(run_type: str, error: str):
    await telegram.send(
        f"❌ <b>{run_type.title()} run failed</b>\n"
        f"<code>{error[:500]}</code>",
        message_type="error",
    )


async def notify_status(s: dict):
    """Periodic system-status heartbeat to the group chat.

    Unlike the event-driven notifications above (run complete, new alert, …),
    this is a recurring "here's where things stand" snapshot posted on an
    interval by the scheduler, so the group always has a current picture even
    when nothing eventful happened. Built by scheduler._build_status.
    """
    url = telegram.get_dashboard_url()
    unrecovered_failures = int(s.get("failed_runs_24h", 0) or 0)
    recovered_failures = int(s.get("recovered_failed_runs_24h", 0) or 0)
    interrupted_runs = int(s.get("interrupted_runs_24h", 0) or 0)

    # Warn-icon the header only for current/actionable breakage: DB down, an
    # unrecovered production failure, or a pipeline phase whose latest status
    # failed. Recovered failures and restart cleanup rows stay visible below.
    healthy = (
        s.get("db_ok")
        and not unrecovered_failures
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

    if unrecovered_failures:
        lines.append(
            f"Unrecovered failed production runs in the last 24h: {unrecovered_failures}."
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
        lines.append("Unrecovered failed production runs in the last 24h: 0.")

    if recovered_failures:
        lines.append(
            f"Recovered production failures still in the 24h audit window: {recovered_failures}."
        )
        for failure in (s.get("recent_recovered_failed_runs") or [])[:2]:
            failed_at = failure.get("finished_at")
            recovered_at = failure.get("recovered_at")
            failed_when = failed_at.strftime("%Y-%m-%d %H:%M UTC") if hasattr(failed_at, "strftime") else str(failed_at or "unknown time")
            recovered_when = recovered_at.strftime("%Y-%m-%d %H:%M UTC") if hasattr(recovered_at, "strftime") else str(recovered_at or "unknown time")
            run_type = str(failure.get("run_type") or "unknown run").replace("_", " ")
            error = str(failure.get("error_message") or "no error captured").replace("\n", " ")
            lines.append(
                f"• {_esc(run_type)} failed at {_esc(failed_when)}, "
                f"then recovered at {_esc(recovered_when)}: <code>{_esc(error[:160])}</code>"
            )

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
    lines.append("<b>Intelligence</b>")
    lines.extend(intelligence_status_lines(s.get("intelligence")))

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
    await telegram.send("\n".join(lines), message_type="status")
