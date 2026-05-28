"""
Inbound message debounce — prevents duplicate/rapid processing.

Tracks recent messages per (user, channel) and skips processing
if another message from the same user in the same channel arrived
within the debounce window.
"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DebounceEntry:
    """Track a recent message."""
    timestamp: float
    message_hash: Optional[str] = None


class InboundDebouncer:
    """
    Memory-based debouncer for adapter inbound events.

    Usage:
        debouncer = InboundDebouncer(window_ms=500)
        if debouncer.should_skip(user_id, channel_id, message):
            return  # Skip this message
        # Process message...
    """

    def __init__(self, window_ms: int = 500):
        """
        Args:
            window_ms: Debounce window in milliseconds. Rapid messages within
                      this window from the same (user, channel) are skipped.
        """
        self.window_ms = window_ms
        self.recent: dict[str, DebounceEntry] = {}

    def should_skip(
        self,
        user_id: str,
        channel_id: str,
        message: Optional[str] = None,
        skip_on_exact_duplicate: bool = True,
    ) -> bool:
        """
        Check if a message should be skipped due to debounce.

        Args:
            user_id: User who sent the message
            channel_id: Channel where message was posted
            message: Optional message content (for exact duplicate detection)
            skip_on_exact_duplicate: If True, skip exact content duplicates
                                     even if outside the window. Useful for
                                     accidental multi-paste within a session.

        Returns:
            True if this message should be skipped, False if it should process.
        """
        key = f"{user_id}:{channel_id}"
        now = time.time()
        elapsed_ms = 0

        if key in self.recent:
            entry = self.recent[key]
            elapsed_ms = (now - entry.timestamp) * 1000

            # Check exact duplicate within any timeframe
            if (
                skip_on_exact_duplicate
                and message
                and entry.message_hash == self._hash(message)
            ):
                return True

            # Check rapid succession within window
            if elapsed_ms < self.window_ms:
                return True

        # Record this message for future checks
        self.recent[key] = DebounceEntry(
            timestamp=now,
            message_hash=self._hash(message) if message else None,
        )

        # Cleanup old entries (optional, for memory efficiency)
        if len(self.recent) > 10000:
            self._cleanup()

        return False

    def _hash(self, message: str) -> str:
        """Quick hash for duplicate detection (first 32 chars + length)."""
        return f"{message[:32]}#{len(message)}"

    def _cleanup(self) -> None:
        """Remove entries older than 10x the debounce window."""
        now = time.time()
        cutoff = (now - (self.window_ms * 10 / 1000)) * 1000
        self.recent = {
            k: v
            for k, v in self.recent.items()
            if (now - v.timestamp) * 1000 < cutoff
        }

    def clear(self) -> None:
        """Clear all debounce history."""
        self.recent.clear()
