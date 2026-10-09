#!/usr/bin/env bash
#
# hlidskjalf watchdog — revive the edge services when they go unhealthy.
#
# Checks a health endpoint (URL mode) or a process pattern (process mode)
# and restarts the systemd service with BOUNDED retries, logging every
# step. Safe to run from cron every minute or as a systemd timer.
#
# Config via environment (config, not code):
#   SERVICE          systemd unit to restart      (default: hlidskjalf-heimdall)
#   CHECK_MODE       url | process                (default: process)
#   HEALTH_URL       health endpoint              (default: http://127.0.0.1:8080/health)
#   PROCESS_PATTERN  pgrep -f pattern             (default: hlidskjalf.heimdall.gateway)
#   MAX_RETRIES      restart attempts before giving up (default: 3)
#   RETRY_DELAY      seconds between attempts     (default: 10)
#   LOG_FILE         where to log                 (default: /var/log/hlidskjalf-watchdog.log)
#
# Exit 0: service healthy or revived. Exit 1: retries exhausted.
set -euo pipefail

SERVICE="${SERVICE:-hlidskjalf-heimdall}"
CHECK_MODE="${CHECK_MODE:-process}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
PROCESS_PATTERN="${PROCESS_PATTERN:-hlidskjalf.heimdall.gateway}"
MAX_RETRIES="${MAX_RETRIES:-3}"
RETRY_DELAY="${RETRY_DELAY:-10}"
LOG_FILE="${LOG_FILE:-/var/log/hlidskjalf-watchdog.log}"

log() {
    local msg="$1"
    local line
    line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') [watchdog] ${SERVICE}: ${msg}"
    echo "${line}"
    # Best-effort file log; never fail the watchdog because logging failed.
    echo "${line}" >> "${LOG_FILE}" 2>/dev/null || true
}

is_healthy() {
    case "${CHECK_MODE}" in
        url)
            curl -fsS --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1
            ;;
        process)
            pgrep -f "${PROCESS_PATTERN}" >/dev/null 2>&1
            ;;
        *)
            log "ERROR: unknown CHECK_MODE '${CHECK_MODE}' (want url|process)"
            return 2
            ;;
    esac
}

main() {
    if is_healthy; then
        # Quiet on healthy rounds: cron stays silent unless something happens.
        exit 0
    fi

    log "UNHEALTHY (mode=${CHECK_MODE}); attempting restart (max ${MAX_RETRIES} retries)"

    local attempt=1
    while [ "${attempt}" -le "${MAX_RETRIES}" ]; do
        log "restart attempt ${attempt}/${MAX_RETRIES}: systemctl restart ${SERVICE}"
        if systemctl restart "${SERVICE}" 2>/dev/null; then
            sleep "${RETRY_DELAY}"
            if is_healthy; then
                log "REVIVED on attempt ${attempt}"
                exit 0
            fi
            log "still unhealthy after restart attempt ${attempt}"
        else
            log "systemctl restart failed on attempt ${attempt}"
        fi
        attempt=$((attempt + 1))
        if [ "${attempt}" -le "${MAX_RETRIES}" ]; then
            sleep "${RETRY_DELAY}"
        fi
    done

    log "GIVING UP after ${MAX_RETRIES} retries — manual intervention needed"
    exit 1
}

main "$@"
