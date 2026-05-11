"""
Provider-agnostic session store.

Stores arbitrary string values (e.g. claude_code session IDs) keyed by a
platform session key ("mm_channelid", "tg_chatid_threadid", etc.).

API used by bridge.py and all adapters:
  sessions.get(key)            → Optional[str]   (sync)
  await sessions.set(key, val) → None
  await sessions.mark_active(key) → None
  await sessions.mark_idle(key)   → None
  await sessions.clear(key)    → None
  sessions.purge_stale(ttl)    → int (count removed)
  sessions.stats()             → dict
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class SessionEntry:
    value: str = ""
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    active: bool = False


@dataclass
class Session:
    """Per-conversation session metadata used by the engine."""
    session_id: str
    platform: str = ""
    channel_id: str = ""
    provider_name: str = ""
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, store_path: str = "config/sessions.json"):
        self.path = Path(store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, SessionEntry] = {}
        self._load()

    # ── Sync read ──────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        return entry.value if entry else None

    def get_or_create(
        self,
        session_id: str,
        platform: str = "",
        channel_id: str = "",
        provider_name: str = "",
    ) -> Session:
        entry = self._data.get(session_id)
        if entry:
            return Session(
                session_id=session_id,
                platform=platform,
                channel_id=channel_id,
                provider_name=provider_name,
                message_count=0,
                created_at=entry.created_at,
                last_active=entry.last_active,
            )
        self._data[session_id] = SessionEntry(value=session_id)
        self._save()
        return Session(
            session_id=session_id,
            platform=platform,
            channel_id=channel_id,
            provider_name=provider_name,
        )

    def update(self, session: Session) -> None:
        entry = self._data.get(session.session_id)
        if entry:
            entry.last_active = time.time()
            entry.active = True
            self._save()

    # ── Async writes ───────────────────────────────────────────────────────

    async def set(self, key: str, value: str) -> None:
        entry = self._data.get(key)
        if entry:
            entry.value = value
            entry.last_active = time.time()
        else:
            self._data[key] = SessionEntry(value=value)
        self._save()

    async def mark_active(self, key: str) -> None:
        entry = self._data.get(key)
        if entry:
            entry.active = True
            entry.last_active = time.time()
        else:
            self._data[key] = SessionEntry(active=True)
        self._save()

    async def mark_idle(self, key: str) -> None:
        entry = self._data.get(key)
        if entry:
            entry.active = False
        self._save()

    async def clear(self, key: str) -> None:
        self._data.pop(key, None)
        self._save()

    # ── Maintenance ────────────────────────────────────────────────────────

    def purge_stale(self, ttl: int = 7200) -> int:
        cutoff = time.time() - ttl
        stale = [k for k, e in self._data.items() if e.last_active < cutoff and not e.active]
        for k in stale:
            del self._data[k]
        if stale:
            self._save()
        return len(stale)

    def stats(self) -> dict:
        active = sum(1 for e in self._data.values() if e.active)
        return {
            "total": len(self._data),
            "active": active,
            "idle": len(self._data) - active,
        }

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            for k, v in raw.items():
                self._data[k] = SessionEntry(**v)
        except (json.JSONDecodeError, TypeError):
            self._data = {}

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps({k: asdict(e) for k, e in self._data.items()}, indent=2)
            )
        except OSError:
            pass
