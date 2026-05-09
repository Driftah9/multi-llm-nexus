"""
Provider-agnostic session management.
Each conversation context (channel, thread, DM) gets its own session.
Sessions persist across daemon restarts via JSON store.
"""
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Session:
    session_id: str
    platform: str
    channel_id: str
    provider_name: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    message_count: int = 0
    context: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def touch(self):
        self.last_active = time.time()
        self.message_count += 1

    def is_stale(self, ttl_seconds: int = 3600) -> bool:
        return (time.time() - self.last_active) > ttl_seconds


class SessionStore:
    def __init__(self, store_path: str = "config/sessions.json"):
        self.path = Path(store_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._load()

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, platform: str, channel_id: str, provider_name: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(
                session_id=session_id,
                platform=platform,
                channel_id=channel_id,
                provider_name=provider_name,
            )
            self._save()
        return self._sessions[session_id]

    def update(self, session: Session):
        session.touch()
        self._sessions[session.session_id] = session
        self._save()

    def remove(self, session_id: str):
        self._sessions.pop(session_id, None)
        self._save()

    def purge_stale(self, ttl_seconds: int = 3600):
        stale = [sid for sid, s in self._sessions.items() if s.is_stale(ttl_seconds)]
        for sid in stale:
            del self._sessions[sid]
        if stale:
            self._save()
        return len(stale)

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for sid, s in data.items():
                    self._sessions[sid] = Session(**s)
            except (json.JSONDecodeError, TypeError):
                self._sessions = {}

    def _save(self):
        self.path.write_text(json.dumps(
            {sid: asdict(s) for sid, s in self._sessions.items()},
            indent=2
        ))
