#!/bin/bash
# check-inbox.sh — Monitor a directory for new files (zero tokens).
#
# Run via cron: * * * * * /path/to/check-inbox.sh
#
# When new files appear in the inbox, writes a wake event so Nexus
# can process them.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WAKE_TRIGGER="${SCRIPT_DIR}/../wake-trigger.sh"

INBOX_DIR="${NEXUS_INBOX:-$HOME/inbox}"
MARKER="/tmp/nexus-inbox-last-check"

if [ ! -d "$INBOX_DIR" ]; then
    exit 0
fi

if [ ! -f "$MARKER" ]; then
    touch "$MARKER"
    exit 0
fi

NEW_FILES=$(find "$INBOX_DIR" -newer "$MARKER" -type f 2>/dev/null | head -20)
COUNT=$(echo "$NEW_FILES" | grep -c . 2>/dev/null || echo 0)

if [ "$COUNT" -gt 0 ]; then
    SUMMARY="${COUNT} new file(s) in inbox"
    "$WAKE_TRIGGER" "file_change" "inbox-watcher" "$SUMMARY" 3
fi

touch "$MARKER"
