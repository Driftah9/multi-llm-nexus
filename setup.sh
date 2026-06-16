#!/usr/bin/env bash
# Nexus reconfigure script — run this as the bot user to add providers or change settings.
#
# Usage (as the bot user):
#   cd ~/nexus && source .venv/bin/activate && python -m src.setup.wizard
#
# Or via this script:
#   bash ~/nexus/setup.sh

set -e

NEXUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$NEXUS_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "  No venv found. Run the installer first:"
    echo "    sudo bash /tmp/nexus-install.sh"
    exit 1
fi

source "$VENV_DIR/bin/activate"
cd "$NEXUS_DIR"
python -m src.setup.wizard
