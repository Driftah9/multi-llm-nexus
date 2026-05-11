"""
HeartbeatManager — live status display for in-flight requests.

Adapter-agnostic. The adapter provides a push_fn callback that handles
the actual update (Mattermost PATCH, Discord edit, Telegram editMessage, etc.).

Format: _{prefix} · {model} [· {effort}] [— {agents}] — {phase} {elapsed}_

Display taxonomy:
    {display_prefix} — Derived from provider type, not connection method.
    "Claude", "Gemini", "OpenAI", "Groq", "Local" etc.
    Users know HOW they're connected (config). The heartbeat shows WHAT is active.

    {model_display} — Human-friendly model name: "Opus", "1.5-pro", "tinyllama"
    {effort} — Only shown when the provider supports effort levels (e.g. Claude).
    {agents} — Active specialist names (≤3: list, >3: count).
    {phase} — "thinking", "working", "triaging", "synthesizing"
    {elapsed} — Time since request started: "30s", "1m12s", "2m30s"

Examples:
    Claude · Opus · high — thinking 30s
    Claude · Opus · high — financial, security — working 1m12s
    Claude · Orchestrator — 4 agents active — working 2m30s
    Gemini · 2.0-flash · thinking — working 55s
    Local · tinyllama — thinking 15s
    OpenAI · o3 · reasoning — working 2m05s
    Groq · llama3-70b — thinking 8s
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

AGENT_LIST_MAX = 3  # 3 or fewer: list names; 4+ shows "N agents active"

PushFn = Callable[[str, str], Awaitable[None]]  # (post_id, formatted_text) -> None


@dataclass
class HeartbeatState:
    post_id: str              # Adapter-specific message identifier
    display_prefix: str       # "Claude", "Local", "Gemini", "OpenAI", etc.
    model_display: str        # "Opus", "tinyllama", "Orchestrator", etc.
    effort: Optional[str]     # "high", "low", None (hidden when None)
    agents: list[str] = field(default_factory=list)
    phase: str = "thinking"
    started: float = field(default_factory=time.time)


class HeartbeatManager:
    """
    Manages a live-updating status display for an in-flight request.

    The background tick loop pushes an updated display every 30s.
    Phase and agent changes trigger immediate updates without waiting for the tick.

    Adapter-agnostic: the adapter provides push_fn(post_id, text) which
    handles the platform-specific update mechanism.
    """

    def __init__(self, state: HeartbeatState, push_fn: PushFn):
        self.state = state
        self._push_fn = push_fn
        self._task: asyncio.Task | None = None

    def start(self) -> "HeartbeatManager":
        self._task = asyncio.create_task(self._tick_loop())
        return self

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def set_phase(self, phase: str) -> None:
        self.state.phase = phase
        await self._push()

    async def set_agents(self, agents: list[str]) -> None:
        self.state.agents = list(agents)
        await self._push()

    async def set_provider(self, prefix: str, model_display: str, effort: Optional[str]) -> None:
        self.state.display_prefix = prefix
        self.state.model_display = model_display
        self.state.effort = effort
        await self._push()

    def format(self) -> str:
        elapsed = int(time.time() - self.state.started)
        if elapsed < 60:
            elapsed_str = f"{elapsed}s"
        else:
            mins, secs = divmod(elapsed, 60)
            elapsed_str = f"{mins}m{secs:02d}s" if secs else f"{mins}m"

        parts = [self.state.display_prefix, self.state.model_display]
        if self.state.effort:
            parts.append(self.state.effort)
        header = " · ".join(parts)

        agents = self.state.agents
        if agents:
            if len(agents) <= AGENT_LIST_MAX:
                agent_str = ", ".join(agents)
            else:
                agent_str = f"{len(agents)} agents active"
            return f"_{header} — {agent_str} — {self.state.phase} {elapsed_str}_"
        return f"_{header} — {self.state.phase} {elapsed_str}_"

    async def _push(self) -> None:
        if not self.state.post_id:
            return
        try:
            await self._push_fn(self.state.post_id, self.format())
        except Exception:
            pass

    async def _tick_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                await self._push()
        except asyncio.CancelledError:
            pass
