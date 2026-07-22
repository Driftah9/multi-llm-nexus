#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Unattended TEST installer — drives the REAL install.sh headlessly with a preset
# answers file, then verifies the install end-to-end. This is a THIN WRAPPER: it
# adds NO installer logic (so it can't drift from install.sh) — it only sets the
# unattended env, runs install.sh, and checks the result.
#
# Run on a clean VM, as root, from a clone of the branch under test:
#     git clone -b <branch> https://github.com/Driftah9/multi-llm-nexus.git
#     sudo multi-llm-nexus/scripts/install_test.sh
#
# Overridable via env:
#   NEXUS_USERNAME     service account to create   (default: nexus)
#   NEXUS_BRANCH       branch install.sh clones    (default: docs/directory-layout)
#   NEXUS_ANSWERS_SRC  preset answers file          (default: bundled config/answers.test.yaml)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

: "${NEXUS_USERNAME:=nexus}"
: "${NEXUS_BRANCH:=experiment/llmfit-hardware-fit}"   # this branch (carries the llmfit prototype)
: "${NEXUS_ANSWERS_SRC:=$REPO_DIR/config/answers.test.yaml}"

INSTALL_SH="$REPO_DIR/install.sh"
ANSWERS_DST="/tmp/nexus-answers.yaml"        # stable path readable after the su handoff
LOG="/tmp/nexus-install-test.log"
HOME_DIR="/home/$NEXUS_USERNAME"

bold() { printf '\033[1m%s\033[0m' "$1"; }
say()  { printf '\n== %s ==\n' "$1"; }

say "Nexus unattended test install"
echo "  user=$NEXUS_USERNAME  branch=$NEXUS_BRANCH"
echo "  answers=$NEXUS_ANSWERS_SRC"
echo "  install.sh=$INSTALL_SH"
echo "  log=$LOG"

# ── Preconditions ────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]]            || { echo "ERROR: must run as root (sudo)"; exit 2; }
[[ -f "$INSTALL_SH" ]]       || { echo "ERROR: install.sh not found at $INSTALL_SH — run from a repo clone"; exit 2; }
[[ -f "$NEXUS_ANSWERS_SRC" ]]|| { echo "ERROR: answers file not found: $NEXUS_ANSWERS_SRC"; exit 2; }

# Answers file at a stable, world-readable path so the wizard (running as the nexus
# user after `su -`) can read it.
cp "$NEXUS_ANSWERS_SRC" "$ANSWERS_DST"
chmod 644 "$ANSWERS_DST"
echo "  answers staged → $ANSWERS_DST"

# ── Optional preflight ───────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/preflight_check.sh" ]]; then
    say "preflight (advisory)"
    bash "$SCRIPT_DIR/preflight_check.sh" || echo "  (preflight flagged items — install.sh installs prereqs; continuing)"
fi

# ── Run the real installer, unattended ───────────────────────────────────────
say "running install.sh (unattended)"
NEXUS_UNATTENDED=1 \
NEXUS_USERNAME="$NEXUS_USERNAME" \
NEXUS_BRANCH="$NEXUS_BRANCH" \
NEXUS_ANSWERS="$ANSWERS_DST" \
    bash "$INSTALL_SH" 2>&1 | tee "$LOG"
INSTALL_RC=${PIPESTATUS[0]}
echo "  install.sh exit code: $INSTALL_RC"

# ── Verification — the 'green' definition ────────────────────────────────────
say "verification"
PASS=0; FAIL=0
APP_PY="$HOME_DIR/nexus/.venv/bin/python"

chk() {  # description ; test-command
    if eval "$2" >/dev/null 2>&1; then printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1))
    else printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); fi
}
# poll a command up to N seconds (for services that take a moment to come up)
wait_for() {  # seconds ; test-command
    local n="$1"; shift
    for ((i=0; i<n; i++)); do eval "$1" >/dev/null 2>&1 && return 0; sleep 1; done
    return 1
}

chk "install.sh exited 0"                   "[[ $INSTALL_RC -eq 0 ]]"
chk "service user '$NEXUS_USERNAME' exists" "id $NEXUS_USERNAME"
chk "scaffold: ~/venv (tool-venv home)"     "[[ -d $HOME_DIR/venv ]]"
chk "scaffold: ~/Tools"                     "[[ -d $HOME_DIR/Tools ]]"
chk "scaffold: ~/workspace"                 "[[ -d $HOME_DIR/workspace ]]"
chk "nexus app cloned (branch $NEXUS_BRANCH)" "[[ -d $HOME_DIR/nexus/.git ]]"
chk "app venv built"                        "[[ -x $APP_PY ]]"
chk "deps: core imports (yaml/aiohttp/httpx)" "$APP_PY -c 'import yaml, aiohttp, httpx'"
chk "deps: full requirements present"       "$APP_PY -c 'import anthropic, openai, fastapi, uvicorn, telegram, trafilatura'"
chk "docker installed"                      "command -v docker"
chk "docker service active"                 "systemctl is-active --quiet docker"
chk "'$NEXUS_USERNAME' in docker group"     "id -nG $NEXUS_USERNAME | grep -qw docker"
chk "ollama installed"                      "command -v ollama"
chk "ollama endpoint up (<=30s)"            "wait_for 30 'curl -sf http://localhost:11434/api/tags'"
chk "mattermost compose written"            "[[ -f $HOME_DIR/dockers/mattermost/docker-compose.yml ]]"
chk "nexus.service unit present"            "systemctl list-unit-files | grep -q '^nexus.service'"
chk "nexus.service active (<=20s)"          "wait_for 20 'systemctl is-active --quiet nexus'"

# ── Optional smoke: ask the local LLM a question (only if the model finished pulling)
say "smoke test (best-effort — model may still be pulling)"
if curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q '"models"'; then
    if curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q 'llama3.2'; then
        echo "  asking Ollama a question..."
        curl -sf http://localhost:11434/api/generate \
            -d '{"model":"llama3.2:3b","prompt":"Reply with the single word: online","stream":false}' \
            2>/dev/null | sed -n 's/.*"response":"\([^"]*\)".*/  LLM says: \1/p' || echo "  (no response yet)"
    else
        echo "  llama3.2:3b not pulled yet — skipping question (background pull)"
    fi
else
    echo "  Ollama not reachable — skipping smoke"
fi

# ── Hardware fit — heuristic vs llmfit (the whole point of the prototype) ─────
say "hardware fit — Nexus heuristic vs llmfit"
if command -v llmfit >/dev/null 2>&1 || [[ -x /usr/local/bin/llmfit ]]; then
    echo "  llmfit installed: yes ($( (llmfit --version 2>/dev/null || /usr/local/bin/llmfit --version 2>/dev/null) | head -1))"
else
    echo "  llmfit installed: no (wizard used heuristic only)"
fi
WLOG="$HOME_DIR/Logs/install.log"
SRC_LOG="$WLOG"; [[ -f "$SRC_LOG" ]] || SRC_LOG="$LOG"
echo "  --- from $SRC_LOG ---"
grep -E "RAM:|GPU:|CPU:|Local LLM recommended|Model:|llmfit|COMPARE|top fit|heuristic pick" "$SRC_LOG" 2>/dev/null | tail -40 \
    || echo "  (no hardware-fit lines captured)"
echo "  (raw llmfit JSON is in $WLOG — grep 'llmfit system raw' / 'llmfit recommend raw')"

# ── Result ───────────────────────────────────────────────────────────────────
say "RESULT: $PASS passed, $FAIL failed"
echo "  full install log: $LOG  (+ $HOME_DIR/Logs/install.log)"
if [[ $FAIL -eq 0 ]]; then
    printf "\033[32m✅ END-TO-END INSTALL GREEN\033[0m\n"; exit 0
else
    printf "\033[31m❌ install verification failed — see the ✗ lines + %s\033[0m\n" "$LOG"; exit 1
fi
