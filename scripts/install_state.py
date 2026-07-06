#!/usr/bin/env python3
"""Install state checkpoint tracker.

Allows install.sh to save/resume progress. Maintains ~/.nexus-install-state.json
with completed phase markers, enabling graceful resume on failure.

Usage (from bash):
  # Mark phase complete
  python3 scripts/install_state.py mark-phase PHASE_NAME

  # Check if phase completed
  python3 scripts/install_state.py is-completed PHASE_NAME && { do_phase; }

  # Reset all state (clean install)
  python3 scripts/install_state.py reset

  # Show current state
  python3 scripts/install_state.py show
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

HOME = Path.home()
STATE_FILE = HOME / ".nexus-install-state.json"
LOCK_FILE = HOME / ".nexus-install-state.lock"


def load_state() -> dict:
    """Load install state from disk, or return empty state."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {"version": 1, "phases": {}, "started_at": None}
    return {"version": 1, "phases": {}, "started_at": None}


def save_state(state: dict) -> None:
    """Save install state to disk (atomically via temp + rename)."""
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def mark_phase(phase_name: str) -> None:
    """Mark a phase as completed."""
    state = load_state()
    if not state.get("started_at"):
        state["started_at"] = datetime.now(timezone.utc).isoformat()
    state["phases"][phase_name] = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok"
    }
    save_state(state)
    print(f"Marked phase: {phase_name}", file=sys.stderr)


def is_completed(phase_name: str) -> bool:
    """Return True if phase is marked as completed."""
    state = load_state()
    return phase_name in state.get("phases", {})


def reset() -> None:
    """Reset all install state (clean slate)."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    print("Install state reset", file=sys.stderr)


def show() -> None:
    """Display current install state."""
    state = load_state()
    print(json.dumps(state, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "mark-phase" and len(sys.argv) >= 3:
        mark_phase(sys.argv[2])
    elif cmd == "is-completed" and len(sys.argv) >= 3:
        sys.exit(0 if is_completed(sys.argv[2]) else 1)
    elif cmd == "reset":
        reset()
    elif cmd == "show":
        show()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
