#!/usr/bin/env bash
# Nexus preflight validator — run before install.sh
# Validates system readiness without making any changes.
#
# Usage:
#   bash scripts/preflight_check.sh
#   bash scripts/preflight_check.sh --fix   (auto-fix where possible)
#
# Exit codes:
#   0 = all checks passed
#   1 = checks failed (see FAILED list)
#   2 = some warnings but may proceed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FIX_MODE="${1:-}"
PASSED=0
WARNED=0
FAILED=0

# ── Color helpers ─────────────────────────────────────────────────────────────

green()  { printf "\033[32m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }
red()    { printf "\033[31m%s\033[0m" "$*"; }
bold()   { printf "\033[1m%s\033[0m"  "$*"; }

pass()   { echo "  $(green "✓") $*"; PASSED=$((PASSED + 1)); }
warn()   { echo "  $(yellow "!") $*"; WARNED=$((WARNED + 1)); }
fail()   { echo "  $(red "✗") $*"; FAILED=$((FAILED + 1)); }

header() {
    echo
    echo "$(bold "=== $* ===")"
}

# ── Checks ────────────────────────────────────────────────────────────────────

header "OS & Architecture"

if [[ "$OSTYPE" != "linux"* ]]; then
    fail "Linux required (detected: $OSTYPE)"
else
    pass "Linux OS detected"
fi

if [[ $(uname -m) != "x86_64" ]]; then
    warn "Expected x86_64, got $(uname -m) — may work but not tested"
fi

DISTRO=""
if [[ -f /etc/os-release ]]; then
    DISTRO=$(. /etc/os-release && echo "$ID")
fi

if [[ "$DISTRO" == "ubuntu" ]] || [[ "$DISTRO" == "debian" ]]; then
    pass "Debian/Ubuntu detected"
else
    warn "Installer tested on Ubuntu/Debian — detected: ${DISTRO:-unknown}"
fi


header "Root Privileges"

if [[ $EUID -eq 0 ]]; then
    pass "Running as root"
else
    fail "Installer requires root — run: sudo bash scripts/preflight_check.sh"
fi


header "System Resources"

# Disk space: require at least 2GB free in /
DISK_FREE_KB=$(df / | awk 'NR==2 {print $4}')
DISK_FREE_GB=$((DISK_FREE_KB / 1024 / 1024))

if [[ $DISK_FREE_GB -ge 2 ]]; then
    pass "Disk space: ${DISK_FREE_GB}GB free (need ≥2GB)"
else
    fail "Insufficient disk space: ${DISK_FREE_GB}GB free (need ≥2GB)"
fi

# Memory: warn if <1GB available
MEM_FREE_KB=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
MEM_FREE_GB=$((MEM_FREE_KB / 1024 / 1024))

if [[ $MEM_FREE_GB -ge 1 ]]; then
    pass "Memory: ${MEM_FREE_GB}GB available"
else
    warn "Low memory: ${MEM_FREE_GB}GB available (recommended ≥1GB)"
fi


header "Network Connectivity"

# DNS resolution
if getent hosts github.com > /dev/null 2>&1; then
    pass "DNS resolution working"
else
    fail "Cannot resolve github.com — check network/DNS"
fi

# GitHub connectivity (non-blocking)
if timeout 5 curl -sSL --head https://github.com > /dev/null 2>&1; then
    pass "Can reach github.com"
else
    warn "Cannot reach https://github.com (may fail during clone)"
fi


header "Python"

PYTHON_BIN=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" --version 2>&1 | awk '{print $2}')
        MAJOR="${VER%%.*}"
        MINOR="${VER#*.}"
        MINOR="${MINOR%.*}"
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]]; then
            PYTHON_BIN="$cmd"
            pass "Python $VER ($cmd)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -n "$FIX_MODE" ]] && command -v apt-get &>/dev/null; then
        warn "Python 3.11+ not found — will install during setup"
        pass "Will install Python 3.11 (via apt-get)"
    else
        fail "Python 3.11+ required — run: apt-get install python3.11"
    fi
else
    # Check venv module
    if ! "$PYTHON_BIN" -m venv --help &>/dev/null 2>&1; then
        warn "venv module missing — will install during setup"
    fi
fi


header "Required Commands"

for cmd in git curl whiptail; do
    if command -v "$cmd" &>/dev/null; then
        pass "$cmd present"
    else
        if [[ -n "$FIX_MODE" ]] && command -v apt-get &>/dev/null; then
            warn "$cmd missing — will install during setup"
        else
            fail "$cmd not found — run: apt-get install $cmd"
        fi
    fi
done

# Docker is installed unconditionally by install.sh (for container-based tools). Advisory here.
if command -v docker &>/dev/null; then
    pass "docker present ($(docker --version 2>/dev/null | cut -d',' -f1))"
else
    warn "docker not found — install.sh will install it (get.docker.com) during setup"
fi


header "Port Availability"

# Check if common ports are available (systemd-resolved, future services)
# This is advisory — systemd may claim ports but services can still coexist
PORTS_TO_CHECK=(53 8065 8080)

for port in "${PORTS_TO_CHECK[@]}"; do
    if ! timeout 2 bash -c "true > /dev/tcp/127.0.0.1/$port" 2>/dev/null; then
        # Port is available (connection refused is good)
        pass "Port $port available"
    else
        warn "Port $port may be in use (check: lsof -i :$port)"
    fi
done


header "Sudoers Configuration"

# Check if sudoers.d directory exists and is writable
if [[ -d /etc/sudoers.d ]] && [[ -w /etc/sudoers.d ]]; then
    pass "Sudoers.d writable (can set NOPASSWD rules)"
else
    warn "Cannot write to /etc/sudoers.d — manual sudo setup may be required"
fi


header "Summary"

TOTAL=$((PASSED + WARNED + FAILED))

echo
echo "  Passed:  $(green "$PASSED/$TOTAL")"
[[ $WARNED -gt 0 ]] && echo "  Warned:  $(yellow "$WARNED/$TOTAL")"
[[ $FAILED -gt 0 ]] && echo "  Failed:  $(red "$FAILED/$TOTAL")"
echo

if [[ $FAILED -gt 0 ]]; then
    echo "$(red "✗ Checks failed.") Fix errors and retry."
    exit 1
elif [[ $WARNED -gt 0 ]]; then
    echo "$(yellow "! Some warnings.") May proceed, but monitor closely."
    exit 2
else
    echo "$(green "✓ All checks passed. Ready to install.")"
    exit 0
fi
