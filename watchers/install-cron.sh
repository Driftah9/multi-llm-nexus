#!/bin/bash
# install-cron.sh — Install watcher cron jobs for Nexus.
#
# Usage: ./install-cron.sh [watcher_dir]
#
# Safe to re-run — removes old entries before adding new ones.

set -euo pipefail

WATCHER_DIR="${1:-$(cd "$(dirname "$0")/examples" && pwd)}"
CRON_TAG="# nexus-watcher"

echo "Installing Nexus watchers from: ${WATCHER_DIR}"

# Remove existing nexus cron entries
(crontab -l 2>/dev/null | grep -v "$CRON_TAG") | crontab - 2>/dev/null || true

CRON_ENTRIES=""

# Service health: every 5 minutes
if [ -f "${WATCHER_DIR}/check-service-health.sh" ]; then
    CRON_ENTRIES="${CRON_ENTRIES}*/5 * * * * ${WATCHER_DIR}/check-service-health.sh ${CRON_TAG}
"
fi

# Inbox: every minute
if [ -f "${WATCHER_DIR}/check-inbox.sh" ]; then
    CRON_ENTRIES="${CRON_ENTRIES}* * * * * ${WATCHER_DIR}/check-inbox.sh ${CRON_TAG}
"
fi

# Disk usage: every 15 minutes
if [ -f "${WATCHER_DIR}/check-disk.sh" ]; then
    CRON_ENTRIES="${CRON_ENTRIES}*/15 * * * * ${WATCHER_DIR}/check-disk.sh ${CRON_TAG}
"
fi

# Model lifecycle: first of each month at 09:00
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "${SCRIPT_DIR}/scripts/model_check.py" ] && [ -f "${SCRIPT_DIR}/config/model_sources.yaml" ]; then
    CRON_ENTRIES="${CRON_ENTRIES}0 9 1 * * cd ${SCRIPT_DIR} && python scripts/model_check.py >> /tmp/nexus-model-check.log 2>&1 ${CRON_TAG}
"
fi

if [ -n "$CRON_ENTRIES" ]; then
    (crontab -l 2>/dev/null; echo "$CRON_ENTRIES") | crontab -
    echo "Installed watchers:"
    crontab -l | grep "$CRON_TAG"
else
    echo "No watcher scripts found in ${WATCHER_DIR}"
    echo "Copy examples from watchers/examples/ and customize for your environment."
fi
