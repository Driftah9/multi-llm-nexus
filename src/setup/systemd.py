"""Generate systemd units for Multi-LLM Nexus."""

import os
import shutil
import sys
from pathlib import Path


def generate_llmfit_service(install_dir: str = None, user: str = None) -> str:
    """
    Oneshot unit for the hardware-fit check (src/lifecycle/fit_check.py).
    Paired with generate_llmfit_timer() — the timer, not this unit, is what
    gets enabled. Mechanical: runs `llmfit update` + a fit comparison and only
    reaches the LLM/adapter layer (via Notifier) if a qualifying candidate
    shows up. Never pulls or reconfigures a model on its own.
    """
    install_dir = install_dir or str(Path(__file__).parent.parent.parent)
    user = user or os.environ.get("USER", "nexus")
    python = f"{install_dir}/.venv/bin/python"

    return f"""[Unit]
Description=Nexus llmfit hardware-fit check (mechanical — notifies only, never auto-swaps)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={user}
WorkingDirectory={install_dir}
ExecStart={python} scripts/llmfit_fit_check.py
Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal
SyslogIdentifier=nexus-llmfit-check
"""


def generate_llmfit_timer() -> str:
    """Weekly trigger for nexus-llmfit-check.service. The script's own
    interval_days skip-logic (config/model_sources.yaml -> fit_check) is the
    real cadence control; the timer just makes sure it gets a chance to run."""
    return """[Unit]
Description=Weekly trigger for the Nexus llmfit hardware-fit check

[Timer]
OnCalendar=weekly
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
"""


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

    # llmfit fit-check timer — best-effort, only if llmfit is actually installed.
    if shutil.which("llmfit"):
        fit_service = generate_llmfit_service(install_dir, user)
        fit_timer = generate_llmfit_timer()
        fit_service_path = Path(install_dir) / "nexus-llmfit-check.service"
        fit_timer_path = Path(install_dir) / "nexus-llmfit-check.timer"

        if len(sys.argv) > 1 and sys.argv[1] == "--install":
            Path("/etc/systemd/system/nexus-llmfit-check.service").write_text(fit_service)
            Path("/etc/systemd/system/nexus-llmfit-check.timer").write_text(fit_timer)
            print("Installed to /etc/systemd/system/nexus-llmfit-check.{service,timer}")
            print("Run: sudo systemctl daemon-reload && sudo systemctl enable --now nexus-llmfit-check.timer")
        else:
            fit_service_path.write_text(fit_service)
            fit_timer_path.write_text(fit_timer)
            print(f"llmfit fit-check timer written to {fit_timer_path} (+ .service)")
    else:
        print("llmfit not found — skipping fit-check timer (wizard falls back to the RAM heuristic)")


if __name__ == "__main__":
    main()
