#!/bin/sh
# embed-backfill-loop.sh - resilient backfill wrapper for the scheduler container.
#
# Runs `python -m src.main embed-backfill` in a while loop. If the python
# process exits (drain complete, crash, sigterm, whatever), wait N seconds
# then relaunch. Timestamped, append-mode log so container recreates and
# nohup redirects can't erase the failure history.
#
# Launch from host via:
#   docker exec -d docker-scheduler-1 sh /app/embed-backfill-loop.sh
#
# Stop via:
#   docker exec docker-scheduler-1 sh -c "touch /tmp/embed-backfill.stop"
#   # then wait for the current python child to exit; the wrapper checks
#   # for the stop flag between iterations.
set -eu

LOG=${EMBED_BACKFILL_LOG:-/tmp/embed-backfill.log}
STOP_FLAG=${EMBED_BACKFILL_STOP:-/tmp/embed-backfill.stop}
BATCH_SIZE=${EMBED_BACKFILL_BATCH:-500}
RESPAWN_SLEEP=${EMBED_BACKFILL_RESPAWN_SLEEP:-60}
DRAIN_SLEEP=${EMBED_BACKFILL_DRAIN_SLEEP:-1800}
FAIL_BACKOFF=${EMBED_BACKFILL_FAIL_BACKOFF:-30}
MAX_CONSEC=${EMBED_BACKFILL_MAX_CONSEC:-5}

rm -f "$STOP_FLAG"

log() {
    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    printf '%s [wrapper] %s\n' "$ts" "$*" | tee -a "$LOG"
}

log "starting embed-backfill loop wrapper. batch_size=$BATCH_SIZE respawn=${RESPAWN_SLEEP}s drain=${DRAIN_SLEEP}s"

while :; do
    if [ -f "$STOP_FLAG" ]; then
        log "STOP flag $STOP_FLAG present -> exiting"
        rm -f "$STOP_FLAG"
        exit 0
    fi

    log "launching python child..."
    # The python side handles per-iter try/except + backoff; this wrapper is
    # a safety net for process-level crashes (OOM, ONNXRuntime segfaults, etc).
    if python -m src.main embed-backfill \
        --batch-size "$BATCH_SIZE" \
        --log-file "$LOG" \
        --fail-backoff "$FAIL_BACKOFF" \
        --max-consecutive-failures "$MAX_CONSEC" >> "$LOG" 2>&1; then
        exit_status=0
    else
        exit_status=$?
    fi

    log "python child exited with status $exit_status"

    # If it drained (exit 0 after logging DRAINED), sleep longer before
    # re-checking - the scheduler's ~2h ticks add fresh events, and we
    # only need to catch up periodically.
    if [ "$exit_status" -eq 0 ]; then
        log "drained/clean exit -> sleeping ${DRAIN_SLEEP}s before re-check"
        sleep "$DRAIN_SLEEP"
    else
        log "abnormal exit -> sleeping ${RESPAWN_SLEEP}s before respawn"
        sleep "$RESPAWN_SLEEP"
    fi
done
