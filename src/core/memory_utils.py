"""
Memory file utilities — backup, history tracking, and pending update application.

Ported from claude-brain. memory_dir is configurable so any operator deployment
can point this at their own memory directory. No hardcoded paths.

Provides:
  backup_memory_file(path)           — copy to .backups/YYYY-MM-DD/ before writes
  append_history(path, note)         — append entry to ## History section (cap 10)
  apply_pending_update(upd, dir)     — backup + apply + history for one pending update
  load_pending(dir)                  — load pending_updates.json
  save_pending(updates, dir)         — save pending_updates.json
  purge_old_backups(dir)             — remove backup dirs older than retention window
"""

import json
import logging
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.memory_utils")

BACKUP_RETENTION_DAYS = 30
HISTORY_CAP = 10


def _backup_dir(memory_dir: Path) -> Path:
    return memory_dir / ".backups"


def _pending_file(memory_dir: Path) -> Path:
    return memory_dir / "pending_updates.json"


def backup_memory_file(path: Path) -> Optional[Path]:
    """Copy a memory file to .backups/YYYY-MM-DD/stem_HHMMSS.md before modifying."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        ts = datetime.now().strftime("%H%M%S")
        day_dir = _backup_dir(path.parent) / today
        day_dir.mkdir(parents=True, exist_ok=True)
        dest = day_dir / f"{path.stem}_{ts}.md"
        shutil.copy2(path, dest)
        logger.debug(f"Backed up {path.name} → {dest}")
        return dest
    except Exception as e:
        logger.warning(f"Backup failed for {path.name}: {e}")
        return None


def append_history(path: Path, note: str) -> None:
    """
    Append a timestamped note to ## History section in a memory file.
    Creates the section if absent. Trims to HISTORY_CAP entries.
    """
    try:
        content = path.read_text(encoding="utf-8")
        today = date.today().isoformat()
        entry = f"- {today}: {note}"

        history_pattern = re.compile(r"^## History\s*$", re.MULTILINE)
        match = history_pattern.search(content)

        if match:
            section_start = match.end()
            lines = content[section_start:].splitlines()
            entries = []
            rest_start = len(lines)
            for i, line in enumerate(lines):
                if line.startswith("## "):
                    rest_start = i
                    break
                if line.startswith("- "):
                    entries.append(line)

            entries.insert(0, entry)
            entries = entries[:HISTORY_CAP]

            before = content[:section_start]
            after_lines = lines[rest_start:]
            new_section = "\n".join(entries)
            after = "\n".join(after_lines)
            new_content = before + "\n" + new_section + ("\n\n" if after else "") + after
        else:
            new_content = content.rstrip() + f"\n\n## History\n{entry}\n"

        path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        logger.warning(f"append_history failed for {path.name}: {e}")


def apply_pending_update(update: dict, memory_dir: Path) -> tuple[bool, str]:
    """
    Apply one pending update to its target memory file.
    Backs up the file first. Appends history entry.
    Returns (success, message).
    """
    file_stem = update.get("file_stem", "").strip()
    old_value = update.get("old_value", "").strip()
    new_value = update.get("new_value", "").strip()
    reason = update.get("reason", "automated update")

    if not file_stem or not old_value or not new_value:
        return False, "Missing file_stem, old_value, or new_value"

    target = memory_dir / f"{file_stem}.md"
    if not target.exists():
        return False, f"File not found: {file_stem}.md"

    content = target.read_text(encoding="utf-8")
    if old_value not in content:
        return False, f"old_value not found in {file_stem}.md — may have already been updated"

    backup_memory_file(target)
    new_content = content.replace(old_value, new_value, 1)
    target.write_text(new_content, encoding="utf-8")
    append_history(target, reason)

    return True, f"Updated {file_stem}.md"


def load_pending(memory_dir: Path) -> list[dict]:
    """Load pending_updates.json. Returns empty list if missing or corrupt."""
    pending = _pending_file(memory_dir)
    if not pending.exists():
        return []
    try:
        return json.loads(pending.read_text())
    except Exception:
        return []


def save_pending(updates: list[dict], memory_dir: Path) -> None:
    """Write pending_updates.json."""
    _pending_file(memory_dir).write_text(json.dumps(updates, indent=2))


def purge_old_backups(memory_dir: Path) -> int:
    """Remove backup day-dirs older than BACKUP_RETENTION_DAYS. Returns count removed."""
    backup_root = _backup_dir(memory_dir)
    if not backup_root.exists():
        return 0
    cutoff = date.today() - timedelta(days=BACKUP_RETENTION_DAYS)
    removed = 0
    for day_dir in backup_root.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            if date.fromisoformat(day_dir.name) < cutoff:
                shutil.rmtree(day_dir)
                removed += 1
        except ValueError:
            pass
    return removed
