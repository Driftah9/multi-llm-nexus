#!/bin/bash
# wake-trigger.sh — Write wake events to the Nexus trigger file.
#
# Usage:
#   ./wake-trigger.sh <reason> <source> <summary> [priority] [json_data]
#
# Example:
#   ./wake-trigger.sh new_message telegram-poll "3 new messages" 3
#   ./wake-trigger.sh service_alert health-check "API down" 1
#   ./wake-trigger.sh scheduled_task backup "Backup done, 2 warnings" 5
#
# Any cron job, systemd timer, or script can call this to wake Nexus.

set -euo pipefail

TRIGGER_FILE="${NEXUS_TRIGGER:-/tmp/nexus-wake.trigger}"
REASON="${1:?Usage: wake-trigger.sh <reason> <source> <summary> [priority] [data]}"
SOURCE="${2:?Missing source}"
SUMMARY="${3:?Missing summary}"
PRIORITY="${4:-5}"
DATA="${5:-{}}"
TIMESTAMP=$(date +%s.%N)

echo "{\"reason\":\"${REASON}\",\"source\":\"${SOURCE}\",\"summary\":\"${SUMMARY}\",\"timestamp\":${TIMESTAMP},\"priority\":${PRIORITY},\"data\":${DATA}}" >> "${TRIGGER_FILE}"
