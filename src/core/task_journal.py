"""Breadcrumb task journal for crash resilience.

Each in-flight task writes a JSON file here. On restart, the recovery module
scans these files, finds orphans, and prompts the user so no work is silently lost.

Journal dir: $NEXUS_DATA_DIR/task_journal/  (default: data/task_journal/)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
JOURNAL_DIR = _DATA_DIR / "task_journal"


def _ensure_dir() -> None:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


def open_task(
    task_id: str,
    platform: str,
    channel: str,
    thread_id: Optional[str],
    session_id: str,
    task_type: str,
    question: str,
    intent: str,
    goal: str,
) -> None:
    """Create a journal entry for a task that is now in-flight."""
    _ensure_dir()
    now = datetime.now(timezone.utc).isoformat()
    task_data = {
        "task_id": task_id,
        "created": now,
        "platform": platform,
        "channel": channel,
        "thread_id": thread_id,
        "session_id": session_id,
        "type": task_type,
        "question": question,
        "intent": intent,
        "goal": goal,
        "status": "in_progress",
        "phase": "triage",
        "started": now,
    }
    task_file = JOURNAL_DIR / f"{task_id}.json"
    task_file.write_text(json.dumps(task_data, indent=2))
    logger.info(f"Opened task journal: {task_id}")


def update_phase(task_id: str, phase: str) -> None:
    """Update the phase of an in-flight task."""
    task_file = JOURNAL_DIR / f"{task_id}.json"
    if not task_file.exists():
        logger.warning(f"task_journal: {task_id} not found, phase update ignored")
        return
    task_data = json.loads(task_file.read_text())
    task_data["phase"] = phase
    task_data["last_phase_update"] = datetime.now(timezone.utc).isoformat()
    task_file.write_text(json.dumps(task_data, indent=2))
    logger.debug(f"Task {task_id} phase → {phase}")


def close_task(task_id: str) -> None:
    """Remove the journal entry for a completed task."""
    task_file = JOURNAL_DIR / f"{task_id}.json"
    if not task_file.exists():
        logger.warning(f"task_journal: {task_id} not found, nothing to close")
        return
    task_file.unlink()
    logger.info(f"Closed task journal: {task_id}")


def scan_orphans() -> list[dict]:
    """Return all unfinished task entries (journal files still on disk)."""
    _ensure_dir()
    orphans = []
    for task_file in JOURNAL_DIR.glob("*.json"):
        try:
            orphans.append(json.loads(task_file.read_text()))
        except json.JSONDecodeError as e:
            logger.warning(f"task_journal: malformed file {task_file}: {e}")
    return orphans
