#!/usr/bin/env bash
# Multi-LLM-Nexus — Root Installer
#
# Download and run:
#   curl -sSL https://raw.githubusercontent.com/Driftah9/multi-llm-nexus/main/install.sh -o /tmp/nexus-install.sh
#   sudo bash /tmp/nexus-install.sh
#
# This script runs as root and does ONLY:
#   1. System package check / install
#   2. Create the bot user account
#   3. Grant narrow sudo rights (systemctl for nexus only)
#   4. Write runtime config + bootstrap script into the bot user's home
#   5. exec su into the bot user — bootstrap.sh handles the rest

set -euo pipefail

REPO_URL="https://github.com/Driftah9/multi-llm-nexus.git"
BRANCH="${NEXUS_BRANCH:-main}"

# Root-phase log — brief; full install log lives at ~/Logs/install.log
ROOT_LOG="/tmp/nexus-root-$(date +%Y%m%d-%H%M%S).log"
exec 3> "$ROOT_LOG"
printf "[%s] nexus root installer started\n" "$(date +%T)" >&3
trap 'printf "[%s] ERROR: status=%s line=%s\n" "$(date +%T)" "$?" "$LINENO" >&3' ERR

# Re-attach stdin to terminal (curl-pipe safety)
if [ -e /dev/tty ] && [ -r /dev/tty ]; then exec < /dev/tty; fi


# ── Helpers ───────────────────────────────────────────────────────────────────

bold()   { printf "\033[1m%s\033[0m"  "$*"; }
green()  { printf "\033[32m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }
red()    { printf "\033[31m%s\033[0m" "$*"; }
dim()    { printf "\033[2m%s\033[0m"  "$*"; }

header() {
    echo
    echo "────────────────────────────────────────────────────────────"
    printf "  %s\n" "$(bold "$1")"
    echo "────────────────────────────────────────────────────────────"
    printf "\n[%s] ═══ STEP: %s ═══\n" "$(date +%T)" "$1" >&3
}
check() { printf "  $(green "✓") %s\n" "$*"; printf "[%s] OK:   %s\n" "$(date +%T)" "$*" >&3; }
warn()  { printf "  $(yellow "!") %s\n" "$*"; printf "[%s] WARN: %s\n" "$(date +%T)" "$*" >&3; }
fail()  { printf "  $(red "✗") %s\n" "$*"; printf "[%s] FAIL: %s\n" "$(date +%T)" "$*" >&3; }
info()  { printf "    %s\n" "$(dim "$*")"; printf "[%s] INFO: %s\n" "$(date +%T)" "$*" >&3; }

_STDIN_EXHAUSTED=0
_stdin_eof_guard() {
    printf "\n  %s\n" "$(red "No more input — stdin closed before setup finished.")"
    exit 1
}

ask() {
    local prompt="$1" default="${2:-}" answer
    [[ "$_STDIN_EXHAUSTED" == "1" ]] && _stdin_eof_guard
    printf "[%s] PROMPT: %s [default: %s]\n" "$(date +%T)" "$prompt" "${default:-none}" >&3
    if [[ -n "$default" ]]; then
        printf "\n  %s [$(dim "$default")]: " "$prompt" > /dev/tty
    else
        printf "\n  %s: " "$prompt" > /dev/tty
    fi
    if ! read -r answer; then
        _STDIN_EXHAUSTED=1
        printf "[%s] ANSWER: (EOF — using default)\n" "$(date +%T)" >&3
        [[ -n "$default" ]] && { echo "$default"; return 0; }
        _stdin_eof_guard
    fi
    printf "[%s] ANSWER: %s\n" "$(date +%T)" "${answer:-$default}" >&3
    echo "${answer:-$default}"
}

ask_yn() {
    local prompt="$1" default="${2:-y}" hint answer
    hint="Y/n"; [[ "$default" == "n" ]] && hint="y/N"
    [[ "$_STDIN_EXHAUSTED" == "1" ]] && _stdin_eof_guard
    printf "[%s] PROMPT_YN: %s [default: %s]\n" "$(date +%T)" "$prompt" "$default" >&3
    printf "\n  %s (%s): " "$prompt" "$hint" > /dev/tty
    if ! read -r answer; then _STDIN_EXHAUSTED=1; answer="$default"; fi
    answer="${answer:-$default}"
    printf "[%s] ANSWER_YN: %s\n" "$(date +%T)" "$answer" >&3
    [[ "${answer,,}" == "y"* ]]
}


# ── Root check ────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo
    printf "  %s\n" "$(red "This installer requires root privileges.")"
    echo "  Run: sudo bash /tmp/nexus-install.sh"
    echo
    exit 1
fi

echo
echo "$(bold "  Multi-LLM-Nexus Installer")"
echo "  $(dim "Your AI platform. Your rules.")"
echo "  $(dim "Root log: $ROOT_LOG")"
echo


# ── 1. System packages ────────────────────────────────────────────────────────

header "System Check"

PYTHON_BIN=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        major="${ver%.*}" minor="${ver#*.}"
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON_BIN="$cmd"
            check "Python $ver"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    warn "Python 3.11+ not found — installing..."
    apt-get update -qq 2>&3
    apt-get install -y -qq python3.11 python3.11-venv python3-pip 2>&3
    PYTHON_BIN="python3.11"
    check "Python 3.11 installed"
fi

# On Ubuntu, python3.11 ships without python3.11-venv
if ! "$PYTHON_BIN" -m venv --help &>/dev/null 2>&1; then
    VENV_PKG="${PYTHON_BIN##*/}-venv"
    warn "venv module missing — installing $VENV_PKG..."
    apt-get update -qq 2>&3
    apt-get install -y -qq "$VENV_PKG" 2>&3
    check "$VENV_PKG installed"
fi

if command -v git &>/dev/null; then
    check "git $(git --version | awk '{print $3}')"
else
    warn "git not found — installing..."
    apt-get update -qq 2>&3 && apt-get install -y -qq git 2>&3
    check "git installed"
fi

if command -v curl &>/dev/null; then
    check "curl present"
else
    apt-get install -y -qq curl 2>&3
    check "curl installed"
fi

if command -v whiptail &>/dev/null; then
    check "whiptail present (interactive menus)"
else
    warn "whiptail not found — installing..."
    apt-get install -y -qq whiptail 2>&3
    check "whiptail installed"
fi


# ── 2. Bot user creation ──────────────────────────────────────────────────────

header "System Service Account"

echo "  Nexus runs as a dedicated Linux system user account."
echo "  (This is the system-level account name, separate from your orchestrator's name.)"
echo "  Choose a name for this account."
echo "  $(dim "Examples: nexus-bot, system-ai, orchestrator")"

GENERATED_PASSWORD=""

while true; do
    USERNAME=$(ask "System account username")

    if [[ -z "$USERNAME" || "$USERNAME" == "root" ]]; then
        fail "Please choose a username."
        continue
    fi

    if [[ ! "$USERNAME" =~ ^[[:lower:]_][[:lower:][:digit:]_-]*$ ]]; then
        fail "Invalid — use lowercase letters, numbers, hyphens, underscores. No spaces."
        continue
    fi

    if id "$USERNAME" &>/dev/null; then
        warn "User '$USERNAME' already exists on this system."
        if ask_yn "Use the existing user and continue?"; then
            # Existing user may have a missing/corrupted home dir (e.g. a prior
            # 'userdel -r' that failed while a process held the account, leaving
            # the passwd entry but no /home). Repair it before any config write.
            USER_HOME=$(getent passwd "$USERNAME" | cut -d: -f6)
            USER_HOME="${USER_HOME:-/home/$USERNAME}"
            if [[ ! -d "$USER_HOME" ]]; then
                warn "Home directory $USER_HOME is missing — recreating it."
                mkdir -p "$USER_HOME"
                cp -a /etc/skel/. "$USER_HOME/" 2>/dev/null || true
                chown -R "$USERNAME:$USERNAME" "$USER_HOME"
                chmod 755 "$USER_HOME"
                check "Home directory restored: $USER_HOME"
            fi
            break
        fi
        continue
    fi

    if command -v openssl &>/dev/null; then
        GENERATED_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' | head -c 22)
    else
        GENERATED_PASSWORD=$(tr -dc 'A-Za-z0-9!@#%^&*' < /dev/urandom 2>/dev/null | head -c 22)
    fi

    useradd -m -s /bin/bash "$USERNAME"
    echo "$USERNAME:$GENERATED_PASSWORD" | chpasswd
    check "User '$USERNAME' created — home: /home/$USERNAME"
    break
done


# ── 3. Sudo permissions ───────────────────────────────────────────────────────

header "Permissions"

# Bot user gets NOPASSWD sudo for nexus service management only.
# Root sets this up — the bot user never manages its own permissions.
SUDOERS_FILE="/etc/sudoers.d/nexus-${USERNAME}"
cat > "$SUDOERS_FILE" << SUDOERS
# Nexus bot user — service management only, no password required
${USERNAME} ALL=(root) NOPASSWD: \
    /bin/systemctl daemon-reload, \
    /bin/systemctl enable nexus, \
    /bin/systemctl disable nexus, \
    /bin/systemctl start nexus, \
    /bin/systemctl stop nexus, \
    /bin/systemctl restart nexus, \
    /bin/systemctl status nexus, \
    /bin/cp /home/${USERNAME}/nexus/nexus.service /etc/systemd/system/nexus.service
SUDOERS
chmod 440 "$SUDOERS_FILE"
check "Sudoers: $USERNAME can manage nexus.service (no password)"


# ── 4. Write runtime config for bootstrap ────────────────────────────────────

header "Preparing Handoff"

# Runtime values the bot-user bootstrap needs but can't detect itself
cat > "/home/$USERNAME/.nexus-install-config" << CONFIG
# Auto-generated by install.sh — do not edit
NEXUS_PYTHON_BIN="$PYTHON_BIN"
NEXUS_REPO_URL="$REPO_URL"
NEXUS_BRANCH="$BRANCH"
NEXUS_ROOT_LOG="$ROOT_LOG"
CONFIG
chown "$USERNAME:$USERNAME" "/home/$USERNAME/.nexus-install-config"

# Write bootstrap.sh — runs as bot user, handles the full install
# Single-quoted heredoc: no variable expansion here; bootstrap reads
# runtime values from .nexus-install-config at startup.
cat > "/home/$USERNAME/.nexus-bootstrap.sh" << 'BOOTSTRAP_EOF'
#!/usr/bin/env bash
# Nexus bootstrap — runs as the bot user, handles full install
# Called automatically by install.sh via: exec su - $USERNAME -c "bash ~/.nexus-bootstrap.sh"
# Can also be re-run manually by the bot user for reconfiguration.

set -euo pipefail

# ── Runtime config from root phase ────────────────────────────────────────────
source ~/.nexus-install-config
# Provides: NEXUS_PYTHON_BIN, NEXUS_REPO_URL, NEXUS_BRANCH, NEXUS_ROOT_LOG

# ── Log setup — everything from here goes to ~/Logs/install.log ───────────────
mkdir -p ~/Logs
LOG_FILE=~/Logs/install.log
exec 3>> "$LOG_FILE"
printf "\n[%s] === nexus bootstrap started (user: %s) ===\n" "$(date +%T)" "$(whoami)" >&3
printf "[%s] root log: %s\n" "$(date +%T)" "$NEXUS_ROOT_LOG" >&3
trap 'printf "[%s] ERROR: status=%s line=%s\n" "$(date +%T)" "$?" "$LINENO" >&3' ERR


# ── Helpers ───────────────────────────────────────────────────────────────────

bold()   { printf "\033[1m%s\033[0m"  "$*"; }
green()  { printf "\033[32m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }
red()    { printf "\033[31m%s\033[0m" "$*"; }
dim()    { printf "\033[2m%s\033[0m"  "$*"; }
cyan()   { printf "\033[36m%s\033[0m" "$*"; }

header() {
    echo
    echo "────────────────────────────────────────────────────────────"
    printf "  %s\n" "$(bold "$1")"
    echo "────────────────────────────────────────────────────────────"
    printf "\n[%s] ═══ STEP: %s ═══\n" "$(date +%T)" "$1" >&3
}
check() { printf "  $(green "✓") %s\n" "$*"; printf "[%s] OK:   %s\n" "$(date +%T)" "$*" >&3; }
warn()  { printf "  $(yellow "!") %s\n" "$*"; printf "[%s] WARN: %s\n" "$(date +%T)" "$*" >&3; }
fail()  { printf "  $(red "✗") %s\n" "$*"; printf "[%s] FAIL: %s\n" "$(date +%T)" "$*" >&3; }
info()  { printf "    %s\n" "$(dim "$*")"; printf "[%s] INFO: %s\n" "$(date +%T)" "$*" >&3; }


# ── 1. Clone repository ───────────────────────────────────────────────────────

header "Cloning Nexus"

INSTALL_DIR=~/nexus
WORKSPACE_DIR=~/workspace

if [[ -d "$INSTALL_DIR/.git" ]]; then
    warn "Nexus already exists at $INSTALL_DIR — pulling latest."
    git -C "$INSTALL_DIR" pull --ff-only 2>&3
    check "Repository updated"
else
    info "Cloning into $INSTALL_DIR ..."
    git clone --branch "$NEXUS_BRANCH" --depth 1 "$NEXUS_REPO_URL" "$INSTALL_DIR" 2>&3
    check "Repository cloned"
fi

# Inject fixed wizard.py from /tmp if available (bypasses GitHub CDN cache)
if [[ -f /tmp/wizard_fixed.py ]]; then
    cp /tmp/wizard_fixed.py "$INSTALL_DIR/src/setup/wizard.py" 2>&3
    info "Injected wizard.py from /tmp"
fi


# ── 2. Scaffold system root ───────────────────────────────────────────────────

header "Scaffolding System Root"

ROOT_FOLDERS=(
    Inbox Logs Scripts backups src tests Data skills
    Config dockers adapters Agents Temp research_cache Tools workspace
)
for folder in "${ROOT_FOLDERS[@]}"; do
    mkdir -p ~/"$folder"
    info "~/$folder/"
done
check "System root folders created (${#ROOT_FOLDERS[@]} canonical folders)"


# ── 3. Identity templates ─────────────────────────────────────────────────────

SYS_TEMPLATES="$INSTALL_DIR/templates/system"
INSTALL_DATE=$(date -u +%Y-%m-%d)
HOSTNAME_VAL=$(hostname -f 2>/dev/null || hostname)
USERNAME_VAL=$(whoami)

for tmpl in SOUL.md OPERATING_PROCEDURES.md AI_CONTEXT.md; do
    if [[ -f "$SYS_TEMPLATES/$tmpl" ]]; then
        dest=~/"$tmpl"
        cp "$SYS_TEMPLATES/$tmpl" "$dest"
        sed -i "s/\[USERNAME\]/$USERNAME_VAL/g"     "$dest"
        sed -i "s/\[INSTALL_DATE\]/$INSTALL_DATE/g" "$dest"
        sed -i "s|\[HOSTNAME\]|$HOSTNAME_VAL|g"     "$dest"
        info "✓ $tmpl"
    fi
done
check "System identity templates installed"

cat > "$WORKSPACE_DIR/.workspace.config" << WSCFG
# Nexus Workspace Configuration — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)
# workspace/ has no pre-declared categories — they grow from use.
# Create a project:  @bot create project [category]/[name]
project_template: "$INSTALL_DIR/templates/project-template"
WSCFG
check "Workspace initialized"


# ── 4. Python environment ─────────────────────────────────────────────────────

header "Python Environment"

VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    "$NEXUS_PYTHON_BIN" -m venv "$VENV_DIR" 2>&3
fi
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip 2>&3
pip install --quiet pyyaml httpx python-dotenv aiohttp 2>&3
check "Python environment ready  ($NEXUS_PYTHON_BIN)"


# ── 5. Config defaults ────────────────────────────────────────────────────────

[[ ! -f "$INSTALL_DIR/config/providers.yaml" ]] && \
    cp "$INSTALL_DIR/config/providers.yaml.example" "$INSTALL_DIR/config/providers.yaml"
[[ ! -f "$INSTALL_DIR/config/adapters.yaml" ]] && \
    cp "$INSTALL_DIR/config/adapters.yaml.example"  "$INSTALL_DIR/config/adapters.yaml"
[[ ! -f "$INSTALL_DIR/.env" ]] && \
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"


# ── 6. Interactive setup wizard ───────────────────────────────────────────────

header "Setup Wizard"
echo "  Configure providers, auth, and platform adapters."
echo "  Log: ~/Logs/install.log"
echo

cd "$INSTALL_DIR"
NEXUS_LOG_FILE="$LOG_FILE" python -m src.setup.wizard


# ── 7. Provider shims ─────────────────────────────────────────────────────────

header "Provider Shims"

PROVIDERS_YAML="$INSTALL_DIR/config/providers.yaml"
SHIM_COUNT=0

if [[ -f "$PROVIDERS_YAML" ]]; then
    while IFS= read -r provider; do
        [[ -z "$provider" ]] && continue
        case "$provider" in
            anthropic|claude*)  shim="CLAUDE.md" ;;
            openai|gpt*)        shim="OPENAI.md" ;;
            gemini|google*)     shim="GEMINI.md" ;;
            groq*)              shim="GROQ.md" ;;
            mistral*)           shim="MISTRAL.md" ;;
            ollama*)            shim="OLLAMA.md" ;;
            cohere*)            shim="COHERE.md" ;;
            *)                  shim="${provider^^}.md" ;;
        esac
        echo "@AI_CONTEXT.md" > ~/"$shim"
        check "Shim: $shim → AI_CONTEXT.md  ($provider)"
        SHIM_COUNT=$((SHIM_COUNT + 1))
    done < <("$NEXUS_PYTHON_BIN" -c "
import yaml, sys
try:
    d = yaml.safe_load(open('$PROVIDERS_YAML')) or {}
    [print(k) for k in d.get('providers', {})]
except Exception:
    pass
" 2>/dev/null)
fi

if [[ "$SHIM_COUNT" -eq 0 ]]; then
    warn "No provider shims generated — re-run wizard after adding credentials."
    info "cd ~/nexus && source .venv/bin/activate && python -m src.setup.wizard"
fi


# ── 8. Generate and install systemd service ───────────────────────────────────

header "System Service"

cd "$INSTALL_DIR"
python -m src.setup.systemd 2>&3 || warn "systemd.py could not generate service file"

SERVICE_SRC="$INSTALL_DIR/nexus.service"
if [[ -f "$SERVICE_SRC" ]]; then
    sudo cp "$SERVICE_SRC" /etc/systemd/system/nexus.service
    sudo systemctl daemon-reload
    sudo systemctl enable nexus
    check "nexus.service installed and enabled (starts on boot)"
    sudo systemctl start nexus
    sleep 2
    if sudo systemctl is-active --quiet nexus 2>/dev/null; then
        check "Nexus is running"
    else
        warn "Service may need a moment. Check: journalctl -u nexus -n 30"
    fi
else
    warn "Service file not found — start manually:"
    info "cd ~/nexus && source .venv/bin/activate && python -m src.main"
fi


# ── 9. Done ───────────────────────────────────────────────────────────────────

printf "[%s] === bootstrap complete ===\n" "$(date +%T)" >&3

header "Nexus Ready"

echo
echo "  $(bold "You are logged in as:") $(whoami)"
echo "  $(bold "Home:")        ~/"
echo "  $(bold "Nexus:")       ~/nexus"
echo "  $(bold "Workspace:")   ~/workspace"
echo "  $(bold "Logs:")        ~/Logs/install.log"
echo "  $(bold "Config:")      ~/nexus/config/"
echo "  $(bold "Env file:")    ~/nexus/.env"
echo
echo "  $(bold "Service management:")"
info "sudo systemctl status nexus"
info "sudo systemctl restart nexus"
info "journalctl -u nexus -f"
echo
echo "  $(bold "Add or change providers:")"
info "cd ~/nexus && source .venv/bin/activate && python -m src.setup.wizard"
echo
BOOTSTRAP_EOF

chown "$USERNAME:$USERNAME" "/home/$USERNAME/.nexus-bootstrap.sh"
chmod +x "/home/$USERNAME/.nexus-bootstrap.sh"
check "Bootstrap script ready"

# Add a .bashrc hook to auto-run bootstrap on first login
# This ensures bootstrap runs in an interactive shell with proper TTY for whiptail
cat >> "/home/$USERNAME/.bashrc" << 'BASHRC_HOOK'

# Nexus bootstrap auto-run on first login
if [[ -f ~/.nexus-bootstrap.sh ]] && [[ ! -f ~/.nexus-bootstrap-done ]]; then
    bash ~/.nexus-bootstrap.sh
    touch ~/.nexus-bootstrap-done
fi
BASHRC_HOOK
chown "$USERNAME:$USERNAME" "/home/$USERNAME/.bashrc"

printf "[%s] root phase complete — handoff to %s\n" "$(date +%T)" "$USERNAME" >&3


# ── 5. Root summary + handoff ─────────────────────────────────────────────────

header "Root Phase Complete"

echo "  Bot user  : $USERNAME"
echo "  Sudo rule : service management only (no password)"
echo "  Root log  : $ROOT_LOG"

if [[ -n "$GENERATED_PASSWORD" ]]; then
    echo
    printf "  %s\n" "$(bold "$(yellow "Generated password — store this now:")")"
    printf "    %s\n" "$(bold "$GENERATED_PASSWORD")"
    echo
    read -p "  $(dim "Press Enter to continue")" < /dev/tty
fi

echo
echo "  $(dim "Switching to '$USERNAME' — bootstrap will handle the rest.")"
echo

# Use script to allocate a proper TTY for interactive shell + bootstrap
# This ensures full job control and whiptail menu support
exec script -q -c "su - $USERNAME" /dev/null
