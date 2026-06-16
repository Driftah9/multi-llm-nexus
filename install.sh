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

# Re-open stdin from terminal in case we were piped through curl | bash
exec < /dev/tty

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
}

check() { printf "  $(green "✓") %s\n" "$*"; }
warn()  { printf "  $(yellow "!") %s\n" "$*"; }
fail()  { printf "  $(red "✗") %s\n" "$*"; }
info()  { printf "    %s\n" "$(dim "$*")"; }

ask() {
    local prompt="$1" default="${2:-}" answer
    if [[ -n "$default" ]]; then
        printf "\n  %s [$(dim "$default")]: " "$prompt"
    else
        printf "\n  %s: " "$prompt"
    fi
    read -r answer
    echo "${answer:-$default}"
}

ask_yn() {
    local prompt="$1" default="${2:-y}" hint answer
    hint="Y/n"; [[ "$default" == "n" ]] && hint="y/N"
    printf "\n  %s (%s): " "$prompt" "$hint"
    read -r answer
    answer="${answer:-$default}"
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
    apt-get update -qq
    apt-get install -y -qq python3.11 python3.11-venv python3-pip
    PYTHON_BIN="python3.11"
    check "Python 3.11 installed"
fi

if command -v git &>/dev/null; then
    check "git $(git --version | awk '{print $3}')"
else
    warn "git not found — installing..."
    apt-get update -qq && apt-get install -y -qq git
    check "git installed"
fi

if command -v curl &>/dev/null; then
    check "curl present"
else
    apt-get install -y -qq curl
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

    if [[ ! "$USERNAME" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
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


# ── 2.5. Workspace use-case detection ──────────────────────────────────────────

header "Workspace Categories"

echo "  Your workspace will organize projects by category."
echo "  Select the types of work you do — categories will be created for each."
echo

CATEGORIES=()

ask_yn "Hardware projects (ESP32, Arduino, IoT, embedded systems)?" && CATEGORIES+=("hardware")
ask_yn "Software development (backend, CLI, libraries, tools)?" && CATEGORIES+=("dev")
ask_yn "Home automation / homelab / infrastructure?" && CATEGORIES+=("homelab")
ask_yn "Research, learning, experiments, PoCs?" && CATEGORIES+=("research")
ask_yn "Business / client work (LLC, consulting, services)?" && CATEGORIES+=("business")
ask_yn "Operations, deployment, DevOps, monitoring?" && CATEGORIES+=("operations")

if [[ ${#CATEGORIES[@]} -eq 0 ]]; then
    info "No categories selected — defaulting to: dev"
    CATEGORIES=("dev")
fi

CATEGORY_LIST=$(IFS=", "; echo "${CATEGORIES[*]}")
check "Workspace categories: $CATEGORY_LIST"


# ── 3. Clone repository ───────────────────────────────────────────────────────

header "Cloning Repository"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    warn "Nexus already exists at $INSTALL_DIR — pulling latest."
    sudo -u "$USERNAME" git -C "$INSTALL_DIR" pull --ff-only
    check "Repository updated"
else
    info "Cloning into $INSTALL_DIR ..."
    sudo -u "$USERNAME" git clone --branch "$BRANCH" --depth 1 \
        "$REPO_URL" "$INSTALL_DIR"
    check "Repository cloned to $INSTALL_DIR"
fi


# ── 3.5. Scaffold workspace directory structure ────────────────────────────────

header "Scaffolding Workspace"

info "Creating workspace at $WORKSPACE_DIR"
mkdir -p "$WORKSPACE_DIR"
chown "$USERNAME:$USERNAME" "$WORKSPACE_DIR"

for category in "${CATEGORIES[@]}"; do
    cat_dir="$WORKSPACE_DIR/$category"
    mkdir -p "$cat_dir"
    chown "$USERNAME:$USERNAME" "$cat_dir"
    info "  ✓ $category/"
done

# Create .workspace.config to track categories
cat > "$WORKSPACE_DIR/.workspace.config" <<EOF
# Nexus Workspace Configuration
# Generated by installer on $(date -u +%Y-%m-%dT%H:%M:%SZ)

categories:
$(printf '  - %s\n' "${CATEGORIES[@]}")

project_template: "$INSTALL_DIR/templates/project-template"

# To create a new project:
#   @bot create project [category]/[projectname]
#
# To link a project to an adapter channel:
#   @bot link [category]/[projectname] to [adapter]:[channel]
EOF

chown "$USERNAME:$USERNAME" "$WORKSPACE_DIR/.workspace.config"
check "Workspace scaffold complete"


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

# Pass install user and workspace config so wizard can use context
export NEXUS_INSTALL_USER="$USERNAME"
export NEXUS_WORKSPACE_DIR="$WORKSPACE_DIR"
CATEGORIES_JSON=$(printf '"%s",' "${CATEGORIES[@]}" | sed 's/,$//')
export NEXUS_WORKSPACE_CATEGORIES="[$CATEGORIES_JSON]"

sudo -u "$USERNAME" \
    NEXUS_INSTALL_USER="$USERNAME" \
    NEXUS_WORKSPACE_DIR="$WORKSPACE_DIR" \
    NEXUS_WORKSPACE_CATEGORIES="$NEXUS_WORKSPACE_CATEGORIES" \
    bash "$INSTALL_DIR/setup.sh"


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
echo "  $(bold "Workspace:")"
info "$WORKSPACE_DIR/"
for category in "${CATEGORIES[@]}"; do
    info "  ├─ $category/"
done

echo
echo "  $(bold "Configuration:")"
info "$INSTALL_DIR/config/   — providers, adapters, specialists"
info "$INSTALL_DIR/.env      — API keys and secrets"
info "$WORKSPACE_DIR/.workspace.config  — workspace structure"

echo
echo "  $(bold "Project commands (via Mattermost/Discord/Telegram):")"
info "@bot create project [category]/[projectname]"
info "@bot link [category]/[projectname] to [adapter]:[channel]"

echo
echo "  $(dim "To add providers or reconfigure later:")"
info "sudo su - $USERNAME"
info "cd nexus && source .venv/bin/activate && python -m src.setup.wizard"
echo
