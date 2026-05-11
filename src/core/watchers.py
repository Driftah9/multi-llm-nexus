"""
Linux Watcher Framework — zero-token background monitoring.

Instead of burning LLM tokens on idle ticks, lightweight Linux processes
(cron jobs, systemd timers, inotifywait) monitor for actionable events.
When a watcher finds something, it writes a WakeEvent to the trigger
file. The engine's standby loop picks it up and activates the LLM.

Watchers are shell scripts that:
  1. Check one thing (new messages, service down, alert fired)
  2. Exit 0 with no output if nothing found
  3. Exit 0 with a JSON WakeEvent on stdout if work found
  4. The cron wrapper pipes stdout to the trigger mechanism

This module defines the Python side: reading triggers, dispatching
wake events, and managing watcher registration.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class WakeReason(Enum):
    """Why the LLM is being woken up."""
    NEW_MESSAGE = "new_message"          # Adapter received a message
    SCHEDULED_TASK = "scheduled_task"    # Cron-based scheduled check
    SERVICE_ALERT = "service_alert"      # Health check failure / Grafana alert
    FILE_CHANGE = "file_change"          # Vault or inbox file modified
    USER_DIRECT = "user_direct"         # User initiated conversation
    MANUAL = "manual"                    # Manual wake via CLI


@dataclass
class WakeEvent:
    """A trigger event from a Linux watcher."""
    reason: WakeReason
    source: str              # Which watcher produced this (e.g., "telegram-poll")
    summary: str             # Human-readable description for the LLM
    timestamp: float = field(default_factory=time.time)
    priority: int = 5        # 1=critical, 5=normal, 10=low
    data: dict = field(default_factory=dict)  # Watcher-specific payload

    def to_json(self) -> str:
        return json.dumps({
            "reason": self.reason.value,
            "source": self.source,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "priority": self.priority,
            "data": self.data,
        })

    @classmethod
    def from_json(cls, raw: str) -> "WakeEvent":
        d = json.loads(raw)
        return cls(
            reason=WakeReason(d["reason"]),
            source=d["source"],
            summary=d["summary"],
            timestamp=d.get("timestamp", time.time()),
            priority=d.get("priority", 5),
            data=d.get("data", {}),
        )


class WatcherConfig:
    """Registry of configured watchers and their schedules."""

    def __init__(self, config: dict):
        self.watchers = config.get("watchers", [])
        self.trigger_path = Path(
            config.get("trigger_path", "/tmp/nexus-wake.trigger")
        )
        self.standby_poll_interval = config.get("standby_poll_seconds", 5)

    def get_active_watchers(self) -> list[dict]:
        """Return watchers that are enabled."""
        return [w for w in self.watchers if w.get("enabled", True)]


class TriggerListener:
    """
    Listens for wake events from Linux watchers.

    Watchers write JSON WakeEvents (one per line) to the trigger file.
    The listener reads and consumes them. This is deliberately simple —
    a plain file, not a socket or message queue — so any shell script
    can produce triggers with just `echo '{}' >> /path/to/trigger`.
    """

    def __init__(self, config: WatcherConfig):
        self.config = config
        self.trigger_path = config.trigger_path
        self._ensure_trigger_file()

    def _ensure_trigger_file(self) -> None:
        """Create the trigger file if it doesn't exist."""
        self.trigger_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.trigger_path.exists():
            self.trigger_path.touch()

    def read_events(self) -> list[WakeEvent]:
        """
        Read and consume all pending wake events.

        Atomic: reads the file, parses events, then truncates.
        Events that fail to parse are logged and discarded.
        """
        events = []
        try:
            content = self.trigger_path.read_text().strip()
            if not content:
                return events

            # Consume: truncate the file after reading
            self.trigger_path.write_text("")

            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(WakeEvent.from_json(line))
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Bad event line — skip it, don't crash
                    pass

        except FileNotFoundError:
            self._ensure_trigger_file()

        return events

    async def wait_for_event(self, timeout: Optional[float] = None) -> list[WakeEvent]:
        """
        Block until wake events appear or timeout expires.

        This is the standby loop's core: poll the trigger file at
        the configured interval. Returns as soon as events are found.
        """
        deadline = time.time() + timeout if timeout else None
        poll_interval = self.config.standby_poll_interval

        while True:
            events = self.read_events()
            if events:
                return events

            if deadline and time.time() >= deadline:
                return []  # Timeout, no events

            await asyncio.sleep(poll_interval)


def write_wake_event(trigger_path: str, event: WakeEvent) -> None:
    """
    Write a wake event to the trigger file.

    Called by watcher scripts (via the companion CLI tool) or
    internally when generating wake triggers programmatically.
    """
    path = Path(trigger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(event.to_json() + "\n")
