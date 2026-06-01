"""
Persistent session store — maps context keys to provider session IDs.

Ported from claude-brain. Provider-agnostic: stores opaque session_id strings
returned by whichever bridge/provider is active.

Features:
  - 24-hour session expiry
  - Idle detection (30-minute inactivity timeout)
  - Message counting per session
  - Async-safe writes via asyncio.Lock
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.session_store")

SESSION_EXPIRY_HOURS = 24
IDLE_TIMEOUT_SECS = 30 * 60  # 30 minutes


@dataclass
class SessionInfo:
    session_id: str
    last_used: str          # ISO format UTC
    message_count: int = 0
    last_activity_ts: float = field(default_factory=time.time)
    idle: bool = False

    def is_expired(self) -> bool:
        last = datetime.fromisoformat(self.last_used)
        age = datetime.now(timezone.utc) - last
        return age.total_seconds() > SESSION_EXPIRY_HOURS * 3600

    def is_idle(self) -> bool:
        return self.idle

    def mark_idle(self):
        self.idle = True


class SessionStore:
    """
    Async JSON-backed session store.

    Keys are operator-defined session keys (e.g. "mm_channel_abc_thread_xyz").
    Values are opaque session IDs returned by the active provider.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, SessionInfo] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                for key, val in raw.items():
                    self._data[key] = SessionInfo(**val)
                logger.info(f"Loaded {len(self._data)} sessions from {self._path}")
            except Exception as e:
                logger.warning(f"Failed to load sessions: {e}")

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            {k: asdict(v) for k, v in self._data.items()},
            indent=2,
        ))

    def get(self, key: str) -> Optional[str]:
        """Return session_id for key if not expired, else None."""
        info = self._data.get(key)
        if info and not info.is_expired():
            return info.session_id
        if info and info.is_expired():
            del self._data[key]
        return None

    def get_info(self, key: str) -> Optional[SessionInfo]:
        return self._data.get(key)

    def get_idle_state(self, key: str) -> bool:
        info = self._data.get(key)
        return info.is_idle() if info else False

    async def set(self, key: str, session_id: str):
        """Store or update a session."""
        async with self._lock:
            existing = self._data.get(key)
            count = (existing.message_count + 1) if existing else 1
            self._data[key] = SessionInfo(
                session_id=session_id,
                last_used=datetime.now(timezone.utc).isoformat(),
                message_count=count,
                last_activity_ts=time.time(),
                idle=False,
            )
            self._save()

    async def mark_active(self, key: str):
        """Update last activity timestamp without creating a new session."""
        async with self._lock:
            if key in self._data:
                self._data[key].last_activity_ts = time.time()
                self._data[key].idle = False
                self._save()

    async def mark_idle(self, key: str):
        """Mark a session as idle."""
        async with self._lock:
            if key in self._data:
                self._data[key].mark_idle()
                self._save()

    async def clear(self, key: str):
        """Remove a session."""
        async with self._lock:
            self._data.pop(key, None)
            self._save()
