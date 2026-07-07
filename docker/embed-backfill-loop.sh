#!/bin/sh
# embed-backfill-loop.sh - resilient backfill wrapper for the scheduler container.
#
# Runs `python -m src.main embed-backfill` in a while loop. If the python
# process exits (drain complete, crash, sigterm, whatever), wait N seconds
# then relaunch. Log strategy:
#   * $LOG           : primary structured log (python's RotatingFileHandler
#                      writes here directly + wrapper tees its own status
#                      lines here). Owned by python.
#   * $LOG.stdout    : capture of python's stdout/stderr (catches anything
#                      the logger didn't handle: pre-init prints, uncaught
#                      exception tracebacks, Docker healthcheck spam).
#                      Rotated by the shell (kept small).
#
# Launch from host via:
#   docker exec -d docker-scheduler-1 /app/embed-backfill-loop.sh
#
# Stop via:
#   docker exec docker-scheduler-1 touch /tmp/embed-backfill.stop
set -eu

LOG=${EMBED_BACKFILL_LOG:-/tmp/embed-backfill.log}
STOP_FLAG=${EMBED_BACKFILL_STOP:-/tmp/embed-backfill.stop}
BATCH_SIZE=${EMBED_BACKFILL_BATCH:-500}
RESPAWN_SLEEP=${EMBED_BACKFILL_RESPAWN_SLEEP:-60}
DRAIN_SLEEP=${EMBED_BACKFILL_DRAIN_SLEEP:-1800}
FAIL_BACKOFF=${EMBED_BACKFILL_FAIL_BACKOFF:-30}
MAX_CONSEC=${EMBED_BACKFILL_MAX_CONSEC:-5}
STDOUT_FILE="${LOG}.stdout"
STDOUT_MAX_BYTES=${EMBED_BACKFILL_STDOUT_MAX_BYTES:-5242880}  # 5MB

rm -f "$STOP_FLAG"

log() {
    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    printf '%s [wrapper] %s\n' "$ts" "$*" >> "$LOG"
}

# Rotate the stdout sidecar if it's grown past STDOUT_MAX_BYTES.
rotate_stdout_if_big() {
    if [ -f "$STDOUT_FILE" ]; then
        sz=$(wc -c < "$STDOUT_FILE" 2>/dev/null || echo 0)
        if [ "$sz" -gt "$STDOUT_MAX_BYTES" ]; then
            mv "$STDOUT_FILE" "${STDOUT_FILE}.1"
            log "rotated $STDOUT_FILE (was $sz bytes)"
        fi
    fi
}

log "starting embed-backfill loop wrapper. batch_size=$BATCH_SIZE respawn=${RESPAWN_SLEEP}s drain=${DRAIN_SLEEP}s"

while :; do
    if [ -f "$STOP_FLAG" ]; then
        log "STOP flag $STOP_FLAG present -> exiting"
        rm -f "$STOP_FLAG"
        exit 0
    fi

    rotate_stdout_if_big

    log "launching python child..."
    # Python owns $LOG via its RotatingFileHandler. Stdout/stderr goes to
    # $LOG.stdout only, so no line duplicates in the primary log.
    if python -m src.main embed-backfill \
        --batch-size "$BATCH_SIZE" \
        --log-file "$LOG" \
        --fail-backoff "$FAIL_BACKOFF" \
        --max-consecutive-failures "$MAX_CONSEC" \
        >> "$STDOUT_FILE" 2>&1; then
        exit_status=0
    else
        exit_status=$?
    fi

    log "python child exited with status $exit_status"

    # Clean exit (drain complete) sleeps longer than an abnormal exit.
    if [ "$exit_status" -eq 0 ]; then
        log "drained/clean exit -> sleeping ${DRAIN_SLEEP}s before re-check"
        sleep "$DRAIN_SLEEP"
    else
        log "abnormal exit -> sleeping ${RESPAWN_SLEEP}s before respawn"
        sleep "$RESPAWN_SLEEP"
    fi
done
