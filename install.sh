#!/usr/bin/env bash
# Multi-LLM-Nexus Bootstrap Installer
#
# Recommended usage (keeps stdin for interactive prompts):
#   sudo bash <(curl -sSL https://raw.githubusercontent.com/Driftah9/multi-llm-nexus/main/install.sh)
#
# Or download first:
#   curl -sSL https://raw.githubusercontent.com/Driftah9/multi-llm-nexus/main/install.sh -o install.sh
#   sudo bash install.sh

set -euo pipefail

# Timestamped install log — all steps, prompts, answers, and command output.
# Tell Claude "I'm on step X" and it reads this file to see exactly what happened.
LOG_FILE="/tmp/nexus-install-$(date +%Y%m%d-%H%M%S).log"
exec 3> "$LOG_FILE"
printf "[%s] nexus installer started (log: %s)\n" "$(date +%T)" "$LOG_FILE" >&3
trap 'printf "[%s] ERROR: exited status=%s line=%s\n" "$(date +%T)" "$?" "$LINENO" >&3' ERR

# Re-open stdin from terminal in case we were piped through curl | bash.
# Only do this when a real controlling terminal exists — in headless/piped/CI
# contexts /dev/tty is absent, and an unconditional redirect there aborts the
# whole script (set -e) before the wizard ever runs.
if [ -e /dev/tty ] && [ -r /dev/tty ]; then
    exec < /dev/tty
fi

REPO_URL="https://github.com/Driftah9/multi-llm-nexus.git"
BRANCH="${NEXUS_BRANCH:-main}"


# ── Color helpers ─────────────────────────────────────────────────────────────

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

check() { printf "  $(green "✓") %s\n" "$*"; printf "[%s] OK:   %s\n"   "$(date +%T)" "$*" >&3; }
warn()  { printf "  $(yellow "!") %s\n" "$*"; printf "[%s] WARN: %s\n"  "$(date +%T)" "$*" >&3; }
fail()  { printf "  $(red "✗") %s\n" "$*";   printf "[%s] FAIL: %s\n"   "$(date +%T)" "$*" >&3; }
info()  { printf "    %s\n" "$(dim "$*")";    printf "[%s] INFO: %s\n"   "$(date +%T)" "$*" >&3; }

# EOF latch. Once stdin is exhausted, any further prompt that would loop on
# validation (`continue`) must abort instead of replaying a default forever —
# the infinite "Invalid" wall seen on headless/piped runs. A default that is
# itself a rejected sentinel (e.g. the placeholder username) would otherwise
# loop indefinitely, so a second post-EOF prompt is always fatal.
_STDIN_EXHAUSTED=0

_stdin_eof_guard() {
    printf "\n  %s\n" "$(red "No more input — stdin closed before the wizard finished.")"
    info "Run the installer interactively, or pipe a complete answers file."
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
        # read failed → EOF. Latch it: use any default this one time, but the
        # next prompt aborts rather than handing back the same value in a loop.
        _STDIN_EXHAUSTED=1
        printf "[%s] ANSWER: (EOF — using default: %s)\n" "$(date +%T)" "${default:-none}" >&3
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
    if ! read -r answer; then
        _STDIN_EXHAUSTED=1
        answer="$default"
        printf "[%s] ANSWER_YN: (EOF — using default: %s)\n" "$(date +%T)" "$default" >&3
    fi
    answer="${answer:-$default}"
    local resolved; [[ "${answer,,}" == "y"* ]] && resolved="YES" || resolved="NO"
    printf "[%s] ANSWER_YN: %s → %s\n" "$(date +%T)" "$answer" "$resolved" >&3
    [[ "${answer,,}" == "y"* ]]
}


# ── Root check ────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo
    printf "  %s\n" "$(red "This installer requires root privileges.")"
    echo
    echo "  Run:"
    echo "    sudo bash <(curl -sSL https://raw.githubusercontent.com/Driftah9/multi-llm-nexus/main/install.sh)"
    echo
    exit 1
fi


# ── Banner ────────────────────────────────────────────────────────────────────

echo
echo "$(bold "  Multi-LLM-Nexus Installer")"
echo "  $(dim "Your AI platform. Your rules.")"
echo "  $(dim "Install log: $LOG_FILE")"
echo


# ── 1. System dependencies ────────────────────────────────────────────────────

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


# ── 2. System user creation ───────────────────────────────────────────────────

header "Nexus System User"

echo "  Nexus will run as a dedicated system user (no sudo privileges)."
echo "  Choose a username that represents your bot or assistant identity."
echo "  $(dim "Example: chamberlain, nexus-bot, myassistant")"

GENERATED_PASSWORD=""

while true; do
    USERNAME=$(ask "Username for Nexus to run as" "nexus-user-name")

    if [[ "$USERNAME" == "root" || "$USERNAME" == "nexus-user-name" ]]; then
        fail "Please choose your own username."
        continue
    fi

    if [[ ! "$USERNAME" =~ ^[[:lower:]_][[:lower:][:digit:]_-]*$ ]]; then
        fail "Invalid — use lowercase letters, numbers, hyphens, underscores. No spaces."
        continue
    fi

    if id "$USERNAME" &>/dev/null; then
        warn "User '$USERNAME' already exists on this system."
        if ask_yn "Use the existing user and continue?"; then
            break
        fi
        continue
    fi

    # Generate password
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

INSTALL_DIR="/home/$USERNAME/nexus"
WORKSPACE_DIR="/home/$USERNAME/workspace"


# ── 3. Clone repository ───────────────────────────────────────────────────────

header "Cloning Repository"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    warn "Nexus already exists at $INSTALL_DIR — pulling latest."
    sudo -u "$USERNAME" git -C "$INSTALL_DIR" pull --ff-only
    check "Repository updated"
else
    info "Cloning into $INSTALL_DIR ..."
    sudo -u "$USERNAME" git clone --branch "$BRANCH" --depth 1 \
        "$REPO_URL" "$INSTALL_DIR" 2>&3
    check "Repository cloned to $INSTALL_DIR"
fi


# ── 3.5. Scaffold system root structure ───────────────────────────────────────

header "Scaffolding System Root"

# 15 canonical folders — everything the system needs, nothing it doesn't.
# Files and folders must live here, not at bare home root.
# Temp/ is for temporary work (screenshots, drafts, fetches) — auto-cleanup.
# Agents/ starts empty — agents emerge from observed usage patterns over time.
ROOT_FOLDERS=(
    "Inbox"
    "Logs"
    "Scripts"
    "backups"
    "src"
    "tests"
    "Data"
    "skills"
    "Config"
    "dockers"
    "adapters"
    "Agents"
    "Temp"
    "research_cache"
    "Tools"
    "workspace"
)

for folder in "${ROOT_FOLDERS[@]}"; do
    mkdir -p "/home/$USERNAME/$folder"
    chown "$USERNAME:$USERNAME" "/home/$USERNAME/$folder"
    info "/home/$USERNAME/$folder/"
done
check "System root folders created (${#ROOT_FOLDERS[@]} canonical folders)"

# Copy system-level identity templates
SYS_TEMPLATES="$INSTALL_DIR/templates/system"
INSTALL_DATE=$(date -u +%Y-%m-%d)
HOSTNAME_VAL=$(hostname -f 2>/dev/null || hostname)

for tmpl in SOUL.md OPERATING_PROCEDURES.md AI_CONTEXT.md; do
    if [[ -f "$SYS_TEMPLATES/$tmpl" ]]; then
        dest="/home/$USERNAME/$tmpl"
        cp "$SYS_TEMPLATES/$tmpl" "$dest"
        # Substitute known values at install time
        sed -i "s/\[USERNAME\]/$USERNAME/g"                  "$dest"
        sed -i "s/\[INSTALL_DATE\]/$INSTALL_DATE/g"          "$dest"
        sed -i "s|\[HOSTNAME\]|$HOSTNAME_VAL|g"              "$dest"
        chown "$USERNAME:$USERNAME" "$dest"
        info "  ✓ $tmpl"
    fi
done
check "System identity templates installed"

# workspace.config — no pre-declared categories; they grow from use
cat > "$WORKSPACE_DIR/.workspace.config" <<EOF
# Nexus Workspace Configuration
# Generated by installer on $(date -u +%Y-%m-%dT%H:%M:%SZ)
#
# workspace/ has no pre-declared categories.
# Categories and projects are created as work requires.
#
# Create a project:
#   @bot create project [category]/[projectname]
#
# Link a project to a channel:
#   @bot link [category]/[projectname] to [adapter]:[channel]

project_template: "$INSTALL_DIR/templates/project-template"
EOF
chown "$USERNAME:$USERNAME" "$WORKSPACE_DIR/.workspace.config"
check "Workspace initialized (categories discovered through use)"


# ── 4. Interactive setup wizard ───────────────────────────────────────────────

header "Starting Nexus Setup Wizard"

echo "  The wizard will guide you through:"
echo "    • Hardware detection + local model suggestion"
echo "    • AI provider selection (subscription or API key)"
echo "    • Provider connection test — must pass before continuing"
echo "    • Additional providers (now or later)"
echo "    • Use-case selection"
echo "    • Communication channel setup (Mattermost, Discord, Telegram)"
echo

# Pass install context to wizard
export NEXUS_INSTALL_USER="$USERNAME"
export NEXUS_WORKSPACE_DIR="$WORKSPACE_DIR"
export NEXUS_SYSTEM_ROOT="/home/$USERNAME"

# setup.sh will run as $USERNAME and needs to write to the log file
chmod 666 "$LOG_FILE"

sudo -u "$USERNAME" \
    NEXUS_INSTALL_USER="$USERNAME" \
    NEXUS_WORKSPACE_DIR="$WORKSPACE_DIR" \
    NEXUS_SYSTEM_ROOT="/home/$USERNAME" \
    NEXUS_LOG_FILE="$LOG_FILE" \
    bash "$INSTALL_DIR/setup.sh"

# ── 4.5. Generate provider shims at system root ───────────────────────────────

header "Provider Shims"

# Read configured providers from providers.yaml, generate a shim at system root
# for each one. Shims point to AI_CONTEXT.md — the single source of truth.
# All projects inherit the same shim set via project scaffold.

PROVIDERS_YAML="$INSTALL_DIR/config/providers.yaml"
SHIM_GENERATED=0

if [[ -f "$PROVIDERS_YAML" ]]; then
    # Extract provider names from yaml (keys under 'providers:')
    PROVIDER_NAMES=$(python3 -c "
import yaml, sys
try:
    with open('$PROVIDERS_YAML') as f:
        cfg = yaml.safe_load(f) or {}
    providers = cfg.get('providers', {})
    for name in providers:
        print(name)
except Exception as e:
    sys.exit(0)
" 2>/dev/null || true)

    while IFS= read -r provider; do
        [[ -z "$provider" ]] && continue
        # Map provider key to shim filename
        case "$provider" in
            anthropic|claude*) shim_file="CLAUDE.md"; shim_content="@AI_CONTEXT.md" ;;
            openai|gpt*)       shim_file="OPENAI.md";
                               shim_content="# Project context is in AI_CONTEXT.md — read that file for identity, layout, routing, and agent roles.
# All working files are under work/. Do not create files at the project root." ;;
            gemini|google*)    shim_file="GEMINI.md";
                               shim_content="# Project context is in AI_CONTEXT.md — read that file for identity, layout, routing, and agent roles.
# All working files are under work/. Do not create files at the project root." ;;
            groq*)             shim_file="GROQ.md";
                               shim_content="# Project context is in AI_CONTEXT.md — read that file for identity, layout, routing, and agent roles.
# All working files are under work/. Do not create files at the project root." ;;
            mistral*)          shim_file="MISTRAL.md";
                               shim_content="# Project context is in AI_CONTEXT.md — read that file for identity, layout, routing, and agent roles.
# All working files are under work/. Do not create files at the project root." ;;
            ollama*)           shim_file="OLLAMA.md";
                               shim_content="# Project context is in AI_CONTEXT.md — read that file for identity, layout, routing, and agent roles.
# All working files are under work/. Do not create files at the project root." ;;
            *)                 shim_file="${provider^^}.md";
                               shim_content="# Project context is in AI_CONTEXT.md — read that file for identity, layout, routing, and agent roles.
# All working files are under work/. Do not create files at the project root." ;;
        esac

        shim_path="/home/$USERNAME/$shim_file"
        echo "$shim_content" > "$shim_path"
        chown "$USERNAME:$USERNAME" "$shim_path"
        check "Shim: $shim_file → AI_CONTEXT.md  ($provider)"
        info "shim $shim_file generated for provider $provider"
        SHIM_GENERATED=$((SHIM_GENERATED + 1))
    done <<< "$PROVIDER_NAMES"
fi

if [[ "$SHIM_GENERATED" -eq 0 ]]; then
    warn "No providers found in config — generate shims manually after configuring providers:"
    info "cp $INSTALL_DIR/templates/system/shims/CLAUDE.md /home/$USERNAME/"
    info "no provider shims generated — providers.yaml missing or empty"
fi


# ── 5. Install and enable systemd service ────────────────────────────────────

SERVICE_FILE="$INSTALL_DIR/nexus.service"
if [[ -f "$SERVICE_FILE" ]]; then
    header "System Service"
    cp "$SERVICE_FILE" /etc/systemd/system/nexus.service
    systemctl daemon-reload
    systemctl enable nexus
    check "nexus.service installed and enabled (starts on boot)"

    if ask_yn "Start Nexus now?"; then
        systemctl start nexus
        sleep 2
        if systemctl is-active --quiet nexus; then
            check "Nexus is running"
        else
            warn "Service started but may need a moment — check logs:"
            info "journalctl -u nexus -n 50"
        fi
    fi
else
    warn "Service file not found — start manually after setup:"
    info "cd $INSTALL_DIR && source .venv/bin/activate && python -m src.main"
fi


# ── 6. Summary ────────────────────────────────────────────────────────────────

header "Installation Complete"

echo "  $(bold "System user:")"
echo "    Username : $USERNAME"
echo "    Home     : /home/$USERNAME"
echo "    Nexus    : $INSTALL_DIR"

if [[ -n "$GENERATED_PASSWORD" ]]; then
    echo
    printf "  %s\n" "$(bold "$(yellow "Generated password — store this now:")")"
    printf "    %s\n" "$(bold "$GENERATED_PASSWORD")"
    echo
    echo "  $(dim "To SSH into the Nexus user:")"
    info "ssh $USERNAME@<this-host>"
    echo "  $(dim "Or switch from your admin account:")"
    info "sudo su - $USERNAME"
fi

echo
echo "  $(bold "Service management:")"
info "systemctl status nexus"
info "journalctl -u nexus -f"
info "systemctl restart nexus"

echo
echo "  $(bold "System root:")"
info "/home/$USERNAME/SOUL.md                 ← system identity"
info "/home/$USERNAME/OPERATING_PROCEDURES.md ← system behavioral rules"
info "/home/$USERNAME/AI_CONTEXT.md           ← system context"
info "/home/$USERNAME/Agents/                 ← dynamic agents (starts empty)"
info "/home/$USERNAME/workspace/              ← projects and categories"

echo
echo "  $(bold "Configuration:")"
info "$INSTALL_DIR/config/   — providers, adapters, specialists"
info "$INSTALL_DIR/.env      — API keys and secrets"
info "$WORKSPACE_DIR/.workspace.config  — workspace settings"

echo
echo "  $(bold "Project commands (via Mattermost/Discord/Telegram):")"
info "@bot create project [category]/[projectname]"
info "@bot link [category]/[projectname] to [adapter]:[channel]"

echo
echo "  $(dim "To add providers or reconfigure later:")"
info "sudo su - $USERNAME"
info "cd nexus && source .venv/bin/activate && python -m src.setup.wizard"

echo
echo "  $(bold "Install log (share with Claude for debugging):")"
info "$LOG_FILE"
echo

printf "[%s] ═══ INSTALL COMPLETE ═══\n" "$(date +%T)" >&3
