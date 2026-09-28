#!/bin/sh
# face_worker entrypoint: best-effort CIFS mounts for the optional W:/X: drive
# scan (see DRIVE_SOURCES in docker-compose.yml). Runs as root (needed for the
# mount syscall + to drop privileges afterwards), then execs the real command
# as the unprivileged appuser.
#
# Resilience fix (2026-09-28): the w-drive SMB share disappeared from the
# configured SMB host (renamed/retired upstream, outside this repo's control)
# and Docker's declarative `volumes:` CIFS mount is all-or-nothing - a single
# failed named volume blocks the WHOLE container from being created, so
# face_worker sat dead in "Created" state for 5+ days even though x-drive was
# perfectly reachable the entire time. Mounting here instead means a missing
# share degrades gracefully to an empty (but present) directory - DriveScanner
# already fails closed / no-ops on paths with nothing to walk, it does not
# error - rather than blocking startup entirely. If a share comes back later,
# the very next container recreate/restart just picks it up again automatically.
set -eu

mount_optional_cifs() {
    share="$1"
    target="$2"

    if [ -z "${SMB_HOST:-}" ] || [ -z "${SMB_USER:-}" ] || [ -z "${SMB_PASS:-}" ]; then
        echo "face_worker entrypoint: SMB_HOST/SMB_USER/SMB_PASS not set - skipping $target (no drive scan there this run)" >&2
        mkdir -p "$target"
        return 0
    fi

    mkdir -p "$target"
    if timeout 15 mount -t cifs "//${SMB_HOST}/${share}" "$target" \
        -o "username=${SMB_USER},password=${SMB_PASS},vers=3.0,ro,uid=0,gid=0,file_mode=0444,dir_mode=0555" \
        >/tmp/mount_${share}.log 2>&1; then
        echo "face_worker entrypoint: mounted //${SMB_HOST}/${share} -> $target" >&2
    else
        echo "face_worker entrypoint: WARNING - could not mount //${SMB_HOST}/${share} (share missing, renamed, or host unreachable) - continuing with an empty $target, this run will just skip that drive" >&2
        cat "/tmp/mount_${share}.log" >&2 || true
    fi
}

mount_optional_cifs "w-drive" /mnt/w
mount_optional_cifs "x-drive" /mnt/x

exec setpriv --reuid=1001 --regid=1001 --clear-groups "$@"
