"""
SessionStateStore — persistent JSON store for SessionState objects.

Ported from claude-brain. Stores per-session specialist claim pools and
locked decisions so orchestration state persists across session rotations.

Keyed by session_key. Stored at operator-configured path.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from .session_state import SessionState

logger = logging.getLogger("nexus.session_state_store")


class SessionStateStore:
    """
    Async JSON-backed store for SessionState objects.

    One entry per session_key. The orchestrator reads/writes here
    so that specialist claims and conflicts persist across multiple
    orchestration rounds in the same channel session.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
                logger.info(f"Loaded {len(self._data)} session states from {self._path}")
            except Exception as e:
                logger.warning(f"SessionStateStore: failed to load {self._path}: {e}")
                self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.error(f"SessionStateStore: failed to save {self._path}: {e}")

    def get(self, session_key: str) -> Optional[SessionState]:
        raw = self._data.get(session_key)
        if raw is None:
            return None
        try:
            return SessionState.from_dict(raw)
        except Exception as e:
            logger.warning(f"SessionStateStore: failed to deserialize {session_key}: {e}")
            return None

    def has(self, session_key: str) -> bool:
        return session_key in self._data

    async def set(self, session_key: str, state: SessionState) -> None:
        async with self._lock:
            self._data[session_key] = state.to_dict()
            self._save()

    async def delete(self, session_key: str) -> None:
        async with self._lock:
            if session_key in self._data:
                del self._data[session_key]
                self._save()
