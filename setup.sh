#!/usr/bin/env bash
# Multi-LLM-Nexus setup script
# Guides the Operator through first-time configuration.

set -e

NEXUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$NEXUS_DIR/.venv"
PYTHON="${VENV_DIR}/bin/python"

# Append to the install.sh log if one was opened by the parent.
_slog() {
    if [[ -n "${NEXUS_LOG_FILE:-}" ]]; then
        printf "[%s] SETUP: %s\n" "$(date +%T)" "$*" >> "$NEXUS_LOG_FILE"
    fi
}
_slog "setup.sh started (NEXUS_DIR=$NEXUS_DIR)"

echo ""
echo "  Multi-LLM-Nexus Setup"
echo "  ====================="
echo "  Your AI platform. Your rules."
echo ""

# Use the python binary install.sh detected (3.11+), fall back to python3
PYTHON_BIN="${NEXUS_PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "Python 3.11+ required. (tried: $PYTHON_BIN)"; exit 1; }
command -v docker >/dev/null 2>&1 && DOCKER_OK=true || DOCKER_OK=false

PYTHON_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PYTHON_VERSION found."
_slog "python binary: $PYTHON_BIN ($PYTHON_VERSION)"

# Virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    _slog "creating venv at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
_slog "venv activated"

_slog "pip install: pyyaml httpx python-dotenv"
pip install --quiet --upgrade pip 2>>"${NEXUS_LOG_FILE:-/dev/null}"
pip install --quiet pyyaml httpx python-dotenv 2>>"${NEXUS_LOG_FILE:-/dev/null}"

# Config files
if [ ! -f "$NEXUS_DIR/config/providers.yaml" ]; then
    cp "$NEXUS_DIR/config/providers.yaml.example" "$NEXUS_DIR/config/providers.yaml"
fi
if [ ! -f "$NEXUS_DIR/config/adapters.yaml" ]; then
    cp "$NEXUS_DIR/config/adapters.yaml.example" "$NEXUS_DIR/config/adapters.yaml"
fi
if [ ! -f "$NEXUS_DIR/.env" ]; then
    cp "$NEXUS_DIR/.env.example" "$NEXUS_DIR/.env"
fi

# Interactive wizard
# Run as module (-m src.setup.wizard) so relative imports work
_slog "launching wizard: src.setup.wizard"
echo ""
cd "$NEXUS_DIR"
$PYTHON -m src.setup.wizard
_slog "wizard returned (exit $?)"

echo ""

# Generate systemd service file
$PYTHON -m src.setup.systemd

echo ""
echo "Setup complete. Start Nexus with:"
echo "  source .venv/bin/activate && python -m src.main"
echo ""
echo "Or install as a service:"
echo "  sudo cp nexus.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now nexus"
echo ""
echo "To install watchers (zero-token background monitoring):"
echo "  cp watchers/examples/*.sh watchers/"
echo "  # Edit endpoints/paths in each script"
echo "  ./watchers/install-cron.sh watchers/"
echo ""
