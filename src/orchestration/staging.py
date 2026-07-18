"""Worker-finding staging — the "where vs what" split applied to delegation (D2).

Workers (nano/standard tier, dispatched by delegator.py) NEVER write canonical
memory directly — same single-writer principle as council failover, applied at
task granularity. Their output lands here first, keyed by task_id, structured
(not a lossy summary string). Only the orchestrator promotes staged findings
into a compiled result; nothing here writes to FalkorDB/graphiti/canonical
memory.

Disk-backed (mirrors core/task_journal.py's breadcrumb pattern) rather than a
new subsystem — one JSON file per task_id, cleared after the orchestrator
compiles/reconciles it.

NEXUS:PORTABLE — storage location is env-overridable ($NEXUS_DATA_DIR) so
every install gets its own staging area with no hardcoded operator path.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
STAGING_DIR = _DATA_DIR / "staging"


def _ensure_dir() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def _path(task_id: str) -> Path:
    return STAGING_DIR / f"{task_id}.json"


def stage_finding(
    task_id: str,
    worker_id: str,
    subtask: str,
    provider: str,
    finding: str,
) -> None:
    """Append one worker's finding to this task's staging record.

    Structured, not prose-collapsed — {provider, subtask, finding} survives
    intact for the orchestrator's compile step and (if it fires) blind council
    review. Never call this from worker code paths that also touch canonical
    memory; staging is read-many/write-by-orchestrator-only downstream.
    """
    _ensure_dir()
    path = _path(task_id)
    record = {"findings": []}
    if path.exists():
        try:
            record = json.loads(path.read_text())
        except Exception as e:
            logger.warning(f"staging: corrupt record for {task_id}, resetting: {e}")
            record = {"findings": []}

    record.setdefault("findings", []).append({
        "worker_id": worker_id,
        "subtask": subtask,
        "provider": provider,
        "finding": finding,
        "staged_at": datetime.now(timezone.utc).isoformat(),
    })
    path.write_text(json.dumps(record, indent=2))


def get_staged_findings(task_id: str) -> List[Dict[str, str]]:
    """Read back all findings staged for this task_id. Empty list if none."""
    path = _path(task_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("findings", [])
    except Exception as e:
        logger.warning(f"staging: failed to read {task_id}: {e}")
        return []


def clear_staging(task_id: str) -> None:
    """Delete the staging record after the orchestrator has compiled/committed it."""
    path = _path(task_id)
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"staging: failed to clear {task_id}: {e}")
