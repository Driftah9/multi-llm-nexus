#!/bin/bash
# check-disk.sh — Alert when disk usage exceeds threshold (zero tokens).
#
# Run via cron: */15 * * * * /path/to/check-disk.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WAKE_TRIGGER="${SCRIPT_DIR}/../wake-trigger.sh"

THRESHOLD="${NEXUS_DISK_THRESHOLD:-85}"

ALERTS=""
ALERT_COUNT=0

while IFS= read -r line; do
    USAGE=$(echo "$line" | awk '{print $5}' | tr -d '%')
    MOUNT=$(echo "$line" | awk '{print $6}')
    if [ "$USAGE" -ge "$THRESHOLD" ]; then
        ALERTS="${ALERTS}${MOUNT} at ${USAGE}%, "
        ALERT_COUNT=$((ALERT_COUNT + 1))
    fi
done < <(df -h --output=pcent,target | tail -n +2 | grep -v tmpfs)

if [ "$ALERT_COUNT" -gt 0 ]; then
    SUMMARY="Disk pressure: ${ALERTS%, }"
    "$WAKE_TRIGGER" "service_alert" "disk-check" "$SUMMARY" 2
fi
