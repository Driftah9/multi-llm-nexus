"""Turn-failure surfacing — a crashed turn must never leave "thinking…" hanging.

Ported from live claude-brain (2026-10-19; live incident 2026-10-04: a pre-dispatch crash left
the heartbeat counting "thinking… 20m" forever). When a turn dies anywhere in the pipeline
(triage, context assembly, dispatch), the adapter edits the placeholder into a visible error and
files the failure here so the operator can find it after the fact.

Record shape — the error-inbox entry format scripts/nightly_sweeper.py reads and DMs, so turn
crashes reach the operator with everything else that broke overnight: `$NEXUS_DATA_DIR/
error-inbox/open/turn-crash__<platform>.json` — status, detail, first/last seen, occurrences,
and evidence (session key, stage, traceback tail). A later crash on the same platform refreshes
the entry and bumps `occurrences`; `nightly_sweeper.py --clear turn-crash__<platform>` removes
it (a RECOVERED entry is replaced by the next crash, kept under `previous`).

# NEXUS:PORTABLE — no platform or provider names; zero LLM calls.
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
OPEN_DIR = _DATA_DIR / "error-inbox" / "open"


def user_message(exc: BaseException, stage: str) -> str:
    """What the user sees in place of the reply."""
    spent = ("Nothing reached a model, so no usage was spent. " if stage == "pre-dispatch" else "")
    return (
        f"⚠️ **Turn failed** ({stage}) — `{type(exc).__name__}: {str(exc)[:200]}`\n"
        f"{spent}The failure was logged for the operator; try again, or check the Nexus logs."
    )


def record(platform: str, session_key: str, stage: str, exc: BaseException) -> None:
    """File/refresh the failure record. Never raises — surfacing must not mask the crash."""
    try:
        OPEN_DIR.mkdir(parents=True, exist_ok=True)
        job = f"turn-crash__{(platform or 'unknown').lower()}"
        f = OPEN_DIR / f"{job}.json"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        detail = f"{stage}: {type(exc).__name__}: {str(exc)[:300]}"
        try:
            cur = json.loads(f.read_text()) if f.exists() else None
        except Exception:
            cur = None
        if cur and cur.get("status") != "RECOVERED":
            cur["occurrences"] = cur.get("occurrences", 1) + 1
            cur.update(status="FAILED", detail=detail, last_seen=now)
        else:
            cur = {"job_id": job, "status": "FAILED", "detail": detail, "first_seen": now,
                   "last_seen": now, "occurrences": 1, "previous": cur}
        cur["evidence"] = {
            "session_key": session_key,
            "stage": stage,
            "traceback_tail": "".join(traceback.format_exception(exc))[-2000:],
        }
        f.write_text(json.dumps(cur, indent=2))
    except Exception as e:  # pragma: no cover — best effort
        logger.warning(f"turn_failure.record failed: {e}")
