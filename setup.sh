#!/usr/bin/env bash
# Multi-LLM-Nexus setup script
# Guides the Operator through first-time configuration.

set -e

NEXUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$NEXUS_DIR/.venv"
PYTHON="${VENV_DIR}/bin/python"

echo ""
echo "  Multi-LLM-Nexus Setup"
echo "  ====================="
echo "  Your AI platform. Your rules."
echo ""

# Prerequisites
command -v python3 >/dev/null 2>&1 || { echo "Python 3.10+ required."; exit 1; }
command -v docker >/dev/null 2>&1 && DOCKER_OK=true || DOCKER_OK=false

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PYTHON_VERSION found."

# Virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet pyyaml httpx python-dotenv

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
echo ""
$PYTHON "$NEXUS_DIR/src/setup/wizard.py"

echo ""

# Generate systemd service file
$PYTHON "$NEXUS_DIR/src/setup/systemd.py"

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
