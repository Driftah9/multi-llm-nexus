"""Rolling conversation context window for non-Claude (stateless) providers.

Without this, follow-up questions like "can you elaborate?" arrive context-free
since stateless providers don't support session resume. This module maintains a
bounded recent Q→A history per session so continuity is preserved across turns.
"""
from collections import deque


class SessionWindow:
    """Rolling conversation window: recent Q→A pairs for context continuity."""

    def __init__(self, max_pairs: int = 3):
        self.max_pairs = max_pairs
        self.windows: dict = {}  # session_key -> deque[(question, answer)]

    def build_context(self, session_key: str) -> str:
        """Return a formatted context string of recent Q→A pairs."""
        if session_key not in self.windows:
            return ""
        window = self.windows[session_key]
        if not window:
            return ""
        parts = ["Recent context:"]
        for q, a in window:
            parts.append(f"  Q: {q}")
            parts.append(f"  A: {a}")
        return "\n".join(parts)

    def update(self, session_key: str, question: str, answer: str) -> None:
        """Add a Q→A pair to the window, dropping old pairs if at capacity."""
        if session_key not in self.windows:
            self.windows[session_key] = deque(maxlen=self.max_pairs)
        self.windows[session_key].append((question, answer))

    def clear(self, session_key: str) -> None:
        """Clear the window for a session (e.g., on session reset)."""
        if session_key in self.windows:
            del self.windows[session_key]

    def clear_all(self) -> None:
        """Clear all windows (e.g., on shutdown)."""
        self.windows.clear()
