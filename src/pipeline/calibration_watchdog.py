"""Calibration cutover watchdog (secondary pipeline phase).

Runs at the tail of every incremental / full-resolution cycle. Watches the
identity_labels table; once >= CALIBRATION_MONITOR_MIN_LABELS rows have
accumulated AND both classes are present, runs the same leave-one-out CV as
`python -m src.pipeline.identity_calibration validate`. If the LR model beats
the hand-set noisy-OR by CALIBRATION_MONITOR_MIN_DELTA AUC (default 0.05),
raises a one-shot CALIBRATION_READY alert + Telegram notification so the
operator knows to flip IDENTITY_MODEL_ENABLED=1.

DELIBERATELY does NOT auto-flip the env flag - calibration cutover is a
one-way trapdoor with real correctness implications. Human-in-the-loop stays.

Dedup: the alert is written with detail->>'label_count' set to the current
count, so we don't spam. The next check only fires if the label count has
grown by CALIBRATION_MONITOR_RECHECK_STRIDE (default 10) since the last
alert - i.e. we alert again after ~10 more merges/dismisses in case the
model got better and delta grew.
"""
import json
import logging
import os

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


async def check_calibration_readiness() -> dict:
    """Secondary phase: emit CALIBRATION_READY alert when LR is proven to
    beat noisy-OR on current labels."""
    min_labels = _int_env("CALIBRATION_MONITOR_MIN_LABELS", 50)
    min_delta = _float_env("CALIBRATION_MONITOR_MIN_DELTA", 0.05)
    recheck_stride = _int_env("CALIBRATION_MONITOR_RECHECK_STRIDE", 10)

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                count(*) AS n_labels,
                count(*) FILTER (WHERE label = 1) AS n_pos,
                count(*) FILTER (WHERE label = 0) AS n_neg
            FROM identity_labels
        """)
        n_labels = int(stats["n_labels"])
        n_pos = int(stats["n_pos"])
        n_neg = int(stats["n_neg"])

        if n_labels < min_labels or n_pos == 0 or n_neg == 0:
            return {
                "skipped": "insufficient_labels",
                "labels": n_labels,
                "min_labels": min_labels,
                "positives": n_pos,
                "negatives": n_neg,
            }

        # Dedup: if we already alerted at this (or nearby) label count, skip.
        last_alert = await conn.fetchrow("""
            SELECT detail->>'label_count' AS lc, detected_at
            FROM alerts
            WHERE alert_type = 'CALIBRATION_READY'
            ORDER BY detected_at DESC LIMIT 1
        """)
        if last_alert and last_alert["lc"]:
            try:
                last_lc = int(last_alert["lc"])
            except (TypeError, ValueError):
                last_lc = -1
            if last_lc >= 0 and (n_labels - last_lc) < recheck_stride:
                return {
                    "skipped": "already_alerted",
                    "labels": n_labels,
                    "last_alert_label_count": last_lc,
                    "recheck_stride": recheck_stride,
                }

    # Blocking CV: pull the validate result out of the calibrator. Local
    # import so this module is safe to import at collection time even if
    # sklearn isn't available.
    try:
        from src.pipeline.identity_calibration import validate_calibration
        result = await validate_calibration()
    except Exception as e:  # noqa: BLE001 - phase is non-fatal
        logger.exception("calibration_watchdog: validate_calibration failed")
        return {"error": "validate_failed", "reason": str(e)[:200]}

    if result.get("error"):
        return {"error": "validate_returned_error", **result}

    delta = float(result.get("delta") or 0.0)
    lr_auc = float(result.get("lr_auc_loo") or 0.0)
    no_auc = float(result.get("noisy_or_auc") or 0.0)

    ready = delta >= min_delta
    summary = {
        "labels": n_labels,
        "n_positive": n_pos,
        "n_negative": n_neg,
        "lr_auc_loo": lr_auc,
        "noisy_or_auc": no_auc,
        "delta": delta,
        "min_delta": min_delta,
        "ready_for_cutover": ready,
    }

    if not ready:
        logger.info(
            "calibration_watchdog: not ready (labels=%d, LR AUC=%.4f, "
            "noisy-OR AUC=%.4f, delta=%.4f, need>=%.2f)",
            n_labels, lr_auc, no_auc, delta, min_delta)
        return {"status": "not_ready", **summary}

    # READY. Write the alert (dashboard picks it up) and telegram-notify.
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO alerts (entity_id, alert_type, severity, title, detail)
            VALUES (NULL, 'CALIBRATION_READY', 'info', $1, $2::jsonb)
        """,
            f"Calibration cutover ready: LR beats noisy-OR by {delta:.3f} AUC "
            f"({n_labels} labels)",
            json.dumps({
                "label_count": n_labels,
                "n_positive": n_pos,
                "n_negative": n_neg,
                "lr_auc_loo": round(lr_auc, 4),
                "noisy_or_auc": round(no_auc, 4),
                "delta": round(delta, 4),
                "min_delta": min_delta,
                "next_step": (
                    "Run `python -m src.pipeline.identity_calibration validate "
                    "--strict --min-delta 0.05` to double-check, then set "
                    "IDENTITY_MODEL_ENABLED=1 in .env and recreate scheduler."
                ),
            }))

    logger.warning(
        "calibration_watchdog: READY for cutover — labels=%d, delta=%.4f "
        "(LR %.4f vs noisy-OR %.4f). CALIBRATION_READY alert written.",
        n_labels, delta, lr_auc, no_auc)

    # Telegram notify (fire-and-forget; errors non-fatal).
    try:
        from src.notifications.telegram import send
        msg = (
            f"<b>Calibration cutover ready</b>\n"
            f"Labels: <b>{n_labels}</b> ({n_pos}\u2713 / {n_neg}\u2717)\n"
            f"LR AUC: <b>{lr_auc:.4f}</b>\n"
            f"Noisy-OR AUC: <b>{no_auc:.4f}</b>\n"
            f"Delta: <b>+{delta:.4f}</b> (\u2265 {min_delta} threshold)\n\n"
            f"<i>Flip <code>IDENTITY_MODEL_ENABLED=1</code> in .env and recreate "
            f"scheduler to activate the trained model.</i>"
        )
        await send(msg)
    except Exception:
        logger.debug("calibration_watchdog: telegram send failed (non-fatal)",
                     exc_info=True)

    return {"status": "ready", "alert_emitted": True, **summary}
