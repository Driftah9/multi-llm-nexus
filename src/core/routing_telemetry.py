"""Passive routing telemetry — observe-only performance recorder.

Phase 1 of the routing weight feedback loop. Records one append-only JSONL line
per non-primary provider call so Phase 2 can derive per-provider, per-task-class
weights. THIS MODULE CHANGES NO ROUTING BEHAVIOUR — it only watches.

Safety contract: record() must NEVER raise into the caller's response path.
Every entry point swallows its own exceptions and logs at debug level.

The "output_ok" signal is a zero-inference heuristic: small free-tier models
frequently narrate their reasoning instead of answering ("Okay, the user
wants…", "<think>…"). Flagging it here gives Phase 2 a usable quality proxy
without spending tokens on an LLM judge.

Log path: $ROUTING_TELEMETRY_PATH  or  $NEXUS_DATA_DIR/routing_telemetry.jsonl
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_LOG_PATH = os.environ.get(
    "ROUTING_TELEMETRY_PATH",
    str(_DATA_DIR / "routing_telemetry.jsonl"),
)

_REASONING_LEAK = re.compile(
    r"^\s*(<think>|okay[,;]|hmm[,.]|let me\b|we need to\b|the user\b|"
    r"first[,;]|alright[,;]|so,? the user\b|i need to\b)",
    re.IGNORECASE,
)


def looks_like_reasoning_leak(text: str) -> bool:
    """True if `text` opens with a reasoning-out-loud marker instead of an answer."""
    if not text:
        return False
    return bool(_REASONING_LEAK.match(text))


def record(
    provider: str,
    duration_ms: int,
    success: bool,
    text: str = "",
    task_class: Optional[str] = None,
) -> None:
    """Append one telemetry observation. Never raises.

    output_ok is True when the call succeeded AND the response does not open
    with a reasoning leak. Recorded as None on failed calls so Phase 2 can
    separate "wrong model for the job" from "model down".
    """
    try:
        output_ok: Optional[bool]
        if not success:
            output_ok = None
        else:
            output_ok = not looks_like_reasoning_leak(text)

        row = {
            "ts": round(time.time(), 3),
            "provider": provider,
            "task_class": task_class,
            "duration_ms": duration_ms,
            "success": success,
            "output_ok": output_ok,
            "text_len": len(text or ""),
        }
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        logger.debug(f"routing_telemetry.record skipped: {e}")
