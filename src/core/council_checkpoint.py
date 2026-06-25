"""Rich step-boundary checkpoints for council failover — the durable "what".

A promoted (or knocked-in) orchestrator resumes an interrupted task from EXACT state, not
a lossy summary. Each checkpoint is fencing-stamped with the writer's leadership term (see
council_lease) so a resumer can reject state from a superseded orchestrator.

Backend: the same optional Redis-compatible coordination store as council_lease. No store
wired → every method degrades gracefully (save→False, load→None). The Checkpoint dataclass
itself is pure and store-independent.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .council_lease import _connect

logger = logging.getLogger(__name__)

_KEY_PREFIX = "council:checkpoint:"
_INDEX_KEY = "council:checkpoint:_open"
DEFAULT_TTL_S = 24 * 3600


@dataclass
class Checkpoint:
    """Full in-flight task state — everything a new orchestrator needs to resume."""
    task_id: str
    session_key: str = ""
    orchestrator: str = ""
    fencing_token: int = 0
    step: str = ""
    original_message: str = ""
    partial_result: str = ""
    next_step: str = ""
    provider_history: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    updated_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "Checkpoint":
        d = json.loads(s)
        known = {f: d.get(f) for f in cls.__dataclass_fields__ if d.get(f) is not None}
        return cls(**known)


class CheckpointStore:
    """Durable checkpoint persistence. Graceful — a backend blip never crashes the request
    path; a failed save just means resume falls back to coarser state."""

    def __init__(self):
        self._r = None

    def _redis(self):
        if self._r is None:
            self._r = _connect()
        return self._r

    def save(self, cp: Checkpoint, ttl_s: int = DEFAULT_TTL_S) -> bool:
        cp.updated_at = datetime.now(timezone.utc).isoformat()
        r = self._redis()
        if r is None:
            return False
        try:
            pipe = r.pipeline()
            pipe.set(_KEY_PREFIX + cp.task_id, cp.to_json(), ex=ttl_s)
            pipe.sadd(_INDEX_KEY, cp.task_id)
            pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"[checkpoint] save failed for {cp.task_id}: {e}")
            return False

    def load(self, task_id: str) -> Optional[Checkpoint]:
        r = self._redis()
        if r is None:
            return None
        try:
            raw = r.get(_KEY_PREFIX + task_id)
            return Checkpoint.from_json(raw) if raw else None
        except Exception as e:
            logger.warning(f"[checkpoint] load failed for {task_id}: {e}")
            return None

    def delete(self, task_id: str) -> bool:
        r = self._redis()
        if r is None:
            return False
        try:
            pipe = r.pipeline()
            pipe.delete(_KEY_PREFIX + task_id)
            pipe.srem(_INDEX_KEY, task_id)
            pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"[checkpoint] delete failed for {task_id}: {e}")
            return False

    def list_open(self) -> list[Checkpoint]:
        """All open checkpoints — a promoted orchestrator scans these to find tasks to
        resume. Prunes index entries whose checkpoint has expired."""
        out: list[Checkpoint] = []
        r = self._redis()
        if r is None:
            return out
        try:
            for tid in (r.smembers(_INDEX_KEY) or set()):
                raw = r.get(_KEY_PREFIX + tid)
                if raw:
                    try:
                        out.append(Checkpoint.from_json(raw))
                    except Exception:
                        pass
                else:
                    r.srem(_INDEX_KEY, tid)
        except Exception as e:
            logger.warning(f"[checkpoint] list_open failed: {e}")
        return out
