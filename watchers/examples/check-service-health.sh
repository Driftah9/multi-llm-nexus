#!/bin/bash
# check-service-health.sh — Monitor service endpoints (zero tokens).
#
# Run via cron: */5 * * * * /path/to/check-service-health.sh
#
# Edit ENDPOINTS to match your environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WAKE_TRIGGER="${SCRIPT_DIR}/../wake-trigger.sh"

# Configure: URL|Name|Timeout_seconds
ENDPOINTS=(
    # "http://localhost:8123|Home Assistant|5"
    # "http://localhost:9000|Portainer|5"
    # "http://localhost:3000|Grafana|5"
)

FAILURES=""
FAILURE_COUNT=0

for entry in "${ENDPOINTS[@]}"; do
    IFS='|' read -r URL NAME TIMEOUT <<< "$entry"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time "${TIMEOUT}" "${URL}" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "000" ] || [ "$HTTP_CODE" -ge 500 ]; then
        FAILURES="${FAILURES}${NAME} (HTTP ${HTTP_CODE}), "
        FAILURE_COUNT=$((FAILURE_COUNT + 1))
    fi
done

if [ "$FAILURE_COUNT" -gt 0 ]; then
    SUMMARY="${FAILURE_COUNT} service(s) down: ${FAILURES%, }"
    "$WAKE_TRIGGER" "service_alert" "health-check" "$SUMMARY" 2
fi
