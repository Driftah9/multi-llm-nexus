"""
Session summary store — context distillation for session continuity.

When !new is issued or the idle timeout fires, a fast provider distills
the current session into 5-8 bullets. The next session start injects
this summary so context is never cold.

Ported from claude-brain (mattermost-daemon/src/session_summary.py).
Provider-agnostic: distillation uses whatever nano-tier provider is configured.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.session_summary")

DISTILL_PROMPT = (
    "Summarize our conversation so far in 5-8 bullet points. "
    "Cover: current tasks and their status, decisions made, important details "
    "discussed, actions taken, and anything left unfinished. "
    "Be specific — include actual names, values, and paths where relevant. "
    "Keep it under 400 words. "
    "This summary will be injected into the next session to restore working context."
)


@dataclass
class SummaryEntry:
    summary: str
    timestamp: str
    session_id: str = ""


class SessionSummaryStore:
    """
    Async JSON-backed store for per-session distilled summaries.

    One entry per session_key. On !new, the distill() method produces
    a summary from the recent conversation history, stores it, and
    returns it for injection into the next session's system prompt.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, SummaryEntry] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                for key, val in raw.items():
                    self._data[key] = SummaryEntry(**val)
                logger.info(f"Loaded {len(self._data)} session summaries")
            except Exception as e:
                logger.warning(f"Failed to load summaries: {e}")

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            {k: asdict(v) for k, v in self._data.items()},
            indent=2,
        ))

    def get(self, session_key: str) -> Optional[str]:
        entry = self._data.get(session_key)
        return entry.summary if entry else None

    async def set(self, session_key: str, summary: str, session_id: str = ""):
        async with self._lock:
            self._data[session_key] = SummaryEntry(
                summary=summary,
                timestamp=datetime.now(timezone.utc).isoformat(),
                session_id=session_id,
            )
            self._save()
            logger.info(f"Stored summary for {session_key} ({len(summary)} chars)")

    async def clear(self, session_key: str):
        async with self._lock:
            self._data.pop(session_key, None)
            self._save()

    async def distill(
        self,
        session_key: str,
        history: list,
        provider,
        session_id: str = "",
    ) -> Optional[str]:
        """
        Distill recent conversation history into a summary using the provided LLM.

        Args:
            session_key: Key to store the result under.
            history: List of Message objects (role/content pairs).
            provider: A BaseProvider instance — typically nano-tier for cost.
            session_id: Optional session ID to tag the entry.

        Returns:
            The distilled summary text, or None if distillation failed.
        """
        if not history:
            return None

        from ..providers.base import Message

        # Build a compact transcript (last 30 turns to stay within tokens)
        recent = history[-30:]
        transcript_lines = []
        for msg in recent:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", "")[:500]
            transcript_lines.append(f"{role.upper()}: {content}")
        transcript = "\n\n".join(transcript_lines)

        prompt = f"{DISTILL_PROMPT}\n\n---\n\n{transcript}"

        try:
            response = await asyncio.wait_for(
                provider.send([Message(role="user", content=prompt)]),
                timeout=30.0,
            )
            summary = response.content.strip()
            if summary:
                await self.set(session_key, summary, session_id)
                return summary
        except asyncio.TimeoutError:
            logger.warning(f"Distillation timed out for {session_key}")
        except Exception as e:
            logger.error(f"Distillation failed for {session_key}: {e}")

        return None

    def inject_context(self, session_key: str) -> str:
        """
        Return the stored summary formatted for injection into a system prompt.
        Returns empty string if no summary exists.
        """
        summary = self.get(session_key)
        if not summary:
            return ""
        return f"## Previous Session Context\n\n{summary}\n\n---\n"
