"""Generate a systemd service unit for Multi-LLM Nexus."""

import os
import sys
from pathlib import Path


def generate(install_dir: str = None, user: str = None) -> str:
    install_dir = install_dir or str(Path(__file__).parent.parent.parent)
    user = user or os.environ.get("USER", "nexus")
    python = f"{install_dir}/.venv/bin/python"

    return f"""[Unit]
Description=Multi-LLM Nexus Agent
After=network.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={install_dir}
ExecStart={python} -m src.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal
SyslogIdentifier=nexus

[Install]
WantedBy=multi-user.target
"""


def main():
    install_dir = str(Path(__file__).parent.parent.parent.resolve())
    user = os.environ.get("USER", "nexus")

    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("Usage: python -m src.setup.systemd [--install]")
        print("  Generates a systemd service file for Nexus.")
        print("  --install  Write to /etc/systemd/system/ (requires sudo)")
        return

    unit = generate(install_dir, user)
    unit_path = Path(install_dir) / "nexus.service"

    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        system_path = Path("/etc/systemd/system/nexus.service")
        system_path.write_text(unit)
        print(f"Installed to {system_path}")
        print("Run: sudo systemctl daemon-reload && sudo systemctl enable --now nexus")
    else:
        unit_path.write_text(unit)
        print(f"Service file written to {unit_path}")
        print(f"To install: sudo cp {unit_path} /etc/systemd/system/")
        print("Then: sudo systemctl daemon-reload && sudo systemctl enable --now nexus")


if __name__ == "__main__":
    main()
